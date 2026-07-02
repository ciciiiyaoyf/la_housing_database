"""Clean the manually downloaded Redfin ZIP Key Metrics product."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

from src.cleaning.common import finalize
from src.common.io import atomic_json

REQUIRED_COLUMNS = (
    "LAST UPDATED",
    "FREQUENCY",
    "PERIOD BEGIN",
    "PERIOD END",
    "REGION ID",
    "REGION TYPE",
    "REGION NAME",
    "METRO",
    "HOMES SOLD",
    "HOMES SOLD YOY (%)",
    "MEDIAN SALE PRICE NSA ($)",
    "MEDIAN SALE PRICE NSA YOY (%)",
    "MEDIAN DAYS ON MARKET (DAYS)",
    "MEDIAN DAYS ON MARKET YOY (%)",
    "NEW LISTINGS",
    "NEW LISTINGS YOY (%)",
    "ACTIVE LISTINGS",
    "ACTIVE LISTINGS YOY (%)",
    "PENDING SALES",
    "PENDING SALES YOY (%)",
)

LEVEL_FIELDS = {
    "homes_sold": (
        "HOMES SOLD",
        "redfin_zip_homes_sold_rolling_3m",
        "count",
    ),
    "median_sale_price_nsa": (
        "MEDIAN SALE PRICE NSA ($)",
        "redfin_zip_median_sale_price_nsa_rolling_3m",
        "USD",
    ),
    "median_days_on_market": (
        "MEDIAN DAYS ON MARKET (DAYS)",
        "redfin_zip_median_days_on_market_rolling_3m",
        "days",
    ),
    "new_listings": (
        "NEW LISTINGS",
        "redfin_zip_new_listings_rolling_3m",
        "count",
    ),
    "active_listings": (
        "ACTIVE LISTINGS",
        "redfin_zip_active_listings_rolling_3m",
        "count",
    ),
    "pending_sales": (
        "PENDING SALES",
        "redfin_zip_pending_sales_rolling_3m",
        "count",
    ),
}

PROVIDER_YOY_FIELDS = {
    "homes_sold_yoy_pct_provider": (
        "HOMES SOLD YOY (%)",
        "redfin_zip_homes_sold_yoy_pct_provider_rolling_3m",
    ),
    "median_sale_price_nsa_yoy_pct_provider": (
        "MEDIAN SALE PRICE NSA YOY (%)",
        "redfin_zip_median_sale_price_nsa_yoy_pct_provider_rolling_3m",
    ),
    "median_days_on_market_yoy_pct_provider": (
        "MEDIAN DAYS ON MARKET YOY (%)",
        "redfin_zip_median_days_on_market_yoy_pct_provider_rolling_3m",
    ),
    "new_listings_yoy_pct_provider": (
        "NEW LISTINGS YOY (%)",
        "redfin_zip_new_listings_yoy_pct_provider_rolling_3m",
    ),
    "active_listings_yoy_pct_provider": (
        "ACTIVE LISTINGS YOY (%)",
        "redfin_zip_active_listings_yoy_pct_provider_rolling_3m",
    ),
    "pending_sales_yoy_pct_provider": (
        "PENDING SALES YOY (%)",
        "redfin_zip_pending_sales_yoy_pct_provider_rolling_3m",
    ),
}


def validate_redfin_zip_schema(columns: list[str] | tuple[str, ...]) -> None:
    actual = tuple(columns)
    if actual != REQUIRED_COLUMNS:
        missing = sorted(set(REQUIRED_COLUMNS) - set(actual))
        extra = sorted(set(actual) - set(REQUIRED_COLUMNS))
        raise ValueError(
            "Redfin ZIP Key Metrics schema changed: "
            f"missing={missing}, extra={extra}, "
            f"expected_order={list(REQUIRED_COLUMNS)}, "
            f"actual_order={list(actual)}"
        )


def _quoted(column: str) -> str:
    return '"' + column.replace('"', '""') + '"'


def _read_csv_sql(path: Path) -> str:
    escaped = path.as_posix().replace("'", "''")
    return (
        f"read_csv('{escaped}', header=true, all_varchar=true, "
        "sample_size=-1)"
    )


def _source_audit(
    connection: duckdb.DuckDBPyConnection,
    coverage_dir: Path,
) -> dict:
    null_expressions = ",\n".join(
        f"count(*) - count({_quoted(column)}) AS {_quoted('null__' + column)}"
        for column in REQUIRED_COLUMNS
    )
    audit = connection.execute(
        f"""
        SELECT count(*) AS total_rows,
               count(DISTINCT "REGION NAME") AS total_unique_zips,
               min(try_cast("PERIOD BEGIN" AS DATE)) AS min_period_begin,
               max(try_cast("PERIOD BEGIN" AS DATE)) AS max_period_begin,
               min(try_cast("PERIOD END" AS DATE)) AS min_period_end,
               max(try_cast("PERIOD END" AS DATE)) AS max_period_end,
               count(*) - count(DISTINCT ("REGION NAME", "PERIOD END"))
                   AS duplicate_zip_period_rows,
               string_agg(DISTINCT "FREQUENCY", '|' ORDER BY "FREQUENCY")
                   AS frequency_values,
               string_agg(DISTINCT "REGION TYPE", '|' ORDER BY "REGION TYPE")
                   AS region_type_values,
               min(try_cast("LAST UPDATED" AS DATE)) AS min_last_updated,
               max(try_cast("LAST UPDATED" AS DATE)) AS max_last_updated,
               {null_expressions}
        FROM redfin_source
        """
    ).fetchdf()
    nulls = pd.DataFrame(
        {
            "source_field": list(REQUIRED_COLUMNS),
            "null_count": [
                int(audit.iloc[0][f"null__{column}"])
                for column in REQUIRED_COLUMNS
            ],
        }
    )
    null_columns = [
        column for column in audit.columns if column.startswith("null__")
    ]
    summary = audit.drop(columns=null_columns)
    coverage_dir.mkdir(parents=True, exist_ok=True)
    summary.to_csv(
        coverage_dir / "redfin_zip_source_audit.csv", index=False
    )
    nulls.to_csv(
        coverage_dir / "redfin_zip_source_null_counts.csv", index=False
    )
    return summary.iloc[0].to_dict()


def _coverage_report(wide: pd.DataFrame, coverage_dir: Path) -> None:
    first = wide["period_end"].min()
    latest = wide["period_end"].max()
    expected_periods = (
        (latest.year - first.year) * 12 + latest.month - first.month + 1
    )
    level_columns = list(LEVEL_FIELDS)
    rows: list[dict] = []
    for postal_zip, group in wide.groupby("postal_zip", sort=True):
        observed = group["period_end"].nunique()
        available_metric_count = sum(
            group[column].notna().any() for column in level_columns
        )
        group_latest = group["period_end"].max()
        coverage_share = observed / expected_periods
        eligible = (
            coverage_share >= 0.80
            and group_latest == latest
            and available_metric_count == len(level_columns)
        )
        reasons = []
        if coverage_share < 0.80:
            reasons.append("coverage_below_80pct")
        if group_latest != latest:
            reasons.append("not_current")
        if available_metric_count < len(level_columns):
            reasons.append("fewer_than_6_metrics")
        rows.append(
            {
                "postal_zip": postal_zip,
                "first_period_end": group["period_end"].min(),
                "latest_period_end": group_latest,
                "observed_periods": observed,
                "expected_periods": expected_periods,
                "missing_periods": expected_periods - observed,
                "coverage_share": coverage_share,
                "provider_latest_update": group["last_updated"].max(),
                "available_metric_count": available_metric_count,
                "eligible_for_monitoring": eligible,
                "eligibility_reason": "eligible"
                if eligible
                else "|".join(reasons),
            }
        )
    pd.DataFrame(rows).to_csv(
        coverage_dir / "redfin_zip_coverage.csv", index=False
    )


def _to_observations(
    wide: pd.DataFrame,
    output_path: Path,
) -> pd.DataFrame:
    variable_map = {
        output: (variable_id, unit)
        for output, (_, variable_id, unit) in LEVEL_FIELDS.items()
    }
    variable_map.update(
        {
            output: (variable_id, "percentage_points")
            for output, (_, variable_id) in PROVIDER_YOY_FIELDS.items()
        }
    )
    melted = wide.melt(
        id_vars=[
            "period_begin",
            "period_end",
            "geo_id",
            "primary_county_fips",
            "source_vintage",
            "retrieved_at",
            "source_filename",
        ],
        value_vars=list(variable_map),
        var_name="_metric",
        value_name="value",
    )
    melted["observation_date"] = melted["period_end"]
    melted["source_observation_date"] = melted["period_end"]
    melted["period_start"] = melted["period_begin"]
    melted["state_fips"] = "06"
    melted["county_fips"] = melted["primary_county_fips"]
    melted["variable_id"] = melted["_metric"].map(
        lambda item: variable_map[item][0]
    )
    melted["unit"] = melted["_metric"].map(
        lambda item: variable_map[item][1]
    )
    melted["provider"] = "Redfin Data Center"
    melted["native_frequency"] = "rolling_3_months"
    melted["final_frequency"] = "monthly"
    melted["transformation_id"] = (
        "redfin_zip_key_metrics_rolling_3m_to_long_v1"
    )
    melted["is_imputed"] = False
    melted["is_interpolated"] = False
    melted["is_seasonally_adjusted"] = False
    melted["quality_flag"] = "overlapping_rolling_3_month_window"
    return finalize(melted, output_path)


def clean_redfin_zip_key_metrics(
    path: Path,
    record: dict[str, str],
    geography_path: Path,
    output_path: Path,
    observations_path: Path,
    coverage_dir: Path,
) -> pd.DataFrame:
    sidecar = output_path.with_suffix(".metadata.json")
    required_reports = (
        coverage_dir / "redfin_zip_source_audit.csv",
        coverage_dir / "redfin_zip_source_null_counts.csv",
        coverage_dir / "redfin_zip_coverage.csv",
        coverage_dir / "redfin_zip_unmatched.csv",
    )
    if sidecar.exists() and output_path.exists() and observations_path.exists():
        metadata = pd.read_json(sidecar, typ="series")
        if (
            str(metadata.get("checksum_sha256"))
            == str(record["checksum_sha256"])
            and all(report.exists() for report in required_reports)
        ):
            return pd.read_parquet(observations_path)

    if not geography_path.exists():
        raise FileNotFoundError(
            "Versioned geography reference is required before Redfin ZIP "
            f"cleaning: {geography_path}"
        )
    geography = pd.read_parquet(geography_path)
    eligible = (
        geography.loc[
            geography["county_fips"].astype(str).eq("06037")
            & geography["postal_zip"].notna(),
            ["postal_zip"],
        ]
        .astype({"postal_zip": "string"})
        .drop_duplicates()
    )
    if eligible.empty:
        raise ValueError(
            "No verified Los Angeles County ZIPs in geography reference"
        )

    connection = duckdb.connect()
    connection.register("eligible_zips", eligible)
    source_sql = _read_csv_sql(path)
    actual_columns = [
        row[0]
        for row in connection.execute(
            f"DESCRIBE SELECT * FROM {source_sql}"
        ).fetchall()
    ]
    validate_redfin_zip_schema(actual_columns)
    connection.execute(
        f"CREATE TEMP VIEW redfin_source AS SELECT * FROM {source_sql}"
    )
    audit = _source_audit(connection, coverage_dir)
    if audit["frequency_values"] != "Rolling 3 Months":
        raise ValueError(
            "Unexpected Redfin ZIP frequency values: "
            f"{audit['frequency_values']}"
        )
    if audit["region_type_values"] != "Zip":
        raise ValueError(
            "Unexpected Redfin ZIP region types: "
            f"{audit['region_type_values']}"
        )
    if int(audit["duplicate_zip_period_rows"]) != 0:
        raise ValueError(
            "Redfin source has duplicate ZIP-period rows: "
            f"{audit['duplicate_zip_period_rows']}"
        )

    projections = [
        'lpad(trim(source."REGION NAME"), 5, \'0\') AS postal_zip',
        '\'ZIP_\' || lpad(trim(source."REGION NAME"), 5, \'0\') AS geo_id',
        "'06037' AS primary_county_fips",
        'source."REGION ID" AS region_id',
        'source."REGION TYPE" AS region_type',
        'source."METRO" AS metro',
        'try_cast(source."PERIOD BEGIN" AS DATE) AS period_begin',
        'try_cast(source."PERIOD END" AS DATE) AS period_end',
        'try_cast(source."LAST UPDATED" AS DATE) AS last_updated',
        'source."FREQUENCY" AS frequency',
        "3::INTEGER AS rolling_window_months",
    ]
    projections.extend(
        f'try_cast(source.{_quoted(source_column)} AS DOUBLE) AS {output}'
        for output, (source_column, _, _) in LEVEL_FIELDS.items()
    )
    projections.extend(
        f'try_cast(source.{_quoted(source_column)} AS DOUBLE) AS {output}'
        for output, (source_column, _) in PROVIDER_YOY_FIELDS.items()
    )
    wide = connection.execute(
        f"""
        SELECT {", ".join(projections)}
        FROM redfin_source AS source
        JOIN eligible_zips AS eligible
          ON eligible.postal_zip =
             lpad(trim(source."REGION NAME"), 5, '0')
        """
    ).fetchdf()
    unmatched = connection.execute(
        """
        SELECT lpad(trim(source."REGION NAME"), 5, '0') AS postal_zip,
               count(*) AS source_rows,
               min(try_cast(source."PERIOD END" AS DATE))
                   AS first_period_end,
               max(try_cast(source."PERIOD END" AS DATE))
                   AS latest_period_end,
               'not_in_verified_la_county_zip_reference' AS rejection_reason
        FROM redfin_source AS source
        LEFT JOIN eligible_zips AS eligible
          ON eligible.postal_zip =
             lpad(trim(source."REGION NAME"), 5, '0')
        WHERE eligible.postal_zip IS NULL
        GROUP BY 1
        ORDER BY 1
        """
    ).fetchdf()
    connection.close()

    if wide.empty:
        raise ValueError("No Redfin ZIP rows matched verified LA County ZIPs")
    if wide.duplicated(["postal_zip", "period_end"]).any():
        raise ValueError("Duplicate Redfin LA ZIP-period rows after filtering")
    if not wide["postal_zip"].astype(str).str.fullmatch(r"\d{5}").all():
        raise ValueError("Redfin postal ZIP identifiers must be five digits")
    if not wide["region_type"].eq("Zip").all():
        raise ValueError("Non-ZIP Redfin regions entered the LA subset")
    if not wide["frequency"].eq("Rolling 3 Months").all():
        raise ValueError("Unexpected Redfin frequency entered the LA subset")

    wide["source_vintage"] = str(record["data_vintage"])
    wide["retrieved_at"] = str(record["retrieved_at"])
    wide["source_filename"] = str(record["source_filename"])
    wide["checksum_sha256"] = str(record["checksum_sha256"])
    wide["quality_flag"] = "overlapping_rolling_3_month_window"
    wide = wide.sort_values(["postal_zip", "period_end"])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    wide.to_parquet(output_path, index=False)
    coverage_dir.mkdir(parents=True, exist_ok=True)
    unmatched.to_csv(
        coverage_dir / "redfin_zip_unmatched.csv", index=False
    )
    _coverage_report(wide, coverage_dir)
    observations = _to_observations(wide, observations_path)
    atomic_json(
        sidecar,
        {
            "checksum_sha256": str(record["checksum_sha256"]),
            "source_filename": str(record["source_filename"]),
            "clean_rows": len(wide),
            "fact_observations": len(observations),
        },
    )
    return observations
