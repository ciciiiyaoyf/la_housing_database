"""Assemble the native-frequency Version A housing-finance benchmark table."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.benchmark.cpi import (
    CPI_U_BASE_YEAR,
    CPI_U_SOURCE_ID,
    cpi_u_annual_index,
)
from src.benchmark.acs import CALIFORNIA_COUNTY_FIPS
from src.benchmark.finance import (
    calculate_aggregate_mortgage_payment,
    calculate_debt_normalizations,
    calibrate_implied_debt_to_california,
    implied_debt_from_distribution,
    implied_debt_scenarios,
)
from src.benchmark.nmdb import (
    FHFA_NMDB_CA_UPB_SOURCE_ID,
    FHFA_NMDB_JOINT_DISTRIBUTION_SOURCE_ID,
)
from src.benchmark.schema import (
    BENCHMARK_COLUMNS,
    benchmark_row,
    finalize_benchmark,
    normalize_benchmark,
)


ACS_SELECTED_COSTS = (
    "acs_aggregate_selected_monthly_owner_costs_mortgaged_benchmark"
)
ACS_MORTGAGED_UNITS = "acs_mortgaged_owner_units_benchmark"
ACS_MORTGAGED_INCOME = (
    "acs_aggregate_household_income_mortgaged_owner_benchmark"
)
ACS_MORTGAGED_VALUE = "acs_aggregate_owner_value_mortgaged_benchmark"
ACS_NO_MORTGAGE_VALUE = (
    "acs_aggregate_owner_value_no_mortgage_benchmark"
)
ACS_OWNER_UNITS = "acs_owner_occupied_units_benchmark"
ACS_TOTAL_POPULATION = "acs_total_population_benchmark"
ACS_OWNER_INCOME = "acs_aggregate_household_income_owner_benchmark"

PAYMENT_OUTPUTS: tuple[tuple[str, str, str], ...] = (
    (
        "aggregate_monthly_pi",
        "acs_estimated_aggregate_monthly_first_mortgage_pi_benchmark",
        "USD/month",
    ),
    (
        "pi_per_mortgaged_unit",
        "acs_first_mortgage_pi_per_mortgaged_unit_benchmark",
        "USD/unit/month",
    ),
    (
        "annual_debt_service",
        "acs_annual_first_mortgage_debt_service_benchmark",
        "USD/year",
    ),
    (
        "mortgage_debt_service_to_income",
        "acs_mortgage_debt_service_to_income_benchmark",
        "ratio",
    ),
)

EQUITY_OUTPUTS: tuple[tuple[str, str], ...] = (
    (
        "equity_mortgaged",
        "acs_implied_owner_occupied_home_equity_mortgaged_benchmark",
    ),
    (
        "equity_all",
        "acs_implied_owner_occupied_home_equity_all_benchmark",
    ),
    (
        "aggregate_ltv",
        "acs_aggregate_ltv_mortgaged_benchmark",
    ),
    (
        "equity_rate",
        "acs_equity_rate_mortgaged_benchmark",
    ),
    (
        "equity_per_owner_unit",
        "acs_implied_owner_occupied_home_equity_per_owner_unit_benchmark",
    ),
)

DEBT_NORMALIZATION_OUTPUTS: tuple[tuple[str, str, str], ...] = (
    (
        "debt_per_mortgaged_unit",
        "acs_first_mortgage_debt_per_mortgaged_owner_unit_benchmark",
        "USD/unit",
    ),
    (
        "debt_per_owner_unit",
        "acs_first_mortgage_debt_per_owner_unit_benchmark",
        "USD/unit",
    ),
    (
        "debt_per_capita",
        "acs_first_mortgage_debt_per_capita_benchmark",
        "USD/person",
    ),
    (
        "debt_to_owner_income",
        "acs_first_mortgage_debt_to_owner_income_benchmark",
        "ratio",
    ),
)

REAL_EQUITY_OUTPUTS: tuple[tuple[str, str], ...] = (
    (
        "equity_mortgaged",
        "acs_implied_owner_occupied_home_equity_mortgaged_real_2024_benchmark",
    ),
    (
        "equity_all",
        "acs_implied_owner_occupied_home_equity_all_real_2024_benchmark",
    ),
)

PMMS_TERM_SCENARIO_OUTPUTS: tuple[tuple[int, str, str, str], ...] = (
    (
        20,
        "acs_implied_first_mortgage_balance_raw_pmms_20y_sensitivity_benchmark",
        "acs_implied_owner_occupied_home_equity_mortgaged_pmms_20y_scenario_benchmark",
        "acs_implied_owner_occupied_home_equity_all_pmms_20y_scenario_benchmark",
    ),
    (
        25,
        "acs_implied_first_mortgage_balance_raw_pmms_25y_sensitivity_benchmark",
        "acs_implied_owner_occupied_home_equity_mortgaged_pmms_25y_scenario_benchmark",
        "acs_implied_owner_occupied_home_equity_all_pmms_25y_scenario_benchmark",
    ),
    (
        30,
        "acs_implied_first_mortgage_balance_raw_pmms_30y_sensitivity_benchmark",
        "acs_implied_owner_occupied_home_equity_mortgaged_pmms_30y_scenario_benchmark",
        "acs_implied_owner_occupied_home_equity_all_pmms_30y_scenario_benchmark",
    ),
)


def _as_timestamp(value: Any) -> pd.Timestamp:
    parsed = pd.to_datetime(value, errors="coerce")
    return pd.NaT if pd.isna(parsed) else pd.Timestamp(parsed)


def _number(value: Any) -> float:
    parsed = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return math.nan if pd.isna(parsed) else float(parsed)


def _finite(value: Any) -> bool:
    number = _number(value)
    return not math.isnan(number) and math.isfinite(number)


def _lineage(*values: Any) -> str:
    payload = "|".join("" if pd.isna(value) else str(value) for value in values)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _pipe(values: list[Any]) -> str | None:
    tokens = [str(value) for value in values if not pd.isna(value) and str(value)]
    return "|".join(dict.fromkeys(tokens)) if tokens else None


def _merge_pipe_values(*values: Any) -> str | None:
    tokens: list[str] = []
    for value in values:
        if value is None or pd.isna(value):
            continue
        tokens.extend(
            token.strip()
            for token in str(value).split("|")
            if token.strip()
        )
    return "|".join(dict.fromkeys(tokens)) if tokens else None


def _source_contract(
    frame: pd.DataFrame | None,
    *,
    default_source_id: str,
    default_transformation_id: str,
) -> pd.DataFrame:
    """Map a source-specific benchmark cleaner into the final contract."""
    if frame is None or frame.empty:
        return pd.DataFrame(columns=BENCHMARK_COLUMNS)
    result = frame.copy()
    defaults: dict[str, Any] = {
        "state_fips": "06",
        "geo_id": None,
        "variable_id": None,
        "estimate_variant": "published",
        "reported_value": None,
        "database_version": "A. Benchmark",
        "source_id": default_source_id,
        "monthly_method": "not_applicable_source_period",
        "construction_method": "direct_published_source",
        "payment_frequency": None,
        "is_modeled": False,
        "is_nowcast": False,
        "is_imputed": False,
        "is_interpolated": False,
        "is_suppressed": False,
        "transformation_id": default_transformation_id,
    }
    for column, value in defaults.items():
        if column not in result:
            result[column] = value
        else:
            result[column] = result[column].fillna(value)

    # PUMS county/region estimates depend on a versioned tract-to-PUMA
    # relationship. Keep that crosswalk as a first-class source in the final
    # compact contract rather than dropping its source-specific columns.
    relationship_fields = {
        "relationship_vintage",
        "relationship_source_filename",
        "relationship_retrieved_at",
        "relationship_checksum_sha256",
    }
    if relationship_fields.issubset(result.columns):
        relationship_rows = result["relationship_vintage"].notna()
        for index, row in result.loc[relationship_rows].iterrows():
            relationship_vintage = _number(row["relationship_vintage"])
            explicit_relationship_source = row.get(
                "relationship_source_id"
            )
            relationship_source_id = (
                str(explicit_relationship_source)
                if not pd.isna(explicit_relationship_source)
                else (
                    f"census_tract_to_puma_{int(relationship_vintage)}"
                    if _finite(relationship_vintage)
                    else "census_tract_to_puma_relationship"
                )
            )
            original_source_id = row["source_id"]
            result.at[index, "source_id"] = _merge_pipe_values(
                original_source_id,
                relationship_source_id,
            )
            result.at[index, "source_filename"] = _merge_pipe_values(
                row.get("source_filename"),
                row.get("relationship_source_filename"),
            )
            result.at[index, "source_vintage"] = _merge_pipe_values(
                row.get("source_vintage"),
                (
                    int(relationship_vintage)
                    if _finite(relationship_vintage)
                    else None
                ),
            )
            result.at[index, "checksum_sha256"] = _merge_pipe_values(
                row.get("checksum_sha256"),
                row.get("relationship_checksum_sha256"),
            )
            retrievals = pd.to_datetime(
                pd.Series(
                    [
                        row.get("retrieved_at"),
                        row.get("relationship_retrieved_at"),
                    ]
                ),
                errors="coerce",
                utc=True,
            ).dropna()
            if not retrievals.empty:
                result.at[index, "retrieved_at"] = retrievals.max().isoformat()

            release_audit = _release_date_mapping(
                row.get("source_release_dates")
            )
            if (
                str(original_source_id) not in release_audit
                and not pd.isna(row.get("release_date"))
            ):
                release_audit[str(original_source_id)] = pd.Timestamp(
                    row["release_date"]
                ).date().isoformat()
            relationship_release = row.get(
                "relationship_release_date",
                "unavailable",
            )
            release_audit[relationship_source_id] = (
                "unavailable"
                if pd.isna(relationship_release)
                else (
                    _normalise_release_audit_value(relationship_release)
                    or "unavailable"
                )
            )
            result.at[index, "source_release_dates"] = "|".join(
                f"{source_id}={release_audit[source_id]}"
                for source_id in dict.fromkeys(
                    str(result.at[index, "source_id"]).split("|")
                )
            )

    result["county_fips"] = result["county_fips"].astype("string").str.zfill(5)
    result["geo_id"] = result["geo_id"].fillna(
        "COUNTY_" + result["county_fips"]
    )
    result["variable_id"] = result["variable_id"].fillna(
        result["variable_name"]
    )
    result["reported_value"] = result["reported_value"].fillna(result["value"])
    if "source_release_dates" not in result:
        result["source_release_dates"] = pd.NA
    released = result["release_date"].notna()
    missing_release_audit = result["source_release_dates"].isna()
    result.loc[released & missing_release_audit, "source_release_dates"] = [
        f"{source_id}={pd.Timestamp(release_date).date().isoformat()}"
        for source_id, release_date in zip(
            result.loc[released & missing_release_audit, "source_id"],
            result.loc[released & missing_release_audit, "release_date"],
            strict=True,
        )
    ]
    if "meets_minimum_sample" in result:
        suppressed = ~result["meets_minimum_sample"].fillna(True).astype(bool)
        result.loc[suppressed, "is_suppressed"] = True
        result.loc[
            suppressed & result.get(
                "quality_flag", pd.Series(index=result.index, dtype="object")
            ).isna(),
            "quality_flag",
        ] = "insufficient_sample"
    missing_without_flag = result["value"].isna() & result.get(
        "quality_flag", pd.Series(index=result.index, dtype="object")
    ).isna()
    result.loc[missing_without_flag, "quality_flag"] = "source_value_unavailable"
    calculated_lineage = pd.Series([
        _lineage(
            row.get("source_id"),
            row.get("checksum_sha256"),
            row.get("county_fips"),
            row.get("reference_date"),
            row.get("variable_name"),
        )
        for _, row in result.iterrows()
    ], index=result.index)
    if "lineage_id" not in result:
        result["lineage_id"] = calculated_lineage
    else:
        result["lineage_id"] = result["lineage_id"].fillna(
            calculated_lineage
        )
    return normalize_benchmark(result)


def pmms_reference_rate_parameters(
    pmms_rows: pd.DataFrame,
    acs_rows: pd.DataFrame,
    *,
    variable_id: str = "mortgage_rate_30y_monthly_median",
) -> pd.DataFrame:
    """Select the exact PMMS calendar month containing each ACS midpoint.

    The returned rate is a decimal annual rate.  No nearest-neighbor, carry
    forward, or interpolation is allowed; a missing reference month remains
    absent so the debt benchmark can publish an explicit missing row.
    """
    required_pmms = {"observation_date", "variable_id", "value"}
    missing = required_pmms.difference(pmms_rows.columns)
    if missing:
        raise KeyError(f"missing PMMS columns: {sorted(missing)}")
    required_acs = {"benchmark_vintage", "reference_date"}
    missing = required_acs.difference(acs_rows.columns)
    if missing:
        raise KeyError(f"missing ACS columns: {sorted(missing)}")

    pmms = pmms_rows.loc[pmms_rows["variable_id"].eq(variable_id)].copy()
    pmms["_period"] = pd.to_datetime(
        pmms["observation_date"], errors="coerce"
    ).dt.to_period("M")
    pmms["_rate_percent"] = pd.to_numeric(pmms["value"], errors="coerce")
    duplicate = pmms.duplicated("_period", keep=False)
    if duplicate.any():
        raise ValueError(
            f"PMMS has duplicate {variable_id} observations by calendar month"
        )
    pmms = pmms.set_index("_period")

    vintages = acs_rows[
        ["benchmark_vintage", "reference_date"]
    ].drop_duplicates()
    rows: list[dict[str, Any]] = []
    for _, vintage in vintages.iterrows():
        reference = _as_timestamp(vintage["reference_date"])
        if pd.isna(reference):
            continue
        period = reference.to_period("M")
        if period not in pmms.index:
            continue
        source = pmms.loc[period]
        rate_percent = _number(source["_rate_percent"])
        if math.isnan(rate_percent):
            continue
        if rate_percent < 0:
            raise ValueError("PMMS rates cannot be negative")
        rows.append(
            {
                "benchmark_vintage": vintage["benchmark_vintage"],
                "reference_date": reference,
                "value": rate_percent / 100.0,
                "unit": "decimal_annual_rate",
                "variable_id": variable_id,
                "parameter_geography": "US",
                "source_id": source.get("source_id", "freddie_mac_pmms"),
                "source_vintage": source.get("source_vintage"),
                "retrieved_at": source.get("retrieved_at"),
                "source_filename": source.get("source_filename"),
                "checksum_sha256": source.get("checksum_sha256"),
                # The consolidated PMMS history does not encode a distinct
                # provider release date for each historical monthly median.
                "release_date": "unavailable",
                "quality_flag": "pmms_origination_rate_sensitivity",
            }
        )
    return pd.DataFrame(rows)


def _indexed_acs(group: pd.DataFrame) -> dict[str, pd.Series]:
    duplicate = group.duplicated("variable_name", keep=False)
    if duplicate.any():
        names = sorted(group.loc[duplicate, "variable_name"].astype(str).unique())
        raise ValueError(f"duplicate ACS variables within county-vintage: {names}")
    return {
        str(row["variable_name"]): row
        for _, row in group.iterrows()
    }


def _estimate(
    values: Mapping[str, pd.Series],
    variable: str,
    field: str = "value",
) -> float:
    row = values.get(variable)
    return math.nan if row is None else _number(row.get(field))


def _select_pums_parameter(
    pums: pd.DataFrame,
    *,
    county_fips: str,
    benchmark_vintage: str,
    reference_date: pd.Timestamp,
) -> pd.Series | None:
    if pums.empty:
        return None
    candidates = pums.copy()
    if "variable_name" in candidates:
        candidates = candidates.loc[
            candidates["variable_name"].astype("string").eq(
                "pums_first_mortgage_pi_share_benchmark"
            )
        ]
        if candidates.empty:
            return None
    if "reference_date" not in candidates:
        return None
    dates = pd.to_datetime(
        candidates["reference_date"], errors="coerce"
    ).dt.normalize()
    candidates = candidates.loc[
        candidates["benchmark_vintage"].astype(str).eq(
            str(benchmark_vintage)
        )
        & dates.eq(reference_date.normalize())
    ]
    if candidates.empty:
        return None

    county = candidates.loc[
        candidates["county_fips"].astype("string").eq(county_fips)
    ]
    if not county.empty:
        if len(county) > 1:
            raise ValueError(
                "multiple PUMS P&I parameters match "
                f"{county_fips} / {benchmark_vintage}"
            )
        row = county.iloc[-1]
        geography_tier = str(row.get("geography_tier", ""))
        hierarchy_selected = geography_tier in {
            "county",
            "regional",
            "state_fallback",
        }
        legacy_county_selected = bool(
            row.get("county_all_pumas_wholly_contained", False)
        )
        if (
            (hierarchy_selected or legacy_county_selected)
            and _finite(row.get("value"))
        ):
            return row
    state = candidates.loc[
        candidates.get(
            "parameter_geography_level",
            pd.Series(index=candidates.index, dtype="object"),
        ).eq("state")
    ]
    state = state.loc[state["county_fips"].isna()]
    state = state.loc[state["state_fips"].astype("string").eq("06")]
    if len(state) > 1:
        raise ValueError(
            f"multiple California PUMS P&I parameters match {benchmark_vintage}"
        )
    return (
        state.iloc[-1]
        if not state.empty and _finite(state.iloc[-1].get("value"))
        else None
    )


def _release_date_mapping(value: Any) -> dict[str, Any]:
    """Parse the pipe-delimited source release audit field."""
    if value is None or pd.isna(value):
        return {}
    result: dict[str, Any] = {}
    for token in str(value).split("|"):
        source_id, separator, release_date = token.partition("=")
        if not separator or not source_id:
            continue
        if release_date.strip().casefold() == "unavailable":
            result[source_id] = "unavailable"
        elif not pd.isna(pd.to_datetime(release_date, errors="coerce")):
            result[source_id] = (
                pd.Timestamp(release_date).date().isoformat()
            )
    return result


def _normalise_release_audit_value(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    if str(value).strip().casefold() == "unavailable":
        return "unavailable"
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return pd.Timestamp(parsed).date().isoformat()


_NMDB_PROVENANCE_FIELDS = {
    "benchmark_vintage",
    "reference_date",
    "source_period_start",
    "source_period_end",
    "release_date",
    "source_release_dates",
    "source_frequency",
    "parameter_geography",
    "source_id",
    "source_vintage",
    "retrieved_at",
    "source_filename",
    "checksum_sha256",
    "source_page_url",
    "transformation_id",
    "universe_definition",
}


def _validate_nmdb_provenance(
    frame: pd.DataFrame,
    *,
    expected_source_id: str,
    semantic_fields: set[str],
    input_name: str,
) -> None:
    required = _NMDB_PROVENANCE_FIELDS | semantic_fields
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(
            f"{input_name} must be a normalized provenance-rich NMDB "
            f"DataFrame; missing columns: {missing}"
        )
    if not frame["source_id"].astype("string").eq(expected_source_id).all():
        observed = sorted(
            frame["source_id"].astype("string").dropna().unique().tolist()
        )
        raise ValueError(
            f"{input_name} source_id must be {expected_source_id}; "
            f"observed: {observed}"
        )
    for field in (
        "benchmark_vintage",
        "source_vintage",
        "source_filename",
        "checksum_sha256",
        "source_page_url",
        "transformation_id",
        "universe_definition",
        "source_release_dates",
    ):
        values = frame[field].astype("string").str.strip()
        if values.isna().any() or values.eq("").any():
            raise ValueError(
                f"{input_name} requires populated provenance field {field}"
            )
    if not frame["checksum_sha256"].astype("string").str.fullmatch(
        r"[0-9a-fA-F]{64}", na=False
    ).all():
        raise ValueError(
            f"{input_name} checksum_sha256 must contain 64 hexadecimal characters"
        )
    for field in (
        "reference_date",
        "source_period_start",
        "source_period_end",
        "release_date",
        "retrieved_at",
    ):
        if pd.to_datetime(frame[field], errors="coerce", utc=True).isna().any():
            raise ValueError(
                f"{input_name} requires valid provenance field {field}"
            )


def _validate_nmdb_inputs(
    nmdb_distributions: pd.DataFrame | None,
    california_upb: pd.DataFrame | None,
) -> None:
    if nmdb_distributions is not None and not isinstance(
        nmdb_distributions, pd.DataFrame
    ):
        raise TypeError(
            "nmdb_distributions must be a normalized provenance-rich DataFrame"
        )
    if california_upb is not None and not isinstance(
        california_upb, pd.DataFrame
    ):
        raise TypeError(
            "california_upb must be a normalized provenance-rich DataFrame; "
            "bare benchmark-to-value mappings are not accepted"
        )
    if nmdb_distributions is not None and not nmdb_distributions.empty:
        _validate_nmdb_provenance(
            nmdb_distributions,
            expected_source_id=FHFA_NMDB_JOINT_DISTRIBUTION_SOURCE_ID,
            semantic_fields={
                "annual_rate",
                "remaining_months",
                "weight",
                "distribution_kind",
                "mortgage_population",
                "joint_distribution",
            },
            input_name="nmdb_distributions",
        )
        if not nmdb_distributions["joint_distribution"].eq(True).all():
            raise ValueError(
                "nmdb_distributions must explicitly attest a joint distribution"
            )
    if california_upb is not None and not california_upb.empty:
        _validate_nmdb_provenance(
            california_upb,
            expected_source_id=FHFA_NMDB_CA_UPB_SOURCE_ID,
            semantic_fields={
                "value",
                "variable_name",
                "unit",
                "control_type",
                "compatible_with_county_debt",
            },
            input_name="california_upb",
        )
        if not california_upb["compatible_with_county_debt"].eq(True).all():
            raise ValueError(
                "california_upb requires explicit compatible_with_county_debt "
                "evidence"
            )
        if not california_upb["parameter_geography"].astype(
            "string"
        ).eq("California").all():
            raise ValueError(
                "california_upb parameter_geography must be California"
            )
        if not california_upb["control_type"].astype("string").eq(
            "unpaid_principal_balance"
        ).all():
            raise ValueError(
                "california_upb control_type must be unpaid_principal_balance"
            )


def _lookup_rate(
    rates: pd.DataFrame,
    benchmark_vintage: str,
    reference_date: pd.Timestamp,
) -> pd.Series | None:
    if rates.empty:
        return None
    if "reference_date" not in rates:
        return None
    dates = pd.to_datetime(
        rates["reference_date"], errors="coerce"
    ).dt.normalize()
    candidates = rates.loc[
        rates["benchmark_vintage"].astype(str).eq(str(benchmark_vintage))
        & dates.eq(reference_date.normalize())
    ]
    if len(candidates) > 1:
        raise ValueError(
            f"multiple rate parameters match benchmark vintage {benchmark_vintage}"
        )
    return candidates.iloc[0] if len(candidates) == 1 else None


def _lookup_distribution(
    distributions: pd.DataFrame | None,
    benchmark_vintage: str,
    reference_date: pd.Timestamp,
) -> pd.DataFrame:
    if distributions is None or distributions.empty:
        return pd.DataFrame()
    dates = pd.to_datetime(
        distributions["reference_date"], errors="coerce"
    ).dt.normalize()
    candidates = distributions.loc[
        distributions["benchmark_vintage"].astype(str).eq(
            str(benchmark_vintage)
        )
        & dates.eq(reference_date.normalize())
    ]
    return candidates.copy()


def _lookup_ca_upb(
    california_upb: pd.DataFrame | None,
    benchmark_vintage: str,
    reference_date: pd.Timestamp,
) -> tuple[float, pd.Series | None]:
    if california_upb is None:
        return math.nan, None
    if california_upb.empty:
        return math.nan, None
    dates = pd.to_datetime(
        california_upb["reference_date"], errors="coerce"
    ).dt.normalize()
    candidates = california_upb.loc[
        california_upb["benchmark_vintage"].astype(str).eq(
            str(benchmark_vintage)
        )
        & dates.eq(reference_date.normalize())
    ]
    if len(candidates) > 1:
        raise ValueError(
            "multiple California UPB controls match "
            f"{benchmark_vintage} / {reference_date.date().isoformat()}"
        )
    if len(candidates) != 1:
        return math.nan, None
    row = candidates.iloc[0]
    return _number(row["value"]), row


def _derived_base(
    *,
    acs_group: pd.DataFrame,
    variable_name: str,
    unit: str,
    value: float,
    low: float = math.nan,
    central: float | None = None,
    high: float = math.nan,
    construction_method: str,
    parameter_geography: str,
    sample_n: Any = None,
    coverage_rate: Any = None,
    quality_flag: str | None = None,
    notes: str,
    universe_definition: str | None = None,
    source_ids: list[Any],
    source_files: list[Any],
    source_vintages: list[Any],
    retrieved: list[Any],
    checksums: list[Any],
    release_dates: Mapping[str, Any] | None = None,
    transformation_id: str = "version_a_finance_benchmark_v1",
) -> dict[str, Any]:
    anchor = acs_group.iloc[0]
    central_value = value if central is None else central
    source_id = _pipe(source_ids)
    source_filename = _pipe(source_files)
    source_vintage = _pipe(source_vintages)
    retrieved_values = [
        pd.to_datetime(item, utc=True, errors="coerce")
        for item in retrieved
        if not pd.isna(item)
    ]
    retrieved_at = (
        max(item for item in retrieved_values if not pd.isna(item))
        if any(not pd.isna(item) for item in retrieved_values)
        else pd.NaT
    )
    release_audit = _release_date_mapping(
        anchor.get("source_release_dates")
    )
    anchor_source_id = anchor.get("source_id", "census_acs5_finance")
    if pd.isna(anchor_source_id):
        anchor_source_id = "census_acs5_finance"
    if (
        str(anchor_source_id) not in release_audit
        and not pd.isna(anchor.get("release_date"))
    ):
        release_audit[str(anchor_source_id)] = _normalise_release_audit_value(
            anchor.get("release_date")
        )
    if release_dates:
        for release_source_id, date in release_dates.items():
            normalized_date = _normalise_release_audit_value(date)
            if normalized_date is not None:
                release_audit[str(release_source_id)] = normalized_date

    release_values = [
        pd.to_datetime(item, errors="coerce")
        for item in release_audit.values()
        if item != "unavailable"
    ]
    release_date = (
        max(item for item in release_values if not pd.isna(item))
        if any(not pd.isna(item) for item in release_values)
        else pd.NaT
    )
    source_id_tokens = [] if source_id is None else str(source_id).split("|")
    ordered_release_ids = [
        *[token for token in source_id_tokens if token in release_audit],
        *[
            token
            for token in release_audit
            if token not in source_id_tokens
        ],
    ]
    source_release_dates = "|".join(
        f"{release_source_id}={release_audit[release_source_id]}"
        for release_source_id in dict.fromkeys(ordered_release_ids)
    )
    checksum = _pipe(checksums)
    missing = not _finite(value)
    resolved_quality = (
        quality_flag
        if quality_flag is not None
        else ("parameter_unavailable" if missing else "derived_source_faithful")
    )
    if missing and not any(
        marker in str(resolved_quality).casefold()
        for marker in (
            "unavailable",
            "missing",
            "incomplete",
            "insufficient",
            "suppressed",
        )
    ):
        resolved_quality = _pipe(
            [resolved_quality, "derived_value_unavailable"]
        )
    return benchmark_row(
        county_fips=str(anchor["county_fips"]),
        state_fips="06",
        geo_id=f"COUNTY_{anchor['county_fips']}",
        month=pd.NaT,
        value=value,
        variable_name=variable_name,
        variable_id=variable_name,
        unit=unit,
        estimate_variant="central",
        reported_value=None,
        database_version="A. Benchmark",
        source_id=source_id,
        source_frequency=str(anchor["source_frequency"]),
        source_period_start=anchor["source_period_start"],
        source_period_end=anchor["source_period_end"],
        reference_date=anchor["reference_date"],
        release_date=release_date,
        source_release_dates=source_release_dates or None,
        source_geography="county",
        parameter_geography=parameter_geography,
        monthly_method="not_applicable_source_period",
        construction_method=construction_method,
        payment_frequency="monthly" if "monthly" in unit else None,
        is_modeled=False,
        is_nowcast=False,
        is_imputed=False,
        is_interpolated=False,
        is_suppressed=False,
        benchmark_vintage=str(anchor["benchmark_vintage"]),
        sample_n=sample_n,
        coverage_rate=coverage_rate,
        low_estimate=low,
        central_estimate=central_value,
        high_estimate=high,
        universe_definition=universe_definition
        or (
            "Owner-occupied housing units with a mortgage, contract to "
            "purchase, or similar debt"
        ),
        notes=notes,
        source_vintage=source_vintage,
        retrieved_at=retrieved_at,
        source_filename=source_filename,
        checksum_sha256=checksum,
        transformation_id=transformation_id,
        lineage_id=_lineage(
            source_id,
            checksum,
            anchor["county_fips"],
            anchor["reference_date"],
            variable_name,
        ),
        quality_flag=resolved_quality,
    )


def _ratio_bounds(
    numerator_low: float,
    numerator_high: float,
    denominator_low: float,
    denominator_high: float,
) -> tuple[float, float]:
    low = (
        numerator_low / denominator_high
        if _finite(numerator_low)
        and _finite(denominator_high)
        and denominator_high > 0
        else math.nan
    )
    high = (
        numerator_high / denominator_low
        if _finite(numerator_high)
        and _finite(denominator_low)
        and denominator_low > 0
        else math.nan
    )
    return low, high


def build_version_a_benchmark(
    acs_rows: pd.DataFrame,
    pums_parameters: pd.DataFrame,
    *,
    hmda_rows: pd.DataFrame | None = None,
    cpi_rows: pd.DataFrame | None = None,
    pmms_rows: pd.DataFrame | None = None,
    rate_parameters: pd.DataFrame | None = None,
    nmdb_distributions: pd.DataFrame | None = None,
    california_upb: pd.DataFrame | None = None,
    output_path: Path | None = None,
    la_export_path: Path | None = None,
) -> pd.DataFrame:
    """Build all Version A finance observations at their published periods.

    ACS, PUMS, and HMDA source estimates are retained alongside the four
    requested finance constructs. County PUMS P&I shares are used only where
    every PUMA is wholly contained; shared-PUMA counties use their complete
    connected region before a labeled California fallback. NMDB joint
    rate/term input is preferred. When it is absent, an exact-month PMMS rate
    may be used only as a documented 20/25/30-year sensitivity. Calibrated
    debt and equity remain null unless a compatible published California UPB
    control and nonmissing raw debt for exactly all 58 counties are supplied.
    Constant-2024-dollar equity uses the published BLS CPI-U annual average
    for the ACS vintage ending year, never the ACS five-year midpoint; those
    real rows remain null if either required CPI value is unavailable.
    """
    required_acs = {
        "county_fips",
        "variable_name",
        "value",
        "benchmark_vintage",
        "reference_date",
        "source_period_start",
        "source_period_end",
        "source_frequency",
    }
    missing = required_acs.difference(acs_rows.columns)
    if missing:
        raise KeyError(f"missing ACS benchmark columns: {sorted(missing)}")
    _validate_nmdb_inputs(nmdb_distributions, california_upb)

    acs_contract = _source_contract(
        acs_rows,
        default_source_id="census_acs5_finance",
        default_transformation_id="acs5_finance_direct_v1",
    )
    hmda_contract = _source_contract(
        hmda_rows,
        default_source_id="cfpb_hmda_modified_lar",
        default_transformation_id="hmda_downpayment_annual_v1",
    )
    pums_contract = pd.DataFrame(columns=BENCHMARK_COLUMNS)
    pums_contract_fields = {
        "county_fips",
        "variable_name",
        "source_frequency",
        "source_period_start",
        "source_period_end",
        "reference_date",
        "release_date",
    }
    if (
        not pums_parameters.empty
        and pums_contract_fields.issubset(pums_parameters.columns)
    ):
        county_pums = pums_parameters.loc[
            pums_parameters["county_fips"].notna()
        ].copy()
        pums_contract = _source_contract(
            county_pums,
            default_source_id="census_acs5_pums_housing_ca",
            default_transformation_id=(
                "acs_pums_parameter_geography_hierarchy_v2"
            ),
        )
    if rate_parameters is None:
        rates = (
            pmms_reference_rate_parameters(pmms_rows, acs_rows)
            if pmms_rows is not None and not pmms_rows.empty
            else pd.DataFrame()
        )
    else:
        rates = rate_parameters.copy()
    cpi_by_year = cpi_u_annual_index(cpi_rows)

    constructed: list[dict[str, Any]] = []
    debt_rows: list[dict[str, Any]] = []
    scenario_debt_by_key: dict[
        tuple[str, str, pd.Timestamp, int], dict[str, Any]
    ] = {}
    group_key = ["county_fips", "benchmark_vintage", "reference_date"]
    for (_, benchmark_vintage, reference_raw), group in acs_rows.groupby(
        group_key, sort=True, dropna=False
    ):
        county_fips = str(group.iloc[0]["county_fips"]).zfill(5)
        reference_date = _as_timestamp(reference_raw)
        values = _indexed_acs(group)
        pums = _select_pums_parameter(
            pums_parameters,
            county_fips=county_fips,
            benchmark_vintage=str(benchmark_vintage),
            reference_date=reference_date,
        )
        share = math.nan if pums is None else _number(pums.get("value"))
        pums_geography = (
            "unavailable"
            if pums is None
            else str(pums.get("parameter_geography"))
        )
        source_ids: list[Any] = ["census_acs5_finance"]
        source_files: list[Any] = [group.iloc[0].get("source_filename")]
        source_vintages: list[Any] = [group.iloc[0].get("source_vintage")]
        retrieved: list[Any] = [group.iloc[0].get("retrieved_at")]
        checksums: list[Any] = [group.iloc[0].get("checksum_sha256")]
        release_dates: dict[str, Any] = {}
        if pums is not None:
            relationship_vintage = _number(pums.get("relationship_vintage"))
            relationship_source_id = (
                str(pums.get("relationship_source_id"))
                if not pd.isna(pums.get("relationship_source_id"))
                else (
                    f"census_tract_to_puma_{int(relationship_vintage)}"
                    if _finite(relationship_vintage)
                    else "census_tract_to_puma_relationship"
                )
            )
            source_ids.extend(
                [
                    "census_acs5_pums_housing_ca",
                    relationship_source_id,
                ]
            )
            source_files.extend(
                [
                    pums.get("source_filename"),
                    pums.get("relationship_source_filename"),
                ]
            )
            source_vintages.extend(
                [
                    pums.get("source_vintage"),
                    pums.get("relationship_vintage"),
                ]
            )
            retrieved.extend(
                [
                    pums.get("retrieved_at"),
                    pums.get("relationship_retrieved_at"),
                ]
            )
            checksums.extend(
                [
                    pums.get("checksum_sha256"),
                    pums.get("relationship_checksum_sha256"),
                ]
            )
            if not pd.isna(pums.get("release_date")):
                release_dates["census_acs5_pums_housing_ca"] = pums.get(
                    "release_date"
                )
            relationship_release = pums.get(
                "relationship_release_date",
                "unavailable",
            )
            release_dates[relationship_source_id] = (
                "unavailable"
                if pd.isna(relationship_release)
                else relationship_release
            )

        selected = _estimate(values, ACS_SELECTED_COSTS)
        units = _estimate(values, ACS_MORTGAGED_UNITS)
        income = _estimate(values, ACS_MORTGAGED_INCOME)
        payment = calculate_aggregate_mortgage_payment(
            selected,
            share,
            mortgaged_units=units,
            aggregate_income_mortgaged_owners=income,
        )
        selected_low_raw = _estimate(
            values, ACS_SELECTED_COSTS, "low_estimate"
        )
        selected_low = (
            max(0.0, selected_low_raw)
            if _finite(selected_low_raw)
            else math.nan
        )
        selected_high = _estimate(values, ACS_SELECTED_COSTS, "high_estimate")
        share_low_raw = (
            math.nan if pums is None else _number(pums.get("low_estimate"))
        )
        share_high_raw = (
            math.nan if pums is None else _number(pums.get("high_estimate"))
        )
        share_low = share_low_raw if _finite(share_low_raw) else share
        share_high = share_high_raw if _finite(share_high_raw) else share
        payment_low = (
            selected_low * share_low
            if _finite(selected_low) and _finite(share_low)
            else math.nan
        )
        payment_high = (
            selected_high * share_high
            if _finite(selected_high) and _finite(share_high)
            else math.nan
        )
        units_low = _estimate(values, ACS_MORTGAGED_UNITS, "low_estimate")
        units_high = _estimate(values, ACS_MORTGAGED_UNITS, "high_estimate")
        income_low = _estimate(values, ACS_MORTGAGED_INCOME, "low_estimate")
        income_high = _estimate(values, ACS_MORTGAGED_INCOME, "high_estimate")
        per_unit_low, per_unit_high = _ratio_bounds(
            payment_low, payment_high, units_low, units_high
        )
        dsr_low, dsr_high = _ratio_bounds(
            12.0 * payment_low if _finite(payment_low) else math.nan,
            12.0 * payment_high if _finite(payment_high) else math.nan,
            income_low,
            income_high,
        )
        output_bounds = {
            "aggregate_monthly_pi": (payment_low, payment_high),
            "pi_per_mortgaged_unit": (per_unit_low, per_unit_high),
            "annual_debt_service": (
                12.0 * payment_low if _finite(payment_low) else math.nan,
                12.0 * payment_high if _finite(payment_high) else math.nan,
            ),
            "mortgage_debt_service_to_income": (dsr_low, dsr_high),
        }
        if pums is None or not _finite(share):
            parameter_quality = "pums_parameter_unavailable"
        elif str(pums.get("geography_tier")) == "regional":
            parameter_quality = "regional_puma_pool"
        elif (
            str(pums.get("geography_tier")) == "state_fallback"
            or pums.get("parameter_geography_level") == "state"
        ):
            parameter_quality = "california_pums_fallback"
        else:
            parameter_quality = "county_pums_parameter"
        constructed.append(
            _derived_base(
                acs_group=group,
                variable_name="pums_first_mortgage_pi_share_applied_benchmark",
                unit="ratio",
                value=share,
                low=share_low_raw,
                high=share_high_raw,
                construction_method="PUMS weighted first-mortgage P&I share",
                parameter_geography=pums_geography,
                sample_n=None if pums is None else pums.get("sample_n"),
                coverage_rate=None if pums is None else pums.get("coverage_rate"),
                quality_flag=parameter_quality,
                notes=(
                    "A wholly contained county PUMA pool is preferred, followed "
                    "by the connected regional PUMA pool, then a labeled "
                    "California fallback. No temporal interpolation is used."
                ),
                source_ids=source_ids,
                source_files=source_files,
                source_vintages=source_vintages,
                retrieved=retrieved,
                checksums=checksums,
                release_dates=release_dates,
            )
        )
        for metric_key, variable_name, unit in PAYMENT_OUTPUTS:
            low, high = output_bounds[metric_key]
            payment_quality_parts: list[Any] = []
            if not _finite(payment[metric_key]):
                if not _finite(selected):
                    payment_quality_parts.append(
                        "acs_selected_owner_costs_unavailable"
                    )
                if not _finite(share):
                    payment_quality_parts.append(
                        "pums_parameter_unavailable"
                    )
                if metric_key == "pi_per_mortgaged_unit" and (
                    not _finite(units) or units <= 0
                ):
                    payment_quality_parts.append(
                        "acs_mortgaged_owner_units_unavailable"
                    )
                if metric_key == "mortgage_debt_service_to_income" and (
                    not _finite(income) or income <= 0
                ):
                    payment_quality_parts.append(
                        "acs_mortgaged_owner_income_unavailable"
                    )
            payment_quality = (
                _pipe(payment_quality_parts)
                if payment_quality_parts
                else parameter_quality
            )
            constructed.append(
                _derived_base(
                    acs_group=group,
                    variable_name=variable_name,
                    unit=unit,
                    value=payment[metric_key],
                    low=low,
                    high=high,
                    construction_method=(
                        "ACS published aggregate level multiplied by the "
                        "source-period PUMS weighted P&I share"
                    ),
                    parameter_geography=pums_geography,
                    sample_n=None if pums is None else pums.get("sample_n"),
                    coverage_rate=(
                        None if pums is None else pums.get("coverage_rate")
                    ),
                    quality_flag=payment_quality,
                    notes=(
                        "Retained at the ACS 5-year source interval. Published "
                        "ACS 90% MOEs and, when available, official PUMS "
                        "replicate-weight 90% MOEs supply bounds where ratio "
                        "arithmetic permits; no monthly allocation is performed."
                    ),
                    source_ids=source_ids,
                    source_files=source_files,
                    source_vintages=source_vintages,
                    retrieved=retrieved,
                    checksums=checksums,
                    release_dates=release_dates,
                )
            )

        distribution = _lookup_distribution(
            nmdb_distributions,
            str(benchmark_vintage),
            reference_date,
        )
        rate = _lookup_rate(rates, str(benchmark_vintage), reference_date)
        aggregate_pi = payment["aggregate_monthly_pi"]
        scenario_terms = tuple(
            remaining_years
            for remaining_years, _, _, _ in PMMS_TERM_SCENARIO_OUTPUTS
        )
        scenario_source_ids = source_ids.copy()
        scenario_source_files = source_files.copy()
        scenario_source_vintages = source_vintages.copy()
        scenario_retrieved = retrieved.copy()
        scenario_checksums = checksums.copy()
        scenario_release_dates = release_dates.copy()
        if rate is not None and _finite(rate.get("value")):
            scenario_rate_geography = str(
                rate.get("parameter_geography", "US")
            )
            scenario_debts = implied_debt_scenarios(
                aggregate_pi,
                _number(rate.get("value")),
                scenario_terms,
            )
            scenario_debt_lows = implied_debt_scenarios(
                payment_low,
                _number(rate.get("value")),
                scenario_terms,
            )
            scenario_debt_highs = implied_debt_scenarios(
                payment_high,
                _number(rate.get("value")),
                scenario_terms,
            )
            scenario_quality = "pmms_origination_rate_sensitivity"
            scenario_source_id = rate.get("source_id", "freddie_mac_pmms")
            scenario_source_ids.append(scenario_source_id)
            scenario_source_files.append(rate.get("source_filename"))
            scenario_source_vintages.append(rate.get("source_vintage"))
            scenario_retrieved.append(rate.get("retrieved_at"))
            scenario_checksums.append(rate.get("checksum_sha256"))
            if not pd.isna(rate.get("release_date")):
                scenario_release_dates[str(scenario_source_id)] = rate.get(
                    "release_date"
                )
            if not _finite(aggregate_pi):
                scenario_quality = "aggregate_first_mortgage_pi_unavailable"
        else:
            scenario_rate_geography = "unavailable"
            scenario_debts = {
                float(remaining_years): math.nan
                for remaining_years in scenario_terms
            }
            scenario_debt_lows = scenario_debts.copy()
            scenario_debt_highs = scenario_debts.copy()
            scenario_quality = _pipe(
                [
                    (
                        "aggregate_first_mortgage_pi_unavailable"
                        if not _finite(aggregate_pi)
                        else None
                    ),
                    "pmms_rate_parameter_unavailable",
                ]
            )
        if not distribution.empty:
            raw_debt = implied_debt_from_distribution(
                aggregate_pi,
                distribution,
                rates_are_percent=bool(
                    distribution.get(
                        "rates_are_percent",
                        pd.Series([False]),
                    ).iloc[0]
                ),
            )
            debt_low = implied_debt_from_distribution(
                payment_low,
                distribution,
                rates_are_percent=bool(
                    distribution.get(
                        "rates_are_percent",
                        pd.Series([False]),
                    ).iloc[0]
                ),
            )
            debt_high = implied_debt_from_distribution(
                payment_high,
                distribution,
                rates_are_percent=bool(
                    distribution.get(
                        "rates_are_percent",
                        pd.Series([False]),
                    ).iloc[0]
                ),
            )
            debt_method = "NMDB published joint outstanding rate-term distribution"
            debt_geography = str(
                distribution.get(
                    "parameter_geography",
                    pd.Series(["US"]),
                ).iloc[0]
            )
            debt_quality = "nmdb_joint_distribution"
            distribution_source_id = str(
                distribution["source_id"].iloc[0]
            )
            source_ids.append(distribution_source_id)
            source_files.append(
                distribution.get(
                    "source_filename", pd.Series([None])
                ).iloc[0]
            )
            source_vintages.append(
                distribution.get(
                    "source_vintage", pd.Series([None])
                ).iloc[0]
            )
            retrieved.append(
                distribution.get("retrieved_at", pd.Series([None])).iloc[0]
            )
            checksums.append(
                distribution.get(
                    "checksum_sha256", pd.Series([None])
                ).iloc[0]
            )
            distribution_release = distribution.get(
                "release_date", pd.Series([pd.NaT])
            ).iloc[0]
            if not pd.isna(distribution_release):
                release_dates[distribution_source_id] = distribution_release
        elif rate is not None and _finite(rate.get("value")):
            debt_low = scenario_debt_lows[20.0]
            raw_debt = scenario_debts[25.0]
            debt_high = scenario_debt_highs[30.0]
            debt_method = "PMMS exact-reference-month 20/25/30-year sensitivity"
            debt_geography = str(rate.get("parameter_geography", "US"))
            debt_quality = "pmms_origination_rate_sensitivity"
            source_ids.append(rate.get("source_id", "freddie_mac_pmms"))
            source_files.append(rate.get("source_filename"))
            source_vintages.append(rate.get("source_vintage"))
            retrieved.append(rate.get("retrieved_at"))
            checksums.append(rate.get("checksum_sha256"))
            if not pd.isna(rate.get("release_date")):
                release_dates[str(rate.get("source_id", "freddie_mac_pmms"))] = (
                    rate.get("release_date")
                )
        else:
            raw_debt = debt_low = debt_high = math.nan
            debt_method = "rate-term parameter unavailable"
            debt_geography = "unavailable"
            debt_quality = "rate_term_parameter_unavailable"

        if not _finite(raw_debt):
            debt_quality_parts: list[Any] = []
            if not _finite(aggregate_pi):
                debt_quality_parts.append(
                    "aggregate_first_mortgage_pi_unavailable"
                )
            if "unavailable" in debt_quality:
                debt_quality_parts.append(debt_quality)
            if not debt_quality_parts:
                debt_quality_parts.append("debt_calculation_unavailable")
            debt_quality = _pipe(debt_quality_parts) or debt_quality

        raw_row = _derived_base(
            acs_group=group,
            variable_name="acs_implied_first_mortgage_balance_raw_benchmark",
            unit="USD",
            value=raw_debt,
            low=debt_low,
            high=debt_high,
            construction_method=debt_method,
            parameter_geography=f"{pums_geography}|{debt_geography}",
            sample_n=None if pums is None else pums.get("sample_n"),
            coverage_rate=None if pums is None else pums.get("coverage_rate"),
            quality_flag=debt_quality,
            notes=(
                "Fixed-rate-equivalent first-lien debt. NMDB joint outstanding "
                "rate/remaining-term input is preferred; PMMS is explicitly a "
                "fallback sensitivity and not an outstanding-stock coupon. "
                "NMDB bounds capitalize ACS payment bounds with the same joint "
                "distribution; PMMS bounds combine the ACS lower/upper payment "
                "bound with the 20/30-year sensitivity, respectively."
            ),
            source_ids=source_ids,
            source_files=source_files,
            source_vintages=source_vintages,
            retrieved=retrieved,
            checksums=checksums,
            release_dates=release_dates,
        )
        constructed.append(raw_row)
        debt_rows.append(raw_row)
        for (
            remaining_years,
            debt_variable,
            _,
            _,
        ) in PMMS_TERM_SCENARIO_OUTPUTS:
            scenario_row = _derived_base(
                acs_group=group,
                variable_name=debt_variable,
                unit="USD",
                value=scenario_debts[float(remaining_years)],
                low=scenario_debt_lows[float(remaining_years)],
                high=scenario_debt_highs[float(remaining_years)],
                construction_method=(
                    "PMMS exact-reference-month fixed-rate-equivalent "
                    f"first-mortgage debt with {remaining_years}-year "
                    "remaining term"
                ),
                parameter_geography=(
                    f"{pums_geography}|{scenario_rate_geography}"
                ),
                sample_n=None if pums is None else pums.get("sample_n"),
                coverage_rate=(
                    None if pums is None else pums.get("coverage_rate")
                ),
                quality_flag=scenario_quality,
                notes=(
                    "Explicit uncalibrated remaining-term sensitivity. The "
                    "central estimate capitalizes central aggregate P&I; its "
                    "bounds capitalize the ACS/PUMS payment bounds at the same "
                    f"{remaining_years}-year term. PMMS is an origination-rate "
                    "sensitivity, not an outstanding-stock coupon, and this "
                    "row is never substituted for calibrated debt."
                ),
                source_ids=scenario_source_ids,
                source_files=scenario_source_files,
                source_vintages=scenario_source_vintages,
                retrieved=scenario_retrieved,
                checksums=scenario_checksums,
                release_dates=scenario_release_dates,
                transformation_id="version_a_pmms_term_scenario_debt_v1",
            )
            constructed.append(scenario_row)
            scenario_key = (
                county_fips,
                str(benchmark_vintage),
                reference_date.normalize(),
                remaining_years,
            )
            scenario_debt_by_key[scenario_key] = scenario_row

    debt_frame = pd.DataFrame(debt_rows)
    calibrated_by_key: dict[
        tuple[str, str, pd.Timestamp], dict[str, float]
    ] = {}
    calibration_status_by_key: dict[tuple[str, pd.Timestamp], str] = {}
    calibration_controls_by_key: dict[
        tuple[str, pd.Timestamp], pd.Series | None
    ] = {}
    calibration_upb_by_key: dict[tuple[str, pd.Timestamp], float] = {}
    if not debt_frame.empty:
        debt_frame["_reference_key"] = pd.to_datetime(
            debt_frame["reference_date"], errors="coerce"
        ).dt.normalize()
        for (vintage, reference_raw), group in debt_frame.groupby(
            ["benchmark_vintage", "_reference_key"],
            sort=True,
            dropna=False,
        ):
            reference = _as_timestamp(reference_raw).normalize()
            parameter_key = (str(vintage), reference)
            upb, upb_control = _lookup_ca_upb(
                california_upb, str(vintage), reference
            )
            calibration_controls_by_key[parameter_key] = upb_control
            calibration_upb_by_key[parameter_key] = upb
            raw_series = group.set_index("county_fips")["value"]
            actual_counties = set(raw_series.index.astype(str))
            expected_counties = set(CALIFORNIA_COUNTY_FIPS)
            if not _finite(upb):
                calibration_status_by_key[parameter_key] = (
                    "california_upb_calibration_unavailable"
                )
            elif actual_counties != expected_counties:
                calibration_status_by_key[parameter_key] = (
                    "california_county_coverage_incomplete"
                )
            elif raw_series.isna().any():
                calibration_status_by_key[parameter_key] = (
                    "county_raw_debt_incomplete"
                )
            else:
                calibration = calibrate_implied_debt_to_california(
                    raw_series,
                    upb,
                    expected_counties=CALIFORNIA_COUNTY_FIPS,
                )
                factor = float(calibration["calibration_factor"])
                calibration_status_by_key[parameter_key] = (
                    "calibrated_to_published_california_upb"
                )
                for _, row in group.iterrows():
                    key = (
                        str(row["county_fips"]),
                        str(vintage),
                        reference,
                    )
                    calibrated_by_key[key] = {
                        "value": _number(row["value"]) * factor,
                        "low": _number(row["low_estimate"]) * factor,
                        "high": _number(row["high_estimate"]) * factor,
                        "factor": factor,
                    }

    for (_, benchmark_vintage, reference_raw), group in acs_rows.groupby(
        group_key, sort=True, dropna=False
    ):
        county_fips = str(group.iloc[0]["county_fips"]).zfill(5)
        reference = _as_timestamp(reference_raw).normalize()
        parameter_key = (str(benchmark_vintage), reference)
        key = (county_fips, str(benchmark_vintage), reference)
        calibrated = calibrated_by_key.get(key)
        if calibrated is None:
            calibrated = {
                "value": math.nan,
                "low": math.nan,
                "high": math.nan,
                "factor": math.nan,
            }
            calibration_quality = calibration_status_by_key.get(
                parameter_key,
                "california_upb_calibration_unavailable",
            )
        else:
            calibration_quality = "calibrated_to_published_california_upb"

        raw_matches = debt_frame.loc[
            debt_frame["county_fips"].astype(str).eq(county_fips)
            & debt_frame["benchmark_vintage"].astype(str).eq(
                str(benchmark_vintage)
            )
            & debt_frame["_reference_key"].eq(reference)
        ]
        if len(raw_matches) != 1:
            raise ValueError(
                "expected one raw debt row for "
                f"{county_fips} / {benchmark_vintage}"
            )
        raw_provenance = raw_matches.iloc[0]
        finance_source_ids: list[Any] = [raw_provenance.get("source_id")]
        finance_source_files: list[Any] = [
            raw_provenance.get("source_filename")
        ]
        finance_source_vintages: list[Any] = [
            raw_provenance.get("source_vintage")
        ]
        finance_retrieved: list[Any] = [raw_provenance.get("retrieved_at")]
        finance_checksums: list[Any] = [
            raw_provenance.get("checksum_sha256")
        ]
        finance_release_dates = _release_date_mapping(
            raw_provenance.get("source_release_dates")
        )
        upb_control = calibration_controls_by_key.get(parameter_key)
        upb = calibration_upb_by_key.get(parameter_key, math.nan)
        if _finite(upb):
            if upb_control is None:
                raise ValueError(
                    "finite California UPB requires normalized control provenance"
                )
            upb_source_id = str(upb_control["source_id"])
            finance_source_ids.append(upb_source_id)
            finance_source_files.append(upb_control.get("source_filename"))
            finance_source_vintages.append(upb_control.get("source_vintage"))
            finance_retrieved.append(upb_control.get("retrieved_at"))
            finance_checksums.append(upb_control.get("checksum_sha256"))
            if not pd.isna(upb_control.get("release_date")):
                finance_release_dates[upb_source_id] = upb_control.get(
                    "release_date"
                )
        cal_row = _derived_base(
            acs_group=group,
            variable_name="acs_implied_first_mortgage_balance_cal_benchmark",
            unit="USD",
            value=calibrated["value"],
            low=calibrated["low"],
            high=calibrated["high"],
            construction_method="California ratio calibration of raw county debt",
            parameter_geography="California",
            quality_flag=calibration_quality,
            notes=(
                "Calibration factor equals compatible published California UPB "
                "divided by the sum of nonmissing county raw debt. The value "
                "remains null when that control is unavailable or any county "
                "raw debt is missing."
            ),
            source_ids=finance_source_ids,
            source_files=finance_source_files,
            source_vintages=finance_source_vintages,
            retrieved=finance_retrieved,
            checksums=finance_checksums,
            release_dates=finance_release_dates,
        )
        constructed.append(cal_row)

        values = _indexed_acs(group)
        mortgaged_units = _estimate(values, ACS_MORTGAGED_UNITS)
        mortgaged_units_low = _estimate(
            values, ACS_MORTGAGED_UNITS, "low_estimate"
        )
        mortgaged_units_high = _estimate(
            values, ACS_MORTGAGED_UNITS, "high_estimate"
        )
        owner_units = _estimate(values, ACS_OWNER_UNITS)
        owner_units_low = _estimate(values, ACS_OWNER_UNITS, "low_estimate")
        owner_units_high = _estimate(values, ACS_OWNER_UNITS, "high_estimate")
        population = _estimate(values, ACS_TOTAL_POPULATION)
        population_low = _estimate(
            values, ACS_TOTAL_POPULATION, "low_estimate"
        )
        population_high = _estimate(
            values, ACS_TOTAL_POPULATION, "high_estimate"
        )
        owner_income = _estimate(values, ACS_OWNER_INCOME)
        owner_income_low = _estimate(
            values, ACS_OWNER_INCOME, "low_estimate"
        )
        owner_income_high = _estimate(
            values, ACS_OWNER_INCOME, "high_estimate"
        )
        value_mort = _estimate(values, ACS_MORTGAGED_VALUE)
        value_mort_low = _estimate(values, ACS_MORTGAGED_VALUE, "low_estimate")
        value_mort_high = _estimate(values, ACS_MORTGAGED_VALUE, "high_estimate")
        value_free = _estimate(values, ACS_NO_MORTGAGE_VALUE)
        value_free_low = _estimate(
            values, ACS_NO_MORTGAGE_VALUE, "low_estimate"
        )
        value_free_high = _estimate(
            values, ACS_NO_MORTGAGE_VALUE, "high_estimate"
        )
        for (
            remaining_years,
            _,
            mortgaged_equity_variable,
            all_owner_equity_variable,
        ) in PMMS_TERM_SCENARIO_OUTPUTS:
            scenario_key = (*key, remaining_years)
            scenario_debt_row = scenario_debt_by_key.get(scenario_key)
            if scenario_debt_row is None:
                raise ValueError(
                    "expected one PMMS term-scenario debt row for "
                    f"{county_fips} / {benchmark_vintage} / "
                    f"{remaining_years} years"
                )
            scenario_debt = _number(scenario_debt_row.get("value"))
            scenario_debt_low = _number(
                scenario_debt_row.get("low_estimate")
            )
            scenario_debt_high = _number(
                scenario_debt_row.get("high_estimate")
            )
            scenario_debt_quality = str(
                scenario_debt_row.get("quality_flag")
            )
            scenario_equity_mortgaged = (
                value_mort - scenario_debt
                if _finite(value_mort) and _finite(scenario_debt)
                else math.nan
            )
            scenario_equity_mortgaged_low = (
                value_mort_low - scenario_debt_high
                if _finite(value_mort_low) and _finite(scenario_debt_high)
                else math.nan
            )
            scenario_equity_mortgaged_high = (
                value_mort_high - scenario_debt_low
                if _finite(value_mort_high) and _finite(scenario_debt_low)
                else math.nan
            )
            scenario_mortgaged_quality_parts: list[Any] = []
            if not _finite(scenario_equity_mortgaged):
                if not _finite(scenario_debt):
                    scenario_mortgaged_quality_parts.append(
                        scenario_debt_quality
                    )
                if not _finite(value_mort):
                    scenario_mortgaged_quality_parts.append(
                        "acs_mortgaged_owner_value_unavailable"
                    )
            scenario_mortgaged_quality = (
                _pipe(scenario_mortgaged_quality_parts)
                if scenario_mortgaged_quality_parts
                else scenario_debt_quality
            )

            scenario_equity_all = (
                scenario_equity_mortgaged + value_free
                if _finite(scenario_equity_mortgaged)
                and _finite(value_free)
                else math.nan
            )
            scenario_equity_all_low = (
                scenario_equity_mortgaged_low + value_free_low
                if _finite(scenario_equity_mortgaged_low)
                and _finite(value_free_low)
                else math.nan
            )
            scenario_equity_all_high = (
                scenario_equity_mortgaged_high + value_free_high
                if _finite(scenario_equity_mortgaged_high)
                and _finite(value_free_high)
                else math.nan
            )
            scenario_all_quality_parts: list[Any] = []
            if not _finite(scenario_equity_all):
                if not _finite(scenario_equity_mortgaged):
                    scenario_all_quality_parts.append(
                        scenario_mortgaged_quality
                    )
                if not _finite(value_free):
                    scenario_all_quality_parts.append(
                        "acs_mortgage_free_owner_value_unavailable"
                    )
            scenario_all_quality = (
                _pipe(scenario_all_quality_parts)
                if scenario_all_quality_parts
                else scenario_debt_quality
            )

            scenario_source_ids = [scenario_debt_row.get("source_id")]
            scenario_source_files = [
                scenario_debt_row.get("source_filename")
            ]
            scenario_source_vintages = [
                scenario_debt_row.get("source_vintage")
            ]
            scenario_retrieved = [scenario_debt_row.get("retrieved_at")]
            scenario_checksums = [
                scenario_debt_row.get("checksum_sha256")
            ]
            scenario_release_dates = _release_date_mapping(
                scenario_debt_row.get("source_release_dates")
            )
            scenario_outputs = (
                (
                    mortgaged_equity_variable,
                    scenario_equity_mortgaged,
                    scenario_equity_mortgaged_low,
                    scenario_equity_mortgaged_high,
                    scenario_mortgaged_quality,
                    (
                        "Owner-occupied housing units with a mortgage, "
                        "contract to purchase, or similar debt"
                    ),
                ),
                (
                    all_owner_equity_variable,
                    scenario_equity_all,
                    scenario_equity_all_low,
                    scenario_equity_all_high,
                    scenario_all_quality,
                    "All owner-occupied housing units",
                ),
            )
            for (
                scenario_variable,
                scenario_value,
                scenario_low,
                scenario_high,
                scenario_quality,
                scenario_universe,
            ) in scenario_outputs:
                constructed.append(
                    _derived_base(
                        acs_group=group,
                        variable_name=scenario_variable,
                        unit="USD",
                        value=scenario_value,
                        low=scenario_low,
                        high=scenario_high,
                        construction_method=(
                            "Published ACS aggregate owner value less explicit "
                            f"uncalibrated {remaining_years}-year PMMS "
                            "first-mortgage debt sensitivity"
                        ),
                        parameter_geography=str(
                            scenario_debt_row.get("parameter_geography")
                        ),
                        sample_n=scenario_debt_row.get("sample_n"),
                        coverage_rate=scenario_debt_row.get("coverage_rate"),
                        quality_flag=scenario_quality,
                        notes=(
                            "Explicit scenario equity, not calibrated equity. "
                            "Mortgaged-owner equity subtracts the matching "
                            f"{remaining_years}-year PMMS debt sensitivity from "
                            "published mortgaged-owner value; all-owner equity "
                            "then adds published mortgage-free owner value in "
                            "full."
                        ),
                        universe_definition=scenario_universe,
                        source_ids=scenario_source_ids,
                        source_files=scenario_source_files,
                        source_vintages=scenario_source_vintages,
                        retrieved=scenario_retrieved,
                        checksums=scenario_checksums,
                        release_dates=scenario_release_dates,
                        transformation_id=(
                            "version_a_pmms_term_scenario_equity_v1"
                        ),
                    )
                )
        debt = calibrated["value"]
        debt_low = calibrated["low"]
        debt_high = calibrated["high"]
        debt_normalizations = calculate_debt_normalizations(
            debt,
            mortgaged_units=mortgaged_units,
            owner_units=owner_units,
            population=population,
            aggregate_owner_income=owner_income,
        )
        normalization_denominators = {
            "debt_per_mortgaged_unit": (
                mortgaged_units_low,
                mortgaged_units_high,
            ),
            "debt_per_owner_unit": (owner_units_low, owner_units_high),
            "debt_per_capita": (population_low, population_high),
            "debt_to_owner_income": (
                owner_income_low,
                owner_income_high,
            ),
        }
        normalization_denominator_values = {
            "debt_per_mortgaged_unit": mortgaged_units,
            "debt_per_owner_unit": owner_units,
            "debt_per_capita": population,
            "debt_to_owner_income": owner_income,
        }
        normalization_denominator_flags = {
            "debt_per_mortgaged_unit": "acs_mortgaged_owner_units",
            "debt_per_owner_unit": "acs_owner_occupied_units",
            "debt_per_capita": "acs_total_population",
            "debt_to_owner_income": "acs_aggregate_owner_income",
        }
        normalization_universes = {
            "debt_per_mortgaged_unit": (
                "First-mortgage debt divided by owner-occupied housing units "
                "with a mortgage, contract to purchase, or similar debt"
            ),
            "debt_per_owner_unit": (
                "First-mortgage debt divided by all owner-occupied housing units"
            ),
            "debt_per_capita": (
                "First-mortgage debt divided by the total resident population"
            ),
            "debt_to_owner_income": (
                "First-mortgage debt divided by aggregate household income "
                "for all owner-occupied housing units"
            ),
        }
        for metric, variable_name, unit in DEBT_NORMALIZATION_OUTPUTS:
            denominator_low, denominator_high = normalization_denominators[
                metric
            ]
            low, high = _ratio_bounds(
                debt_low,
                debt_high,
                denominator_low,
                denominator_high,
            )
            normalized_value = debt_normalizations[metric]
            denominator_value = normalization_denominator_values[metric]
            if not _finite(debt):
                normalization_quality = calibration_quality
            elif not _finite(normalized_value):
                denominator_flag = normalization_denominator_flags[metric]
                normalization_quality = (
                    f"{denominator_flag}_unavailable"
                    if not _finite(denominator_value)
                    else f"{denominator_flag}_nonpositive"
                )
            else:
                normalization_quality = calibration_quality
            constructed.append(
                _derived_base(
                    acs_group=group,
                    variable_name=variable_name,
                    unit=unit,
                    value=normalized_value,
                    low=low,
                    high=high,
                    construction_method=(
                        "California-calibrated first-mortgage debt divided "
                        "by the directly published matched ACS denominator"
                    ),
                    parameter_geography="county|California",
                    quality_flag=normalization_quality,
                    notes=(
                        "The denominator is retained at the same ACS source "
                        "period. Missing or zero denominators remain null; no "
                        "broader geography or time-series substitute is used."
                    ),
                    universe_definition=normalization_universes[metric],
                    source_ids=finance_source_ids,
                    source_files=finance_source_files,
                    source_vintages=finance_source_vintages,
                    retrieved=finance_retrieved,
                    checksums=finance_checksums,
                    release_dates=finance_release_dates,
                    transformation_id=(
                        "version_a_debt_equity_normalization_v1"
                    ),
                )
            )
        if _finite(value_mort) and _finite(debt):
            equity_mort = value_mort - debt
            equity_mort_low = (
                value_mort_low - debt_high
                if _finite(value_mort_low) and _finite(debt_high)
                else math.nan
            )
            equity_mort_high = (
                value_mort_high - debt_low
                if _finite(value_mort_high) and _finite(debt_low)
                else math.nan
            )
            aggregate_ltv = debt / value_mort if value_mort > 0 else math.nan
            ltv_low, ltv_high = _ratio_bounds(
                debt_low, debt_high, value_mort_low, value_mort_high
            )
            equity_rate = (
                1.0 - aggregate_ltv if _finite(aggregate_ltv) else math.nan
            )
            equity_rate_low = (
                1.0 - ltv_high if _finite(ltv_high) else math.nan
            )
            equity_rate_high = (
                1.0 - ltv_low if _finite(ltv_low) else math.nan
            )
        else:
            equity_mort = equity_mort_low = equity_mort_high = math.nan
            aggregate_ltv = ltv_low = ltv_high = math.nan
            equity_rate = equity_rate_low = equity_rate_high = math.nan
        if _finite(equity_mort) and _finite(value_free):
            equity_all = equity_mort + value_free
            equity_all_low = (
                equity_mort_low + value_free_low
                if _finite(equity_mort_low) and _finite(value_free_low)
                else math.nan
            )
            equity_all_high = (
                equity_mort_high + value_free_high
                if _finite(equity_mort_high) and _finite(value_free_high)
                else math.nan
            )
        else:
            equity_all = equity_all_low = equity_all_high = math.nan
        equity_per_owner_unit = (
            equity_all / owner_units
            if _finite(equity_all) and _finite(owner_units) and owner_units > 0
            else math.nan
        )
        equity_per_owner_unit_low, equity_per_owner_unit_high = _ratio_bounds(
            equity_all_low,
            equity_all_high,
            owner_units_low,
            owner_units_high,
        )
        equity_metrics = {
            "equity_mortgaged": (
                equity_mort,
                equity_mort_low,
                equity_mort_high,
                "USD",
            ),
            "equity_all": (
                equity_all,
                equity_all_low,
                equity_all_high,
                "USD",
            ),
            "aggregate_ltv": (
                aggregate_ltv,
                ltv_low,
                ltv_high,
                "ratio",
            ),
            "equity_rate": (
                equity_rate,
                equity_rate_low,
                equity_rate_high,
                "ratio",
            ),
            "equity_per_owner_unit": (
                equity_per_owner_unit,
                equity_per_owner_unit_low,
                equity_per_owner_unit_high,
                "USD/unit",
            ),
        }
        equity_quality_by_metric: dict[str, str] = {}
        for metric, (metric_value, _, _, _) in equity_metrics.items():
            missing_parts: list[Any] = []
            if not _finite(metric_value):
                if not _finite(debt):
                    missing_parts.append(calibration_quality)
                if not _finite(value_mort):
                    missing_parts.append(
                        "acs_mortgaged_owner_value_unavailable"
                    )
                elif metric in {"aggregate_ltv", "equity_rate"} and (
                    value_mort <= 0
                ):
                    missing_parts.append(
                        "acs_mortgaged_owner_value_nonpositive"
                    )
                if metric in {"equity_all", "equity_per_owner_unit"} and (
                    not _finite(value_free)
                ):
                    missing_parts.append(
                        "acs_mortgage_free_owner_value_unavailable"
                    )
                if metric == "equity_per_owner_unit" and (
                    not _finite(owner_units) or owner_units <= 0
                ):
                    missing_parts.append(
                        "acs_owner_occupied_units_unavailable"
                    )
            equity_quality_by_metric[metric] = (
                _pipe(missing_parts)
                if missing_parts
                else calibration_quality
            )
        for metric, variable_name in EQUITY_OUTPUTS:
            value, low, high, unit = equity_metrics[metric]
            constructed.append(
                _derived_base(
                    acs_group=group,
                    variable_name=variable_name,
                    unit=unit,
                    value=value,
                    low=low,
                    high=high,
                    construction_method=(
                        "Matched-universe ACS aggregate owner value less "
                        "California-calibrated first-mortgage debt"
                    ),
                    parameter_geography="county|California",
                    quality_flag=equity_quality_by_metric[metric],
                    notes=(
                        "Mortgaged-owner value and debt use the same universe. "
                        "Mortgage-free owner value is added only for the "
                        "all-owner equity measure."
                    ),
                    universe_definition=(
                        "Owner-occupied housing units with a mortgage, contract "
                        "to purchase, or similar debt"
                        if metric in {
                            "equity_mortgaged",
                            "aggregate_ltv",
                            "equity_rate",
                        }
                        else "All owner-occupied housing units"
                    ),
                    source_ids=finance_source_ids,
                    source_files=finance_source_files,
                    source_vintages=finance_source_vintages,
                    retrieved=finance_retrieved,
                    checksums=finance_checksums,
                    release_dates=finance_release_dates,
                    transformation_id=(
                        "version_a_debt_equity_normalization_v1"
                        if metric == "equity_per_owner_unit"
                        else "version_a_finance_benchmark_v1"
                    ),
                )
            )

        source_period_ends = pd.to_datetime(
            group["source_period_end"], errors="coerce"
        ).dropna()
        ending_years = source_period_ends.dt.year.unique()
        if len(ending_years) > 1:
            raise ValueError(
                "ACS county-vintage rows have inconsistent source-period "
                "ending years"
            )
        price_basis_year = (
            int(ending_years[0]) if len(ending_years) == 1 else None
        )
        price_cpi = (
            cpi_by_year.get(price_basis_year)
            if price_basis_year is not None
            else None
        )
        base_cpi = cpi_by_year.get(CPI_U_BASE_YEAR)
        if price_cpi is not None and base_cpi is not None:
            price_cpi_value = _number(price_cpi.get("value"))
            base_cpi_value = _number(base_cpi.get("value"))
            cpi_factor = base_cpi_value / price_cpi_value
        else:
            price_cpi_value = (
                math.nan if price_cpi is None else _number(price_cpi.get("value"))
            )
            base_cpi_value = (
                math.nan if base_cpi is None else _number(base_cpi.get("value"))
            )
            cpi_factor = math.nan

        cpi_provenance: list[pd.Series] = []
        for cpi_row in (price_cpi, base_cpi):
            if cpi_row is not None and not any(
                str(existing.get("checksum_sha256"))
                == str(cpi_row.get("checksum_sha256"))
                for existing in cpi_provenance
            ):
                cpi_provenance.append(cpi_row)
        # If neither requested year exists, retain the snapshot that
        # established the explicit missing result.
        if not cpi_provenance and cpi_by_year:
            cpi_provenance.append(next(iter(cpi_by_year.values())))

        real_source_ids = finance_source_ids.copy()
        real_source_files = finance_source_files.copy()
        real_source_vintages = finance_source_vintages.copy()
        real_retrieved = finance_retrieved.copy()
        real_checksums = finance_checksums.copy()
        cpi_release_dates = finance_release_dates.copy()
        for cpi_row in cpi_provenance:
            cpi_source_id = str(cpi_row.get("source_id", CPI_U_SOURCE_ID))
            real_source_ids.append(cpi_source_id)
            real_source_files.append(cpi_row.get("source_filename"))
            real_source_vintages.append(cpi_row.get("source_vintage"))
            real_retrieved.append(cpi_row.get("retrieved_at"))
            real_checksums.append(cpi_row.get("checksum_sha256"))
            cpi_release_dates[cpi_source_id] = cpi_row.get("release_date")

        cpi_unavailable_reason: str | None = None
        if price_basis_year is None:
            cpi_unavailable_reason = "acs_price_basis_year_unavailable"
        elif price_cpi is None and base_cpi is None:
            cpi_unavailable_reason = (
                f"cpi_u_{price_basis_year}_and_{CPI_U_BASE_YEAR}_unavailable"
            )
        elif price_cpi is None:
            cpi_unavailable_reason = f"cpi_u_{price_basis_year}_unavailable"
        elif base_cpi is None:
            cpi_unavailable_reason = (
                f"cpi_u_{CPI_U_BASE_YEAR}_unavailable"
            )

        for metric, variable_name in REAL_EQUITY_OUTPUTS:
            nominal, nominal_low, nominal_high, _ = equity_metrics[metric]
            real_value = (
                nominal * cpi_factor
                if _finite(nominal) and _finite(cpi_factor)
                else math.nan
            )
            real_low = (
                nominal_low * cpi_factor
                if _finite(nominal_low) and _finite(cpi_factor)
                else math.nan
            )
            real_high = (
                nominal_high * cpi_factor
                if _finite(nominal_high) and _finite(cpi_factor)
                else math.nan
            )
            quality_parts: list[Any] = []
            if not _finite(nominal):
                quality_parts.append(equity_quality_by_metric[metric])
            if cpi_unavailable_reason:
                quality_parts.append(cpi_unavailable_reason)
            elif not _finite(cpi_factor):
                quality_parts.append("cpi_u_index_unavailable")
            quality_flag = (
                _pipe(quality_parts)
                if quality_parts
                else "deflated_with_bls_cpi_u_annual_average"
            )
            reference = _as_timestamp(group.iloc[0]["reference_date"])
            midpoint_year = None if pd.isna(reference) else reference.year
            constructed.append(
                _derived_base(
                    acs_group=group,
                    variable_name=variable_name,
                    unit=f"constant_{CPI_U_BASE_YEAR}_USD",
                    value=real_value,
                    low=real_low,
                    high=real_high,
                    construction_method=(
                        "Nominal matched-universe equity multiplied by the "
                        f"BLS CPI-U annual-average ratio {CPI_U_BASE_YEAR}/"
                        "ACS-vintage-ending-year"
                    ),
                    parameter_geography="county|California|US",
                    quality_flag=quality_flag,
                    notes=(
                        f"ACS monetary values use the {price_basis_year} "
                        "vintage-ending-year price basis, not the "
                        f"{midpoint_year} five-year midpoint. Constant-dollar "
                        f"factor is CPI-U({CPI_U_BASE_YEAR})={base_cpi_value} "
                        f"divided by CPI-U({price_basis_year})="
                        f"{price_cpi_value}. Nominal equity rows are retained "
                        "unchanged; this real row remains null when nominal "
                        "equity or a required CPI annual average is unavailable."
                    ),
                    universe_definition=(
                        "Owner-occupied housing units with a mortgage, contract "
                        "to purchase, or similar debt"
                        if metric == "equity_mortgaged"
                        else "All owner-occupied housing units"
                    ),
                    source_ids=real_source_ids,
                    source_files=real_source_files,
                    source_vintages=real_source_vintages,
                    retrieved=real_retrieved,
                    checksums=real_checksums,
                    release_dates=cpi_release_dates,
                    transformation_id=(
                        "version_a_equity_constant_2024_usd_v1"
                    ),
                )
            )

    constructed_frame = pd.DataFrame(constructed)
    combined = pd.concat(
        [
            acs_contract,
            pums_contract,
            hmda_contract,
            normalize_benchmark(constructed_frame),
        ],
        ignore_index=True,
        sort=False,
    )
    result = finalize_benchmark(combined, output_path)
    if la_export_path is not None:
        la_export_path.parent.mkdir(parents=True, exist_ok=True)
        result.loc[result["county_fips"].eq("06037")].to_csv(
            la_export_path, index=False
        )
    return result


__all__ = [
    "PMMS_TERM_SCENARIO_OUTPUTS",
    "build_version_a_benchmark",
    "pmms_reference_rate_parameters",
]
