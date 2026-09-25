from src.common.config import load_config
from src.geography.comparable_counties import COMPARABLE_LAUS_SERIES_IDS


def test_config_has_unique_sources_and_la_geography():
    config = load_config()
    assert len(config.sources) == len({source.source_id for source in config.sources})
    assert any(item["geo_id"] == "COUNTY_06037" for item in config.geographies)
    assert all(source.download_url.startswith("https://") for source in config.sources)
    source_ids = {source.source_id for source in config.sources}
    assert {
        "zillow_county_zori_smoothed_sa",
        "zillow_zip_zori_smoothed_sa",
    }.issubset(source_ids)
    assert {
        "zillow_county_zori",
        "zillow_zip_zori",
        "zillow_county_zori_smoothed_nsa",
        "zillow_zip_zori_smoothed_nsa",
    }.isdisjoint(source_ids)
    redfin_zip = next(
        source
        for source in config.sources
        if source.source_id == "redfin_zip_monthly"
    )
    assert redfin_zip.auto_download
    assert redfin_zip.access_method == "official_browser_download"
    assert not redfin_zip.selected_end_date
    assert "redfin_zip_monthly" in config.pipeline["source_refresh_review_holds"]
    assert redfin_zip.native_frequency == "rolling_3_months"
    assert redfin_zip.rolling_window_months == 3
    assert len(redfin_zip.expected_columns) == 20
    bls = next(
        source
        for source in config.sources
        if source.source_id == "bls_laus_la_county"
    )
    assert bls.download_url == (
        "https://api.bls.gov/publicAPI/v2/timeseries/data/"
    )
    assert bls.series_ids == COMPARABLE_LAUS_SERIES_IDS
