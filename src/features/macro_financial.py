"""Sparse, geography-faithful monthly macro control panel (no broadcasting)."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


MACRO_SOURCE_IDS = frozenset({
    "bls_ces_ca_la_industry_earnings", "dol_ca_ui_claims", "bea_ca_real_gdp",
    "fred_fedfunds", "fred_dgs2", "fred_dgs10", "fred_baa10y", "fred_dprime", "fred_sofr",
})
GEOGRAPHIES = {
    "US": ("United States", "country", None),
    "STATE_06": ("California", "state", "06"),
    "METRODIV_31084": ("Los Angeles-Long Beach-Glendale, CA Metropolitan Division", "metropolitan_division", "06"),
}
MONTHLY_IDENTITY_COLUMNS = ("date", "geo_id", "geo_name", "geography_type", "state_fips")


@dataclass(frozen=True)
class MonthlyVariableContract:
    source_id: str
    geo_ids: frozenset[str]
    role: str


_CES_MONTHLY_IDS = (
    "ces_construction_employment_nsa", "ces_construction_employment_nsa_yoy_pct",
    "ces_information_employment_nsa", "ces_information_employment_nsa_yoy_pct",
    "ces_financial_activities_employment_nsa", "ces_financial_activities_employment_nsa_yoy_pct",
    "ces_professional_business_services_employment_nsa",
    "ces_professional_business_services_employment_nsa_yoy_pct",
    "ces_leisure_hospitality_employment_nsa", "ces_leisure_hospitality_employment_nsa_yoy_pct",
    "ces_average_hourly_earnings_total_private_nsa",
    "ces_average_hourly_earnings_total_private_nsa_yoy_pct",
)
_RATE_MONTHLY_STEMS = {
    "us_treasury_2y_yield": "fred_dgs2",
    "us_treasury_10y_yield": "fred_dgs10",
    "us_baa_10y_spread": "fred_baa10y",
    "us_bank_prime_rate": "fred_dprime",
    "us_sofr": "fred_sofr",
}

# The authorized output contract is explicit: 20 substantive fields and seven
# coverage diagnostics. Native audit observations and quarterly GDP are not
# monthly controls, even if a corrupt input labels their frequency "monthly".
MONTHLY_VARIABLE_CONTRACTS: dict[str, MonthlyVariableContract] = {
    variable: MonthlyVariableContract(
        "bls_ces_ca_la_industry_earnings",
        frozenset({"STATE_06", "METRODIV_31084"}), "monthly_substantive",
    )
    for variable in _CES_MONTHLY_IDS
}
MONTHLY_VARIABLE_CONTRACTS["us_federal_funds_rate_monthly"] = MonthlyVariableContract(
    "fred_fedfunds", frozenset({"US"}), "monthly_substantive",
)
for _stem, _source, _geo in (
    ("ca_initial_claims", "dol_ca_ui_claims", "STATE_06"),
    ("ca_continued_claims", "dol_ca_ui_claims", "STATE_06"),
    *((stem, source, "US") for stem, source in _RATE_MONTHLY_STEMS.items()),
):
    for _suffix, _role in (
        ("monthly_average", "monthly_substantive"),
        ("monthly_observation_count", "monthly_coverage_diagnostic"),
    ):
        MONTHLY_VARIABLE_CONTRACTS[f"{_stem}_{_suffix}"] = MonthlyVariableContract(
            _source, frozenset({_geo}), _role,
        )

MONTHLY_VARIABLE_IDS = frozenset(MONTHLY_VARIABLE_CONTRACTS)
MACRO_NATIVE_DAILY_IDS = frozenset(f"{stem}_daily" for stem in _RATE_MONTHLY_STEMS)
MACRO_NATIVE_WEEKLY_IDS = frozenset({"ca_initial_claims_weekly", "ca_continued_claims_weekly"})
MACRO_QUARTERLY_GDP_IDS = frozenset({
    "bea_ca_real_gdp_quarterly", "bea_ca_real_gdp_qoq_annualized_pct", "bea_ca_real_gdp_yoy_pct",
})
MACRO_VARIABLE_IDS = (
    MONTHLY_VARIABLE_IDS | MACRO_NATIVE_DAILY_IDS | MACRO_NATIVE_WEEKLY_IDS | MACRO_QUARTERLY_GDP_IDS
)


def macro_delivery_role(variable_id: str) -> str:
    """Separate monthly controls, native audit rows and required quarterly GDP."""
    if variable_id in MONTHLY_VARIABLE_CONTRACTS:
        return MONTHLY_VARIABLE_CONTRACTS[variable_id].role
    if variable_id in MACRO_NATIVE_DAILY_IDS:
        return "native_daily_audit"
    if variable_id in MACRO_NATIVE_WEEKLY_IDS:
        return "native_weekly_audit"
    if variable_id in MACRO_QUARTERLY_GDP_IDS:
        return "quarterly_gdp_required"
    return "not_in_macro_request"


def macro_monthly_panel(fact: pd.DataFrame) -> pd.DataFrame:
    if fact.empty:
        return pd.DataFrame(columns=MONTHLY_IDENTITY_COLUMNS)
    required = {"source_id", "variable_id", "geo_id", "observation_date", "final_frequency", "value"}
    if not required.issubset(fact.columns):
        raise ValueError(f"Macro panel missing required fact columns: {sorted(required - set(fact.columns))}")
    relevant = fact.loc[
        fact.source_id.isin(MACRO_SOURCE_IDS) | fact.variable_id.isin(MACRO_VARIABLE_IDS)
    ]
    monthly = relevant.final_frequency.eq("monthly")
    unexpected = set(relevant.loc[monthly, "variable_id"]) - MONTHLY_VARIABLE_IDS
    if unexpected:
        raise ValueError(f"Unapproved monthly macro variable: {sorted(map(str, unexpected))}")
    selected = relevant.loc[relevant.variable_id.isin(MONTHLY_VARIABLE_IDS)].copy()
    if not selected.final_frequency.eq("monthly").all():
        raise ValueError("Authorized monthly macro variable has nonmonthly frequency")
    keys = ["geo_id", "observation_date", "variable_id"]
    if selected.empty:
        return pd.DataFrame(columns=MONTHLY_IDENTITY_COLUMNS)
    unknown = set(selected.geo_id) - set(GEOGRAPHIES)
    if unknown:
        raise ValueError(f"Unapproved macro geography: {sorted(map(str, unknown))}")
    for variable, group in selected.groupby("variable_id"):
        contract = MONTHLY_VARIABLE_CONTRACTS[variable]
        if not group.source_id.eq(contract.source_id).all():
            raise ValueError(f"Unapproved macro source for {variable}: expected {contract.source_id}")
        if not group.geo_id.isin(contract.geo_ids).all():
            raise ValueError(f"Unapproved macro geography for {variable}: {sorted(contract.geo_ids)}")
    dates = pd.to_datetime(selected.observation_date, errors="coerce", utc=True).dt.tz_localize(None)
    if dates.isna().any() or not (dates.dt.is_month_end & dates.eq(dates.dt.normalize())).all():
        raise ValueError("Monthly macro observation dates must be valid month ends without a time component")
    selected["observation_date"] = dates
    if selected.duplicated(keys).any():
        raise ValueError("Macro panel requires one selected observation per geography/date/variable")
    # pivot (not pivot_table) retains explicitly null data; no forward-fill and
    # no copying US financial rates into California or local observations.
    panel = selected.pivot(index=["observation_date", "geo_id"], columns="variable_id", values="value").reset_index()
    panel.columns.name = None
    panel = panel.rename(columns={"observation_date": "date"})
    panel["geo_name"] = panel.geo_id.map(lambda x: GEOGRAPHIES[x][0])
    panel["geography_type"] = panel.geo_id.map(lambda x: GEOGRAPHIES[x][1])
    panel["state_fips"] = panel.geo_id.map(lambda x: GEOGRAPHIES[x][2]).astype("string")
    identity = list(MONTHLY_IDENTITY_COLUMNS)
    return panel[identity + sorted(set(panel.columns) - set(identity))].sort_values(["geo_id", "date"]).reset_index(drop=True)
