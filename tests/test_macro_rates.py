import pandas as pd
import pytest

from src.cleaning.macro_rates import clean_macro_rates


def record():
    return dict(source_id="fred_dgs2", data_vintage="2026-09-15", retrieved_at="2026-09-15T10:00:00+00:00", source_filename="official.csv", checksum_sha256="a" * 64)


def test_daily_rates_preserve_nulls_and_exclude_partial_month(tmp_path):
    path = tmp_path / "rates.csv"
    path.write_text("observation_date,DGS2\n2026-08-03,3\n2026-08-04,.\n2026-08-05,5\n2026-09-01,9\n")
    output = clean_macro_rates(path, record(), "fred_dgs2", tmp_path / "out.parquet")
    monthly = output.loc[output.final_frequency.eq("monthly")]
    assert set(monthly.observation_date) == {pd.Timestamp("2026-08-31")}
    assert monthly.loc[monthly.variable_id.str.endswith("average"), "value"].item() == 4
    assert monthly.loc[monthly.variable_id.str.endswith("count"), "value"].item() == 2
    assert output.loc[output.variable_id.eq("us_treasury_2y_yield_daily"), "value"].isna().sum() == 1
    assert output.geo_id.eq("US").all()
    assert not output.is_interpolated.any()


def test_fedfunds_is_published_monthly_not_summed(tmp_path):
    path = tmp_path / "rates.csv"
    path.write_text("observation_date,FEDFUNDS\n2026-07-01,4.5\n2026-08-01,4.25\n")
    result = clean_macro_rates(path, {**record(), "source_id": "fred_fedfunds"}, "fred_fedfunds", tmp_path / "out.parquet")
    assert result.value.tolist() == [4.5, 4.25]
    assert result.observation_date.dt.is_month_end.all()
    assert result.native_frequency.eq("monthly").all()


@pytest.mark.parametrize("payload", ["observation_date,DGS2\n2026-08-03,3\n2026-08-03,4\n", "observation_date,DGS2\n2026-08-03,nonsense\n", "observation_date,DGS2\n2026-10-01,3\n", "<html>blocked</html>"])
def test_bad_rate_payload_stops_cleaning(tmp_path, payload):
    path = tmp_path / "rates.csv"
    path.write_text(payload)
    with pytest.raises((ValueError, KeyError)):
        clean_macro_rates(path, record(), "fred_dgs2", tmp_path / "out.parquet")


@pytest.mark.parametrize("token", ["inf", "-inf"])
def test_nonfinite_rate_stops_cleaning(tmp_path, token):
    path = tmp_path / "rates.csv"
    path.write_text(f"observation_date,DGS2\n2026-08-03,{token}\n")
    with pytest.raises(ValueError, match="nonfinite"):
        clean_macro_rates(path, record(), "fred_dgs2", tmp_path / "out.parquet")
