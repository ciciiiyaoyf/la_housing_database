from dataclasses import replace

import pytest

from src.common.config import load_config
from src.cleaning.redfin_zip import OFFICIAL_COLUMNS
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
    assert redfin_zip.access_method == "official_public_download"
    assert not redfin_zip.selected_end_date
    assert "redfin_county_monthly" in config.pipeline["source_refresh_review_holds"]
    assert redfin_zip.native_frequency == "rolling_3_months"
    assert redfin_zip.rolling_window_months == 3
    assert redfin_zip.expected_columns == OFFICIAL_COLUMNS
    bls = next(
        source
        for source in config.sources
        if source.source_id == "bls_laus_la_county"
    )
    assert bls.download_url == (
        "https://api.bls.gov/publicAPI/v2/timeseries/data/"
    )
    assert bls.series_ids == COMPARABLE_LAUS_SERIES_IDS


def test_redfin_group_selects_only_county_and_zip_without_changing_other_selection():
    config = load_config()
    selected = config.selected_sources("redfin")
    assert [source.source_id for source in selected] == [
        "redfin_county_monthly", "redfin_zip_monthly"
    ]
    assert config.selected_sources("redfin_zip_monthly") == (selected[1],)
    assert config.selected_sources() == tuple(source for source in config.sources if source.enabled)


@pytest.mark.parametrize("members", [
    [], None, "redfin_zip_monthly", [""], [1],
    ["redfin_zip_monthly", "redfin_zip_monthly"], ["unknown_source"],
])
def test_invalid_source_group_is_rejected_without_partial_selection(members):
    config = load_config()
    config = replace(config, pipeline={**config.pipeline, "source_groups": {"redfin": members}})
    with pytest.raises(ValueError, match="Source group 'redfin'"):
        config.selected_sources("redfin")


def test_disabled_group_member_is_rejected_but_all_still_excludes_disabled_sources():
    config = load_config()
    config = replace(config, sources=tuple(
        replace(source, enabled=False) if source.source_id == "redfin_county_monthly" else source
        for source in config.sources
    ))
    with pytest.raises(ValueError, match="disabled members: redfin_county_monthly"):
        config.selected_sources("redfin")
    assert all(source.enabled for source in config.selected_sources())
    assert len(config.selected_sources("redfin_zip_monthly")) == 1


@pytest.mark.parametrize("groups", [[], {1: ["redfin_zip_monthly"]}, {"": []}])
def test_invalid_source_group_mapping_is_rejected(groups):
    config = load_config()
    config = replace(config, pipeline={**config.pipeline, "source_groups": groups})
    with pytest.raises(ValueError, match="source_groups"):
        config.selected_sources("redfin")


def test_source_group_cannot_shadow_an_individual_source():
    config = load_config()
    config = replace(config, pipeline={
        **config.pipeline, "source_groups": {"redfin_zip_monthly": ["redfin_county_monthly"]}
    })
    with pytest.raises(ValueError, match="conflicts with a source_id"):
        config.selected_sources("redfin_zip_monthly")
