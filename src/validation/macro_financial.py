"""Independent structural and arithmetic QA for the macro/rates additions.

Only families present in the fact table are checked.  This keeps older fixtures
valid while requiring a complete derived contract whenever a base series exists.
No source observations are filled or inferred by this validator.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.features.macro_financial import (
    GEOGRAPHIES, MACRO_SOURCE_IDS, MACRO_VARIABLE_IDS, MONTHLY_IDENTITY_COLUMNS,
    MONTHLY_VARIABLE_CONTRACTS, MONTHLY_VARIABLE_IDS,
)


CES_BASE_IDS = tuple(
    f"ces_{sector}_employment_nsa"
    for sector in (
        "construction", "information", "financial_activities",
        "professional_business_services", "leisure_hospitality",
    )
) + ("ces_average_hourly_earnings_total_private_nsa",)
GDP_BASE_ID = "bea_ca_real_gdp_quarterly"
CLAIMS_BASE_IDS = ("ca_initial_claims_weekly", "ca_continued_claims_weekly")
DAILY_BASE_IDS = (
    "us_treasury_2y_yield_daily", "us_treasury_10y_yield_daily",
    "us_baa_10y_spread_daily", "us_bank_prime_rate_daily", "us_sofr_daily",
)
FED_FUNDS_ID = "us_federal_funds_rate_monthly"
FAILURE_COLUMNS = (
    "observation_date", "geo_id", "variable_id", "reason", "actual", "expected",
)


def _contracts() -> dict[str, tuple[set[str], str]]:
    result: dict[str, tuple[set[str], str]] = {}
    for base in CES_BASE_IDS:
        for name in (base, f"{base}_yoy_pct"):
            result[name] = ({"STATE_06", "METRODIV_31084"}, "monthly")
    for name in (
        GDP_BASE_ID, "bea_ca_real_gdp_qoq_annualized_pct", "bea_ca_real_gdp_yoy_pct",
    ):
        result[name] = ({"STATE_06"}, "quarterly")
    for bases, native, geography in (
        (CLAIMS_BASE_IDS, "weekly", "STATE_06"),
        (DAILY_BASE_IDS, "daily", "US"),
    ):
        for base in bases:
            result[base] = ({geography}, native)
            stem = base.rsplit("_", 1)[0]
            result[f"{stem}_monthly_average"] = ({geography}, "monthly")
            result[f"{stem}_monthly_observation_count"] = ({geography}, "monthly")
    result[FED_FUNDS_ID] = ({"US"}, "monthly")
    return result


def macro_financial_failures(fact: pd.DataFrame) -> pd.DataFrame:
    """Return failures without mutating facts or relying on feature builders."""

    contracts = _contracts()
    if "variable_id" not in fact or fact.empty:
        return pd.DataFrame(columns=FAILURE_COLUMNS)
    work = fact.loc[fact["variable_id"].isin(contracts)].copy()
    if work.empty:
        return pd.DataFrame(columns=FAILURE_COLUMNS)
    failures: list[dict] = []

    def fail(reason, variable=None, geo=None, date=None, actual=None, expected=None):
        failures.append(dict(zip(FAILURE_COLUMNS, (
            date, geo, variable, reason, actual, expected,
        ))))

    required = {"observation_date", "geo_id", "value", "final_frequency"}
    missing = required.difference(work.columns)
    if missing:
        fail("missing_required_columns", actual="|".join(sorted(missing)))
        return pd.DataFrame(failures, columns=FAILURE_COLUMNS)
    work["_date"] = pd.to_datetime(work["observation_date"], errors="coerce", utc=True).dt.tz_localize(None)
    work["_numeric"] = pd.to_numeric(work["value"], errors="coerce")
    for _, row in work.iterrows():
        variable, geo, date = row["variable_id"], row["geo_id"], row["_date"]
        allowed, frequency = contracts[variable]
        if pd.isna(geo) or geo not in allowed:
            fail("invalid_geography", variable, geo, date, geo, "|".join(sorted(allowed)))
        if pd.isna(row["final_frequency"]) or row["final_frequency"] != frequency:
            fail("invalid_frequency", variable, geo, date, row["final_frequency"], frequency)
        if pd.isna(date):
            fail("invalid_observation_date", variable, geo, date)
        elif date != date.normalize():
            fail("observation_date_has_time", variable, geo, date)
        elif frequency == "monthly" and date != date.to_period("M").end_time.normalize():
            fail("monthly_date_not_month_end", variable, geo, date)
        elif frequency == "quarterly" and date != date.to_period("Q").end_time.normalize():
            fail("quarterly_date_not_quarter_end", variable, geo, date)
        elif frequency == "weekly" and date.weekday() != 5:
            fail("claims_date_not_saturday", variable, geo, date)
        if pd.notna(row["value"]) and pd.isna(row["_numeric"]):
            fail("nonnumeric_value", variable, geo, date, row["value"])
        elif pd.notna(row["_numeric"]) and not np.isfinite(row["_numeric"]):
            fail("nonfinite_value", variable, geo, date, row["_numeric"])
        elif variable in (*CES_BASE_IDS, GDP_BASE_ID) and pd.notna(row["_numeric"]) and row["_numeric"] <= 0:
            fail("nonpositive_economic_level", variable, geo, date, row["_numeric"])
        elif variable in CLAIMS_BASE_IDS and pd.notna(row["_numeric"]) and row["_numeric"] < 0:
            fail("negative_claims_count", variable, geo, date, row["_numeric"])

    keys = ["variable_id", "geo_id", "_date"]
    vintage_keys = keys + [name for name in ("source_vintage", "retrieved_at") if name in work]
    for _, row in work.loc[work.duplicated(vintage_keys, keep=False)].iterrows():
        fail("duplicate_observation_key", row["variable_id"], row["geo_id"], row["_date"])
    work = work.loc[work["_date"].notna() & work["geo_id"].notna()].copy()
    if work.empty:
        return pd.DataFrame(failures, columns=FAILURE_COLUMNS)
    for name in ("source_vintage", "retrieved_at"):
        work[f"_{name}"] = (
            pd.to_datetime(work[name], errors="coerce", utc=True)
            if name in work else pd.NaT
        )
    work = work.sort_values(["_source_vintage", "_retrieved_at"], na_position="first").drop_duplicates(keys, keep="last")

    def compare(output: str, geo: str, expected: dict[pd.Timestamp, float]):
        rows = work.loc[work["variable_id"].eq(output) & work["geo_id"].eq(geo)]
        actual = rows.set_index("_date")["_numeric"].to_dict()
        for date in sorted(set(expected).union(actual)):
            if date not in actual:
                fail("missing_derived_observation", output, geo, date, None, expected[date])
            elif date not in expected:
                fail("unexpected_derived_observation", output, geo, date, actual[date])
            else:
                observed, target = actual[date], expected[date]
                if pd.isna(observed) and pd.isna(target):
                    continue
                if pd.isna(observed) or pd.isna(target) or not np.isclose(observed, target, rtol=1e-9, atol=1e-8):
                    fail("formula_or_missingness_mismatch", output, geo, date, observed, target)

    def growth(base: str, specs: tuple[tuple[str, int, float], ...], period: str):
        family = {base, *(name for name, _, _ in specs)}
        for geo in work.loc[work["variable_id"].isin(family), "geo_id"].unique():
            rows = work.loc[work["variable_id"].eq(base) & work["geo_id"].eq(geo)]
            values = dict(zip(rows["_date"].dt.to_period(period), rows["_numeric"]))
            for output, lag, power in specs:
                expected = {}
                for _, row in rows.iterrows():
                    current = row["_numeric"]
                    previous = values.get(row["_date"].to_period(period) - lag, np.nan)
                    expected[row["_date"]] = (
                        100.0 * ((current / previous) ** power - 1.0)
                        if pd.notna(current) and pd.notna(previous) and current > 0 and previous > 0
                        else np.nan
                    )
                compare(output, geo, expected)

    for base in CES_BASE_IDS:
        growth(base, ((f"{base}_yoy_pct", 12, 1.0),), "M")
    growth(GDP_BASE_ID, (
        ("bea_ca_real_gdp_qoq_annualized_pct", 1, 4.0),
        ("bea_ca_real_gdp_yoy_pct", 4, 1.0),
    ), "Q")

    for base in (*CLAIMS_BASE_IDS, *DAILY_BASE_IDS):
        stem = base.rsplit("_", 1)[0]
        average_id, count_id = f"{stem}_monthly_average", f"{stem}_monthly_observation_count"
        family = {base, average_id, count_id}
        for geo in work.loc[work["variable_id"].isin(family), "geo_id"].unique():
            rows = work.loc[work["variable_id"].eq(base) & work["geo_id"].eq(geo)].copy()
            averages, counts = {}, {}
            if rows.empty:
                compare(average_id, geo, averages)
                compare(count_id, geo, counts)
                continue
            if rows["_retrieved_at"].isna().any():
                fail("missing_or_invalid_retrieval_timestamp", base, geo)
                continue
            cutoff = rows["_retrieved_at"].max().tz_convert("UTC").tz_localize(None).to_period("M")
            rows["_month"] = rows["_date"].dt.to_period("M")
            closed = rows.loc[rows["_month"].lt(cutoff)]
            monthly_groups = dict(tuple(closed.groupby("_month", sort=True)))
            if base in CLAIMS_BASE_IDS:
                # An entirely absent month is itself a coverage diagnostic.
                # Match the cleaner's null mean / zero valid-week count across
                # the observed native span, without fabricating weekly rows or
                # extending the span to the retrieval date.
                months = pd.period_range(
                    rows["_month"].min(),
                    min(rows["_month"].max(), cutoff - 1),
                    freq="M",
                )
            else:
                months = sorted(monthly_groups)
            for month in months:
                group = monthly_groups.get(month, closed.iloc[:0])
                date = month.end_time.normalize()
                valid = group.loc[group["_numeric"].notna()]
                counts[date] = float(len(valid))
                complete = True
                if base in CLAIMS_BASE_IDS:
                    saturdays = set(pd.date_range(month.start_time, date, freq="W-SAT"))
                    complete = set(valid["_date"]) == saturdays
                averages[date] = float(valid["_numeric"].mean()) if complete and len(valid) else np.nan
            compare(average_id, geo, averages)
            compare(count_id, geo, counts)
    return pd.DataFrame(failures, columns=FAILURE_COLUMNS)


def macro_monthly_panel_failures(
    fact: pd.DataFrame,
    panel: pd.DataFrame,
    *,
    require_complete: bool = False,
) -> pd.DataFrame:
    """Independently reconcile monthly delivery to facts, never using its builder.

    Sparse/empty fixtures are allowed by default. Production completeness means
    all 27 authorized fields and at least one nonnull value for each of the 39
    authorized geography-variable series, not filled historical observations.
    The separate macro fact QA still checks source-to-derived arithmetic.
    """
    failures: list[dict] = []

    def fail(reason, variable=None, geo=None, date=None, actual=None, expected=None):
        failures.append(dict(zip(FAILURE_COLUMNS, (
            date, geo, variable, reason, actual, expected,
        ))))

    identity = set(MONTHLY_IDENTITY_COLUMNS)
    required_fact = {"source_id", "variable_id", "geo_id", "observation_date", "final_frequency", "value"}
    relevant = pd.DataFrame(columns=sorted(required_fact))
    if not fact.empty:
        if not required_fact.issubset(fact.columns):
            fail("monthly_fact_missing_required_columns", actual="|".join(sorted(required_fact - set(fact.columns))))
        else:
            relevant = fact.loc[
                fact.source_id.isin(MACRO_SOURCE_IDS) | fact.variable_id.isin(MACRO_VARIABLE_IDS)
            ].copy()
    selected = relevant.loc[relevant.variable_id.isin(MONTHLY_VARIABLE_IDS)].reset_index(drop=True).copy()
    unauthorized = relevant.loc[
        relevant.final_frequency.eq("monthly") & ~relevant.variable_id.isin(MONTHLY_VARIABLE_IDS)
    ]
    for row in unauthorized.itertuples():
        fail("unauthorized_monthly_fact_variable", row.variable_id, row.geo_id, row.observation_date)
    selected["_date"] = pd.to_datetime(selected.observation_date, errors="coerce", utc=True).dt.tz_localize(None)
    selected["_numeric"] = pd.to_numeric(selected.value, errors="coerce")
    valid_fact = pd.Series(True, index=selected.index)
    for idx, row in selected.iterrows():
        variable, geo, date = row.variable_id, row.geo_id, row._date
        contract = MONTHLY_VARIABLE_CONTRACTS[variable]
        invalid = False
        if pd.isna(row.source_id) or row.source_id != contract.source_id:
            fail("monthly_fact_unauthorized_source", variable, geo, date, row.source_id, contract.source_id)
            invalid = True
        if pd.isna(geo) or geo not in contract.geo_ids:
            fail("monthly_fact_unauthorized_geography", variable, geo, date)
            invalid = True
        if pd.isna(row.final_frequency) or row.final_frequency != "monthly":
            fail("monthly_fact_invalid_frequency", variable, geo, date, row.final_frequency, "monthly")
            invalid = True
        if pd.isna(date) or date != date.normalize() or not date.is_month_end:
            fail("monthly_fact_invalid_month_end", variable, geo, date)
            invalid = True
        if pd.notna(row.value) and (pd.isna(row._numeric) or not np.isfinite(row._numeric)):
            fail("monthly_fact_invalid_numeric_value", variable, geo, date, row.value)
            invalid = True
        valid_fact.at[idx] = not invalid
    selected = selected.loc[valid_fact].copy()
    fact_keys = ["_date", "geo_id", "variable_id"]
    duplicates = selected.duplicated(fact_keys, keep=False)
    for _, row in selected.loc[duplicates].iterrows():
        fail("monthly_fact_duplicate_key", row.variable_id, row.geo_id, row._date)
    # Continue diagnosing the panel after a duplicate error; this is never a
    # permissive aggregation or an accepted correction to the fact table.
    selected = selected.drop_duplicates(fact_keys, keep="first")
    expected = selected.pivot(index=["_date", "geo_id"], columns="variable_id", values="_numeric")
    expected_columns = set(MONTHLY_VARIABLE_IDS if require_complete else expected.columns)

    if panel.columns.duplicated().any():
        fail("monthly_panel_duplicate_columns", actual="|".join(map(str, panel.columns[panel.columns.duplicated()])))
        return pd.DataFrame(failures, columns=FAILURE_COLUMNS)
    missing_identity = identity - set(panel.columns)
    if missing_identity:
        fail("monthly_panel_missing_identity_columns", actual="|".join(sorted(missing_identity)))
    actual_columns = set(panel.columns) - identity
    for variable in sorted(actual_columns - expected_columns):
        fail("monthly_panel_unexpected_column", variable)
    for variable in sorted(expected_columns - actual_columns):
        fail("monthly_panel_missing_column", variable)
    if not {"date", "geo_id"}.issubset(panel.columns):
        return pd.DataFrame(failures, columns=FAILURE_COLUMNS)

    actual = panel.reset_index(drop=True).copy()
    actual["_date"] = pd.to_datetime(actual.date, errors="coerce", utc=True).dt.tz_localize(None)
    valid_panel = pd.Series(True, index=actual.index)
    for idx, row in actual.iterrows():
        geo, date = row.geo_id, row._date
        if pd.isna(date) or date != date.normalize() or not date.is_month_end:
            fail("monthly_panel_invalid_month_end", geo=geo, date=date)
            valid_panel.at[idx] = False
        if pd.isna(geo) or geo not in GEOGRAPHIES:
            fail("monthly_panel_unauthorized_geography", geo=geo, date=date)
            valid_panel.at[idx] = False
            continue
        for field, target in zip(("geo_name", "geography_type", "state_fips"), GEOGRAPHIES[geo]):
            if field not in actual:
                continue
            observed = row[field]
            equal = pd.isna(observed) if target is None else pd.notna(observed) and observed == target
            if not equal:
                fail("monthly_panel_geography_metadata_mismatch", field, geo, date, observed, target)
    keys = ["_date", "geo_id"]
    duplicates = actual.duplicated(keys, keep=False)
    for _, row in actual.loc[duplicates].iterrows():
        fail("monthly_panel_duplicate_key", geo=row.geo_id, date=row._date)
    actual = actual.loc[valid_panel].drop_duplicates(keys, keep="first").set_index(keys)
    expected_keys, actual_keys = set(expected.index), set(actual.index)
    for date, geo in sorted(expected_keys - actual_keys):
        fail("monthly_panel_missing_row", geo=geo, date=date)
    for date, geo in sorted(actual_keys - expected_keys):
        fail("monthly_panel_unexpected_row", geo=geo, date=date)

    for variable in sorted(actual_columns & MONTHLY_VARIABLE_IDS):
        contract = MONTHLY_VARIABLE_CONTRACTS[variable]
        numeric = pd.to_numeric(actual[variable], errors="coerce")
        bad_numeric = actual[variable].notna() & (numeric.isna() | ~np.isfinite(numeric))
        for date, geo in actual.index[bad_numeric]:
            fail("monthly_panel_invalid_numeric_value", variable, geo, date, actual.loc[(date, geo), variable])
        for date, geo in actual.index[actual[variable].notna()]:
            if geo not in contract.geo_ids:
                fail("monthly_panel_unauthorized_value_geography", variable, geo, date)
        if variable not in expected.columns:
            continue
        for date, geo in sorted(expected_keys & actual_keys):
            observed = numeric.loc[(date, geo)]
            target = expected.loc[(date, geo), variable]
            if pd.isna(observed) and pd.isna(target):
                continue
            if pd.isna(observed) or pd.isna(target) or not np.isclose(observed, target, rtol=1e-12, atol=1e-12):
                fail("monthly_panel_value_or_null_mask_mismatch", variable, geo, date, observed, target)

    if require_complete:
        for variable, contract in MONTHLY_VARIABLE_CONTRACTS.items():
            for geo in sorted(contract.geo_ids):
                fact_series = selected.loc[selected.variable_id.eq(variable) & selected.geo_id.eq(geo), "_numeric"]
                if not fact_series.notna().any():
                    fail("monthly_contract_missing_nonnull_fact_series", variable, geo)
                present = variable in actual.columns and (
                    actual.loc[actual.index.get_level_values("geo_id") == geo, variable].notna().any()
                )
                if not present:
                    fail("monthly_contract_missing_nonnull_panel_series", variable, geo)
    return pd.DataFrame(failures, columns=FAILURE_COLUMNS)
