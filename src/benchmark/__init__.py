"""Source-faithful Version A housing-finance benchmark calculations."""

from src.benchmark.acs import clean_acs_finance, download_acs_finance_vintage
from src.benchmark.build import (
    build_version_a_benchmark,
    pmms_reference_rate_parameters,
)
from src.benchmark.finance import (
    REQUIRED_BENCHMARK_METADATA_FIELDS,
    BenchmarkMetadataError,
    adjust_pums_housing_dollars,
    annuity_factor,
    calculate_aggregate_mortgage_payment,
    calculate_hmda_downpayment_metrics,
    calculate_matched_universe_equity,
    calculate_pums_first_mortgage_pi,
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
    read_pums_housing,
    read_tract_puma_relationship,
)
from src.benchmark.qa import run_benchmark_qa

__all__ = [
    "REQUIRED_BENCHMARK_METADATA_FIELDS",
    "BenchmarkMetadataError",
    "adjust_pums_housing_dollars",
    "annuity_factor",
    "calculate_aggregate_mortgage_payment",
    "calculate_hmda_downpayment_metrics",
    "calculate_matched_universe_equity",
    "calculate_pums_first_mortgage_pi",
    "calculate_pums_pi_share",
    "calibrate_implied_debt_to_california",
    "implied_debt_from_distribution",
    "implied_debt_scenarios",
    "validate_benchmark_metadata",
    "weighted_pi_share",
    "build_pums_pi_share_parameters",
    "build_version_a_benchmark",
    "clean_acs_finance",
    "clean_hmda",
    "download_acs_finance_vintage",
    "normalize_nmdb_california_upb",
    "normalize_nmdb_joint_distribution",
    "pmms_reference_rate_parameters",
    "read_pums_housing",
    "read_tract_puma_relationship",
    "run_benchmark_qa",
]
