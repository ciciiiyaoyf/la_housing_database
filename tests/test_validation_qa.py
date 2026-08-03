import pytest

from src.validation.qa import _classify_geo_type


@pytest.mark.parametrize(
    ("geo_id", "expected"),
    [
        ("US", "national"),
        ("STATE_06", "state"),
        ("COUNTY_06037", "county"),
        ("ZIP_90001", "postal_zip"),
        ("ZCTA_90001", "zcta"),
        ("METRO_31080", "metro"),
        ("METRODIV_31084", "metro_division"),
        ("BOGUS_1", "unknown"),
    ],
)
def test_classify_geo_type_preserves_supported_geographies(
    geo_id: str, expected: str
) -> None:
    assert _classify_geo_type(geo_id) == expected
