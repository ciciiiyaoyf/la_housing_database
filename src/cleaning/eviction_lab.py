"""Eviction Lab county annual estimate cleaner."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.cleaning.common import finalize, metadata_fields

LA_COUNTY_FIPS = "06037"
LA_STATE_FIPS = "06"

REQUIRED_COLUMNS = {
    "state",
    "county",
    "FIPS_state",
    "FIPS_county",
    "year",
    "renting_hh",
    "filings_estimate",
    "filings_ci_95_lower",
    "filings_ci_95_upper",
    "ind_filings_court_issued",
    "ind_filings_court_issued_LT",
    "hh_threat_estimate",
    "hh_threat_95_lower",
    "hh_threat_95_upper",
    "ind_hht_observed",
}

SOURCE_VARIABLES = {
    "renting_hh": ("eviction_lab_renting_households_annual", "renter_households"),
    "filings_estimate": ("eviction_lab_filings_estimate_annual", "filings"),
    "filings_ci_95_lower": (
        "eviction_lab_filings_ci_95_lower_annual",
        "filings",
    ),
    "filings_ci_95_upper": (
        "eviction_lab_filings_ci_95_upper_annual",
        "filings",
    ),
    "hh_threat_estimate": (
        "eviction_lab_households_threatened_estimate_annual",
        "households",
    ),
    "hh_threat_95_lower": (
        "eviction_lab_households_threatened_ci_95_lower_annual",
        "households",
    ),
    "hh_threat_95_upper": (
        "eviction_lab_households_threatened_ci_95_upper_annual",
        "households",
    ),
    "ind_filings_court_issued": (
        "eviction_lab_filings_court_issued_observed_flag",
        "flag",
    ),
    "ind_filings_court_issued_LT": (
        "eviction_lab_filings_court_issued_landlord_tenant_flag",
        "flag",
    ),
    "ind_hht_observed": (
        "eviction_lab_households_threatened_observed_flag",
        "flag",
    ),
}

RATE_VARIABLES = {
    "eviction_lab_filings_rate_estimate_annual": {
        "numerator": "filings_estimate",
        "observed_flag": "ind_filings_court_issued",
        "unit": "filings_per_100_renter_households",
        "quality_prefix": "filing_rate",
    },
    "eviction_lab_households_threatened_rate_estimate_annual": {
        "numerator": "hh_threat_estimate",
        "observed_flag": "ind_hht_observed",
        "unit": "households_threatened_per_100_renter_households",
        "quality_prefix": "households_threatened_rate",
    },
}


def _numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(
        series.astype(str).str.replace(",", "", regex=False).str.strip(),
        errors="coerce",
    )


def _flag_value(value: object) -> bool:
    parsed = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return bool(parsed == 1)


def _quality_flag(row: pd.Series, observed_flag: str, prefix: str) -> str:
    observed = _flag_value(row[observed_flag])
    if observed_flag == "ind_filings_court_issued":
        landlord_tenant = _flag_value(row["ind_filings_court_issued_LT"])
        suffix = (
            "court_issued_landlord_tenant_observed"
            if landlord_tenant
            else "court_issued_observed"
        )
    else:
        suffix = "observed" if observed else "modeled_estimate"
    if not observed:
        suffix = "modeled_estimate"
    return f"{prefix}_{suffix}"


def clean_eviction_lab_county_estimates(
    path: Path,
    record: dict[str, str],
    output_path: Path,
) -> pd.DataFrame:
    """Normalize Eviction Lab annual county estimates for Los Angeles County.

    The official Eviction Lab observed/validated county analysis files do not
    include Los Angeles County.  This cleaner uses the official county annual
    estimates file from the "Estimating Eviction Prevalence across the United
    States" release and preserves the observation-source flags supplied by
    Eviction Lab.
    """

    source = pd.read_csv(path, dtype=str)
    missing = REQUIRED_COLUMNS - set(source.columns)
    if missing:
        raise ValueError(
            f"Eviction Lab county estimates schema changed in {path.name}: "
            f"{sorted(missing)}"
        )

    source["FIPS_state"] = source["FIPS_state"].astype(str).str.zfill(2)
    source["FIPS_county"] = source["FIPS_county"].astype(str).str.zfill(5)
    source = source.loc[
        source["FIPS_state"].eq(LA_STATE_FIPS)
        & source["FIPS_county"].eq(LA_COUNTY_FIPS)
    ].copy()
    if source.empty:
        raise ValueError(f"No Los Angeles County rows found in {path.name}")

    numeric_columns = {
        "year",
        "renting_hh",
        "filings_estimate",
        "filings_ci_95_lower",
        "filings_ci_95_upper",
        "ind_filings_court_issued",
        "ind_filings_court_issued_LT",
        "hh_threat_estimate",
        "hh_threat_95_lower",
        "hh_threat_95_upper",
        "ind_hht_observed",
    }
    for column in numeric_columns:
        source[column] = _numeric(source[column])
    source = source.dropna(subset=["year"]).copy()
    source["year"] = source["year"].astype(int)
    if source.empty:
        raise ValueError(f"No numeric LA County annual observations in {path.name}")

    long_rows: list[dict[str, object]] = []
    base_metadata = metadata_fields(record)
    for _, row in source.sort_values("year").iterrows():
        year = int(row["year"])
        observation_date = pd.Timestamp(year=year, month=12, day=31)
        base = {
            "observation_date": observation_date,
            "source_observation_date": observation_date,
            "period_start": pd.Timestamp(year=year, month=1, day=1),
            "period_end": observation_date,
            "geo_id": "COUNTY_06037",
            "state_fips": LA_STATE_FIPS,
            "county_fips": LA_COUNTY_FIPS,
            "provider": "Eviction Lab",
            "native_frequency": "annual",
            "final_frequency": "annual",
            "is_interpolated": False,
            "is_seasonally_adjusted": False,
            **base_metadata,
        }

        for source_column, (variable_id, unit) in SOURCE_VARIABLES.items():
            value = row[source_column]
            if pd.isna(value):
                continue
            if source_column.startswith("filings"):
                observed_flag = "ind_filings_court_issued"
                quality_prefix = "filings"
            elif source_column.startswith("hh_threat"):
                observed_flag = "ind_hht_observed"
                quality_prefix = "households_threatened"
            else:
                observed_flag = ""
                quality_prefix = source_column
            is_imputed = (
                not _flag_value(row[observed_flag])
                if observed_flag
                else False
            )
            quality_flag = (
                _quality_flag(row, observed_flag, quality_prefix)
                if observed_flag
                else f"{source_column}_source_field"
            )
            long_rows.append(
                {
                    **base,
                    "variable_id": variable_id,
                    "value": value,
                    "reported_value": value,
                    "unit": unit,
                    "transformation_id": "eviction_lab_county_estimates_v1",
                    "is_imputed": is_imputed,
                    "quality_flag": quality_flag,
                }
            )

        denominator = row["renting_hh"]
        for variable_id, spec in RATE_VARIABLES.items():
            numerator = row[spec["numerator"]]
            if pd.isna(numerator) or pd.isna(denominator) or denominator == 0:
                continue
            value = numerator / denominator * 100
            observed_flag = str(spec["observed_flag"])
            long_rows.append(
                {
                    **base,
                    "variable_id": variable_id,
                    "value": value,
                    "reported_value": value,
                    "unit": spec["unit"],
                    "transformation_id": "eviction_lab_county_rate_v1",
                    "is_imputed": not _flag_value(row[observed_flag]),
                    "quality_flag": _quality_flag(
                        row,
                        observed_flag,
                        str(spec["quality_prefix"]),
                    ),
                }
            )

    return finalize(pd.DataFrame(long_rows), output_path)
