"""Falsification tests for the report-denominator and assignment-count QA.

Feature construction and frozen clustering are tested in test_market_regimes;
these tests fix their classifications to isolate the main QA integration.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.features.market_regimes import (
    APPLICATION_START, FEATURE_COLUMNS, REGIME_LABELS,
    _summary_output, _transition_output,
)
from src.validation import qa


@pytest.fixture
def regime_outputs(monkeypatch):
    rows = []
    for date, identifiers in (
        ("2019-12-31", [1, None, 3, 4]),
        ("2020-03-31", [2, 2, 3, 4]),
        ("2020-06-30", [2, 2, 4, 4]),
    ):
        for zip_number, identifier in enumerate(identifiers):
            rows.append({
                "quarter_end": pd.Timestamp(date), "postal_zip": f"{90001 + zip_number:05d}",
                **{column: float(zip_number + 1) for column in FEATURE_COLUMNS},
                "zip_market_regime_id": identifier,
                "zip_market_regime_label": REGIME_LABELS.get(identifier),
                "zip_market_regime_distance": 0.1 if identifier is not None else np.nan,
                "complete_regime_inputs": identifier is not None,
                "classification_status": "classified" if identifier is not None else "missing_input",
                "source_vintage": "fixture", "retrieved_at": "fixture",
                "source_filename": "fixture", "checksum_sha256": "fixture",
            })
    context = pd.DataFrame(rows)
    context["zip_market_regime_id"] = context["zip_market_regime_id"].astype("Int64")
    quarterly = context[["quarter_end", "postal_zip", *FEATURE_COLUMNS]].copy()
    assignments = context.loc[context.quarter_end.ge(APPLICATION_START)].reset_index(drop=True)
    transitions = _transition_output(context, pd.DatetimeIndex(assignments.quarter_end.unique()))
    summary = _summary_output(assignments, transitions)
    summary["zip_market_regime_share"] = summary["regime_share_of_classified"]
    summary["zip_market_regime_changed_share"] = summary["changed_regime_share_of_classified"]
    summary["zip_market_regime_entry_share"] = summary["entered_regime_share_of_classified"]
    summary["zip_market_regime_classified_coverage"] = summary["classification_coverage_share_of_universe"]
    transitions["zip_market_regime_transition_share"] = transitions["transition_share_of_classified"]

    def classify(inputs, model):
        fields = [column for column in context if column not in FEATURE_COLUMNS]
        return inputs.merge(context[fields], on=["quarter_end", "postal_zip"], validate="one_to_one")

    monkeypatch.setattr(qa, "load_market_regime_model", lambda _: {})
    monkeypatch.setattr(qa, "build_quarterly_regime_inputs", lambda _: quarterly.copy())
    monkeypatch.setattr(qa, "classify_quarterly_regime_inputs", classify)
    return assignments, summary, transitions


def validate(outputs):
    return qa._zip_market_regime_failures(pd.DataFrame(), *outputs, Path("unused-model.json"))


def test_main_qa_accepts_null_canonical_flow_when_prior_classification_missing(regime_outputs):
    _, summary, transitions = regime_outputs
    first = summary.quarter_end.eq(APPLICATION_START)
    assert summary.loc[first, "classified_zip_count"].eq(4).all()
    assert summary.loc[first, "comparable_zip_count"].eq(3).all()
    assert summary.loc[first, "zip_market_regime_changed_share"].isna().all()
    assert summary.loc[first, "changed_regime_share_of_comparable"].eq(100 / 3).all()
    second = summary.quarter_end.eq("2020-06-30")
    assert summary.loc[second, "zip_market_regime_changed_share"].eq(25).all()
    assert validate(regime_outputs).empty


@pytest.mark.parametrize("column,diagnostic", [
    ("zip_market_regime_changed_share", "changed_regime_share_of_comparable"),
    ("zip_market_regime_entry_share", "entered_regime_share_of_comparable"),
    ("changed_regime_share_of_classified", "changed_regime_share_of_comparable"),
    ("entered_regime_share_of_classified", "entered_regime_share_of_comparable"),
    ("exited_regime_share_of_classified", "exited_regime_share_of_comparable"),
])
def test_main_qa_rejects_comparable_denominator_under_canonical_summary_name(regime_outputs, column, diagnostic):
    _, summary, _ = regime_outputs
    missing_prior = summary.quarter_end.eq(APPLICATION_START)
    summary.loc[missing_prior, column] = summary.loc[missing_prior, diagnostic]
    failures = validate(regime_outputs)
    assert column in set(failures.variable.dropna())


@pytest.mark.parametrize("column", ["zip_market_regime_transition_share", "transition_share_of_classified"])
def test_main_qa_rejects_published_transition_matrix_with_missing_prior(regime_outputs, column):
    _, _, transitions = regime_outputs
    missing_prior = transitions.quarter_end.eq(APPLICATION_START)
    transitions.loc[missing_prior, column] = transitions.loc[missing_prior, "transition_share_of_comparable"]
    failures = validate(regime_outputs)
    assert column in set(failures.variable.dropna())


@pytest.mark.parametrize("field,value", [
    ("classified_zip_count", 3),
    ("comparable_zip_count", 4),
    ("missing_previous_classification_count", 0),
    ("changed_regime_zip_count", 0),
])
def test_main_qa_reconstructs_counts_from_assignments_not_summary(regime_outputs, field, value):
    _, summary, _ = regime_outputs
    summary.loc[summary.quarter_end.eq(APPLICATION_START), field] = value
    failures = validate(regime_outputs)
    assert field in set(failures.variable.dropna())


def test_main_qa_rejects_false_complete_denominator_status(regime_outputs):
    _, summary, transitions = regime_outputs
    summary.loc[summary.quarter_end.eq(APPLICATION_START), "transition_denominator_status"] = "complete"
    transitions.loc[transitions.quarter_end.eq(APPLICATION_START), "transition_denominator_status"] = "complete"
    failures = validate(regime_outputs)
    assert "regime_summary_transition_denominator_status_mismatch" in set(failures.failure.dropna())
    assert "regime_transition_transition_denominator_status_mismatch" in set(failures.failure.dropna())


def test_main_qa_rejects_transition_counts_with_correct_total_but_wrong_cells(regime_outputs):
    _, _, transitions = regime_outputs
    quarter = transitions.quarter_end.eq("2020-06-30")
    occupied = quarter & transitions.from_regime_id.eq(2) & transitions.to_regime_id.eq(2)
    wrong = quarter & transitions.from_regime_id.eq(2) & transitions.to_regime_id.eq(3)
    transitions.loc[occupied, "transition_zip_count"] -= 1
    transitions.loc[wrong, "transition_zip_count"] += 1
    for column in ("transition_share_of_classified", "transition_share_of_comparable", "zip_market_regime_transition_share"):
        transitions.loc[quarter, column] = 100 * transitions.loc[quarter, "transition_zip_count"] / 4
    failures = validate(regime_outputs)
    assert "transition_zip_count" in set(failures.variable.dropna())
