"""Source-faithful Version A housing-finance benchmark calculations."""

from src.benchmark.acs import clean_acs_finance, download_acs_finance_vintage
from src.benchmark.build import (
    build_version_a_benchmark,
    pmms_reference_rate_parameters,
)
from src.benchmark.cpi import (
    CPIFormatError,
    CPI_U_BASE_YEAR,
    CPI_U_SERIES_ID,
    clean_bls_cpi_u_annual,
    cpi_u_annual_index,
)
from src.benchmark.finance import (
    REQUIRED_BENCHMARK_METADATA_FIELDS,
    BenchmarkMetadataError,
    adjust_pums_housing_dollars,
    annuity_factor,
    calculate_aggregate_mortgage_payment,
    calculate_debt_normalizations,
    calculate_hmda_downpayment_metrics,
    calculate_matched_universe_equity,
    calculate_pums_first_mortgage_pi,
    calculate_pums_junior_lien_payment,
    calculate_pums_pi_share,
    calibrate_implied_debt_to_california,
    implied_debt_from_distribution,
    implied_debt_scenarios,
    validate_benchmark_metadata,
    weighted_pi_share,
)
from src.benchmark.hmda import clean_hmda
from src.benchmark.nmdb import (
    normalize_nmdb_california_upb,
    normalize_nmdb_joint_distribution,
)
from src.benchmark.pums import (
    build_pums_pi_share_parameters,
    derive_county_puma_pool_hierarchy,
    read_pums_housing,
    read_tract_puma_relationship,
    resolve_pums_geography_contract,
)
from src.benchmark.qa import run_benchmark_qa

__all__ = [
    "REQUIRED_BENCHMARK_METADATA_FIELDS",
    "BenchmarkMetadataError",
    "CPIFormatError",
    "CPI_U_BASE_YEAR",
    "CPI_U_SERIES_ID",
    "adjust_pums_housing_dollars",
    "annuity_factor",
    "calculate_aggregate_mortgage_payment",
    "calculate_debt_normalizations",
    "calculate_hmda_downpayment_metrics",
    "calculate_matched_universe_equity",
    "calculate_pums_first_mortgage_pi",
    "calculate_pums_junior_lien_payment",
    "calculate_pums_pi_share",
    "calibrate_implied_debt_to_california",
    "implied_debt_from_distribution",
    "implied_debt_scenarios",
    "validate_benchmark_metadata",
    "weighted_pi_share",
    "build_pums_pi_share_parameters",
    "build_version_a_benchmark",
    "clean_bls_cpi_u_annual",
    "clean_acs_finance",
    "clean_hmda",
    "cpi_u_annual_index",
    "download_acs_finance_vintage",
    "derive_county_puma_pool_hierarchy",
    "normalize_nmdb_california_upb",
    "normalize_nmdb_joint_distribution",
    "pmms_reference_rate_parameters",
    "read_pums_housing",
    "read_tract_puma_relationship",
    "resolve_pums_geography_contract",
    "run_benchmark_qa",
]
