from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from src.features.market_regimes import (
    APPLICATION_START,
    FEATURE_COLUMNS,
    MODEL_SCHEMA_VERSION,
    MODEL_VERSION,
    PRICE_MOMENTUM,
    PRICE_TO_RENT,
    PRICE_VOLATILITY,
    REGIME_LABELS,
    RENT_MOMENTUM,
    TRAINING_END,
    TRAINING_START,
    _summary_output,
    _transition_output,
    build_quarterly_regime_inputs,
    build_zip_market_regimes,
    load_market_regime_model,
)


def _synthetic_monthly_panel() -> pd.DataFrame:
    dates = pd.date_range("2013-01-31", "2021-12-31", freq="ME")
    profiles = (
        # monthly price drift, rent drift, price volatility, initial PTR
        (0.0020, 0.0010, 0.0008, 25.0),
        (0.0030, 0.0080, 0.0020, 27.0),
        (-0.0020, 0.0030, 0.0100, 36.0),
        (0.0100, 0.0040, 0.0070, 46.0),
    )
    rows: list[dict[str, object]] = []
    for profile_number, (price_drift, rent_drift, volatility, ptr) in enumerate(
        profiles
    ):
        for member in range(3):
            postal_zip = f"{90001 + profile_number * 10 + member:05d}"
            month = np.arange(len(dates), dtype="float64")
            phase = member * 0.7 + profile_number * 0.2
            price_changes = (
                price_drift
                + volatility * np.sin(month * 0.73 + phase)
                + member * 0.00008
            )
            rent_changes = (
                rent_drift
                + volatility * 0.25 * np.cos(month * 0.41 + phase)
                + member * 0.00004
            )
            initial_rent = 1_700.0 + 30.0 * member
            initial_price = initial_rent * 12.0 * ptr
            prices = initial_price * np.exp(np.cumsum(price_changes))
            rents = initial_rent * np.exp(np.cumsum(rent_changes))
            rows.extend(
                {
                    "date": date,
                    "postal_zip": postal_zip,
                    "zhvi": price,
                    "zori": rent,
                }
                for date, price, rent in zip(dates, prices, rents, strict=True)
            )
    return pd.DataFrame(rows)


def test_quarterly_inputs_use_exact_calendar_lags_and_full_24_month_window():
    dates = pd.date_range("2013-01-31", "2021-12-31", freq="ME")
    month = np.arange(len(dates), dtype="float64")
    price_log = np.log(500_000.0) + np.cumsum(
        0.004 + 0.002 * np.sin(month * 0.37)
    )
    rent_log = np.log(2_000.0) + np.cumsum(
        0.003 + 0.001 * np.cos(month * 0.29)
    )
    panel = pd.DataFrame(
        {
            "date": dates,
            "postal_zip": "90001",
            "zhvi": np.exp(price_log),
            "zori": np.exp(rent_log),
        }
    )

    quarterly = build_quarterly_regime_inputs(panel)
    target = quarterly.loc[quarterly["quarter_end"].eq("2015-06-30")].iloc[0]
    indexed = panel.set_index("date")
    expected_price_momentum = 400.0 * (
        np.log(indexed.loc["2015-06-30", "zhvi"])
        - np.log(indexed.loc["2015-03-31", "zhvi"])
    )
    expected_rent_momentum = 400.0 * (
        np.log(indexed.loc["2015-06-30", "zori"])
        - np.log(indexed.loc["2015-03-31", "zori"])
    )
    monthly_growth = np.log(indexed["zhvi"]).diff()
    expected_volatility = monthly_growth.loc[
        "2013-07-31":"2015-06-30"
    ].std(ddof=0)
    assert target[PRICE_MOMENTUM] == pytest.approx(expected_price_momentum)
    assert target[RENT_MOMENTUM] == pytest.approx(expected_rent_momentum)
    assert target[PRICE_TO_RENT] == pytest.approx(
        indexed.loc["2015-06-30", "zhvi"]
        / (12.0 * indexed.loc["2015-06-30", "zori"])
    )
    assert target[PRICE_VOLATILITY] == pytest.approx(expected_volatility)

    with_gap = panel.loc[~panel["date"].eq("2018-06-30")].copy()
    gap_inputs = build_quarterly_regime_inputs(with_gap).set_index("quarter_end")
    assert pd.isna(gap_inputs.loc[pd.Timestamp("2018-09-30"), PRICE_MOMENTUM])
    assert pd.isna(gap_inputs.loc[pd.Timestamp("2018-09-30"), PRICE_VOLATILITY])
    assert pd.notna(gap_inputs.loc[pd.Timestamp("2020-09-30"), PRICE_VOLATILITY])


def test_model_is_frozen_reloaded_and_classification_uses_nearest_center(tmp_path):
    panel = _synthetic_monthly_panel()
    model_path = tmp_path / "zip_market_regime_model_v2.json"
    first = build_zip_market_regimes(panel, model_path)
    frozen_bytes = model_path.read_bytes()
    model = load_market_regime_model(model_path)

    assert model["training_start"] == TRAINING_START.date().isoformat()
    assert model["training_end"] == TRAINING_END.date().isoformat()
    assert model["schema_version"] == MODEL_SCHEMA_VERSION == 2
    assert model["model_version"] == MODEL_VERSION
    assert model["training_quarter_count"] == 19
    assert model["n_clusters"] == 4
    assert {item["regime_label"] for item in model["regimes"]} == set(
        REGIME_LABELS.values()
    )
    assert first.assignments["quarter_end"].min() == APPLICATION_START
    assert first.assignments["complete_regime_inputs"].all()
    assert set(first.assignments["zip_market_regime_label"]) == set(
        REGIME_LABELS.values()
    )

    means = np.asarray([model["scaler_mean"][item] for item in FEATURE_COLUMNS])
    scales = np.asarray([model["scaler_scale"][item] for item in FEATURE_COLUMNS])
    centers = np.asarray(
        [
            [regime["center_standardized"][item] for item in FEATURE_COLUMNS]
            for regime in model["regimes"]
        ]
    )
    values = first.assignments.loc[:, FEATURE_COLUMNS].to_numpy(dtype="float64")
    standardized = (values - means) / scales
    expected_ids = (
        ((standardized[:, None, :] - centers[None, :, :]) ** 2)
        .sum(axis=2)
        .argmin(axis=1)
        + 1
    )
    np.testing.assert_array_equal(
        first.assignments["zip_market_regime_id"].to_numpy(), expected_ids
    )

    # Changing historical training inputs must not refit or overwrite an
    # existing frozen model.  These early changes do not affect 2019Q4+ inputs.
    revised = panel.copy()
    revised.loc[revised["date"].lt("2017-01-01"), "zhvi"] *= 1.25
    second = build_zip_market_regimes(revised, model_path)
    assert model_path.read_bytes() == frozen_bytes
    pd.testing.assert_series_equal(
        first.assignments["zip_market_regime_id"],
        second.assignments["zip_market_regime_id"],
    )


def test_summary_and_transitions_expose_coverage_and_comparable_denominators(
    tmp_path,
):
    panel = _synthetic_monthly_panel()
    # The missing current-quarter rent makes one ZIP ineligible while keeping
    # it in the explicit ZIP universe denominator.
    panel.loc[
        panel["postal_zip"].eq("90001")
        & panel["date"].eq("2020-03-31"),
        "zori",
    ] = np.nan
    outputs = build_zip_market_regimes(
        panel, tmp_path / "zip_market_regime_model_v2.json"
    )

    first_quarter = outputs.summary.loc[
        outputs.summary["quarter_end"].eq(APPLICATION_START)
    ]
    assert len(first_quarter) == 4
    assert first_quarter["universe_zip_count"].eq(12).all()
    assert first_quarter["classified_zip_count"].eq(11).all()
    assert first_quarter[
        "classification_coverage_share_of_universe"
    ].eq(100.0 * 11 / 12).all()
    assert first_quarter["regime_share_of_classified"].sum() == pytest.approx(100.0)

    assignment = outputs.assignments.loc[
        outputs.assignments["quarter_end"].eq(APPLICATION_START)
        & outputs.assignments["postal_zip"].eq("90001")
    ].iloc[0]
    assert not assignment["complete_regime_inputs"]
    assert assignment["classification_status"] == "missing_input"
    assert RENT_MOMENTUM in assignment["missing_regime_inputs"]
    assert PRICE_TO_RENT in assignment["missing_regime_inputs"]

    transitions = outputs.transitions.loc[
        outputs.transitions["quarter_end"].eq(APPLICATION_START)
    ]
    assert len(transitions) == 16
    assert transitions["previous_quarter_end"].eq("2019-12-31").all()
    assert transitions["comparable_zip_count"].eq(11).all()
    assert transitions["transition_zip_count"].sum() == 11
    assert transitions["transition_share_of_comparable"].sum() == pytest.approx(100.0)
    assert first_quarter["comparable_zip_count"].eq(11).all()
    expected_changed_share = (
        100.0
        * transitions["changed_regime_zip_count"].iloc[0]
        / transitions["comparable_zip_count"].iloc[0]
    )
    assert first_quarter["changed_regime_share_of_comparable"].eq(
        expected_changed_share
    ).all()
    assert first_quarter["entered_regime_share_of_comparable"].sum() == pytest.approx(
        expected_changed_share
    )
    assert first_quarter["exited_regime_share_of_comparable"].sum() == pytest.approx(
        expected_changed_share
    )

    second_quarter = outputs.transitions.loc[
        outputs.transitions["quarter_end"].eq("2020-06-30")
    ]
    assert second_quarter["previous_quarter_end"].eq("2020-03-31").all()
    assert second_quarter["comparable_zip_count"].eq(11).all()


def test_model_fingerprint_rejects_tampering(tmp_path):
    model_path = tmp_path / "zip_market_regime_model_v2.json"
    build_zip_market_regimes(_synthetic_monthly_panel(), model_path)
    model = json.loads(model_path.read_text(encoding="utf-8"))
    model["regimes"][0]["center_standardized"][PRICE_MOMENTUM] += 1.0
    model_path.write_text(json.dumps(model), encoding="utf-8")
    with pytest.raises(ValueError, match="fingerprint"):
        load_market_regime_model(model_path)


@pytest.mark.parametrize(
    "previous_ids,current_ids,status,expected_classified,expected_comparable",
    [
        ([1, 3], [2, 3], "complete", 2, 2),
        ([1, None], [2, 3], "incomplete_previous_quarter", 2, 1),
        ([None, None], [2, 3], "incomplete_previous_quarter", 2, 0),
        ([1, 3], [None, None], "no_current_classifications", 0, 0),
        # A ZIP that becomes unclassified now is excluded from current N_t;
        # it must not make the remaining current population incomplete.
        ([1, 3], [2, None], "complete", 1, 1),
    ],
)
def test_document_transition_denominator_preserves_unknown_prior_regimes(
    previous_ids, current_ids, status, expected_classified, expected_comparable
):
    previous_quarter = pd.Timestamp("2019-12-31")
    current_quarter = pd.Timestamp("2020-03-31")
    rows = []
    for quarter, ids in [(previous_quarter, previous_ids), (current_quarter, current_ids)]:
        for zipcode, regime in zip(["90001", "90002"], ids):
            rows.append({
                "quarter_end": quarter,
                "postal_zip": zipcode,
                "complete_regime_inputs": regime is not None,
                "zip_market_regime_id": regime,
                "zip_market_regime_label": REGIME_LABELS.get(regime),
            })
    context = pd.DataFrame(rows)
    transitions = _transition_output(context, pd.DatetimeIndex([current_quarter]))
    summary = _summary_output(context.loc[context.quarter_end.eq(current_quarter)], transitions)

    assert len(transitions) == 16
    assert len(summary) == 4
    for frame in [transitions, summary]:
        assert frame.classified_zip_count.eq(expected_classified).all()
        assert frame.comparable_zip_count.eq(expected_comparable).all()
        assert frame.missing_previous_classification_count.eq(
            expected_classified - expected_comparable
        ).all()
        assert frame.transition_denominator_status.eq(status).all()

    if status == "complete":
        assert transitions.transition_share_of_classified.sum() == pytest.approx(100)
        assert summary.regime_share_of_classified.sum() == pytest.approx(100)
        assert summary.changed_regime_share_of_classified.eq(
            100 / expected_classified
        ).all()
        assert summary.entered_regime_share_of_classified.sum() == pytest.approx(
            100 / expected_classified
        )
        assert summary.exited_regime_share_of_classified.sum() == pytest.approx(
            100 / expected_classified
        )
    else:
        assert transitions.transition_share_of_classified.isna().all()
        assert transitions.changed_regime_share_of_classified.isna().all()
        assert summary.changed_regime_share_of_classified.isna().all()
        assert summary.entered_regime_share_of_classified.isna().all()
        assert summary.exited_regime_share_of_classified.isna().all()

    # Conditional percentages remain useful but retain their explicit name;
    # they are never substituted for the document's full-N_t variables.
    if expected_comparable:
        assert transitions.transition_share_of_comparable.sum() == pytest.approx(100)
        assert summary.changed_regime_share_of_comparable.eq(
            100 / expected_comparable
        ).all()
    else:
        assert transitions.transition_share_of_comparable.isna().all()
        assert summary.changed_regime_share_of_comparable.isna().all()


def test_missing_prior_momentum_does_not_remove_current_zip_from_n_t(tmp_path):
    panel = _synthetic_monthly_panel()
    # Missing September rent blocks December's three-month momentum but not
    # March's. That current ZIP still belongs in N_t even though its transition
    # cannot be determined; neither its change nor its origin may be invented.
    panel.loc[
        panel.postal_zip.eq("90001") & panel.date.eq("2019-09-30"), "zori"
    ] = np.nan
    outputs = build_zip_market_regimes(panel, tmp_path / "frozen_model.json")
    current = outputs.summary.loc[outputs.summary.quarter_end.eq(APPLICATION_START)]
    assert current.classified_zip_count.eq(12).all()
    assert current.comparable_zip_count.eq(11).all()
    assert current.missing_previous_classification_count.eq(1).all()
    assert current.transition_denominator_status.eq("incomplete_previous_quarter").all()
    assert current.regime_share_of_classified.sum() == pytest.approx(100)
    assert current.changed_regime_share_of_classified.isna().all()
    assert current.entered_regime_share_of_classified.isna().all()
    assert current.changed_regime_share_of_comparable.notna().all()
