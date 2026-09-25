"""Deterministic LA postal-ZIP housing-market regime classification.

The production definition is intentionally narrow: construct quarterly inputs
from monthly Zillow fields, fit four clusters over 2015Q2--2019Q4 once, freeze
the fitted scaler and centers in JSON, and use those fixed definitions for
2020-and-later classifications.  Missing source observations are never filled.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, NamedTuple

import numpy as np
import pandas as pd


TRAINING_START = pd.Timestamp("2015-06-30")
TRAINING_END = pd.Timestamp("2019-12-31")
APPLICATION_START = pd.Timestamp("2020-03-31")
N_CLUSTERS = 4
MODEL_SCHEMA_VERSION = 2
MODEL_VERSION = "zip_market_regime_k4_fixed_2015q2_2019q4_v2"
RANDOM_SEED = 20260824
N_INIT = 50
MAX_ITERATIONS = 500
CONVERGENCE_TOLERANCE = 1e-10

PRICE_MOMENTUM = "zhvi_momentum_3m_annualized_log_pct"
RENT_MOMENTUM = "zori_momentum_3m_annualized_log_pct"
PRICE_TO_RENT = "price_to_rent_ratio_zillow"
PRICE_VOLATILITY = "zhvi_monthly_log_growth_volatility_24m"
FEATURE_COLUMNS = (
    PRICE_MOMENTUM,
    RENT_MOMENTUM,
    PRICE_TO_RENT,
    PRICE_VOLATILITY,
)
ZSCORE_COLUMNS = tuple(f"{column}_z_fixed" for column in FEATURE_COLUMNS)

# IDs are semantic and therefore remain stable even when the raw K-means
# cluster numbering changes during the one-time fit.
REGIME_LABELS = {
    1: "stable/low-volatility",
    2: "rent-led pressure",
    3: "cooling/high-volatility",
    4: "high-growth/high-valuation",
}


class MarketRegimeOutputs(NamedTuple):
    """Three analysis-ready outputs returned by :func:`build_zip_market_regimes`."""

    assignments: pd.DataFrame
    summary: pd.DataFrame
    transitions: pd.DataFrame


def _positive_numeric(series: pd.Series) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce").to_numpy(
        dtype="float64", na_value=np.nan
    )
    result = pd.Series(values, index=series.index, dtype="float64")
    return result.where(result.gt(0))


def _required_training_quarters() -> pd.DatetimeIndex:
    periods = pd.period_range(
        TRAINING_START.to_period("Q"), TRAINING_END.to_period("Q"), freq="Q"
    )
    return periods.to_timestamp(how="end").normalize()


def build_quarterly_regime_inputs(zip_monthly_panel: pd.DataFrame) -> pd.DataFrame:
    """Construct the four required quarter-end inputs without filling gaps.

    Three-month momentum uses an exact calendar lag and is expressed as an
    annualized log percentage: ``400 * (ln(X_t) - ln(X_t-3))``.  Volatility is
    the population standard deviation of the latest 24 *consecutive* monthly
    log ZHVI changes.  Reindexing each ZIP to a monthly calendar
    makes a missing source month break both calculations rather than allowing
    a row shift to bridge the gap.
    """

    required = {"date", "postal_zip", "zhvi", "zori"}
    missing = sorted(required - set(zip_monthly_panel.columns))
    if missing:
        raise ValueError(f"ZIP monthly panel is missing required columns: {missing}")

    panel = zip_monthly_panel.loc[:, sorted(required)].copy()
    panel["date"] = pd.to_datetime(panel["date"], errors="coerce")
    if panel["date"].isna().any():
        raise ValueError("ZIP monthly panel contains an invalid or missing date")
    panel["date"] = panel["date"].dt.to_period("M").dt.to_timestamp("M")

    panel["postal_zip"] = panel["postal_zip"].astype("string").str.strip()
    valid_zip = panel["postal_zip"].str.fullmatch(r"\d{5}").fillna(False)
    if not valid_zip.all():
        invalid = sorted(panel.loc[~valid_zip, "postal_zip"].astype(str).unique())
        raise ValueError(f"Invalid postal ZIP identifiers: {invalid[:10]}")
    duplicate = panel.duplicated(["postal_zip", "date"], keep=False)
    if duplicate.any():
        keys = panel.loc[duplicate, ["postal_zip", "date"]].head(10)
        raise ValueError(
            "ZIP monthly panel must have one row per postal ZIP and month; "
            f"duplicates include {keys.to_dict(orient='records')}"
        )

    parts: list[pd.DataFrame] = []
    for postal_zip, group in panel.groupby("postal_zip", sort=True):
        ordered = group.sort_values("date").set_index("date")
        calendar = pd.date_range(
            ordered.index.min(), ordered.index.max(), freq="ME"
        )
        ordered = ordered.reindex(calendar)
        price = _positive_numeric(ordered["zhvi"])
        rent = _positive_numeric(ordered["zori"])
        log_price = np.log(price)
        log_rent = np.log(rent)
        monthly_price_growth = log_price - log_price.shift(1)

        constructed = pd.DataFrame(index=calendar)
        constructed["postal_zip"] = str(postal_zip)
        constructed[PRICE_MOMENTUM] = 400.0 * (
            log_price - log_price.shift(3)
        )
        constructed[RENT_MOMENTUM] = 400.0 * (
            log_rent - log_rent.shift(3)
        )
        constructed[PRICE_TO_RENT] = price / (12.0 * rent)
        constructed[PRICE_VOLATILITY] = monthly_price_growth.rolling(
            24, min_periods=24
        ).std(ddof=0)
        constructed = constructed.loc[constructed.index.month.isin((3, 6, 9, 12))]
        constructed.index.name = "quarter_end"
        parts.append(constructed.reset_index())

    if not parts:
        return pd.DataFrame(columns=["quarter_end", "postal_zip", *FEATURE_COLUMNS])
    return (
        pd.concat(parts, ignore_index=True)
        .sort_values(["quarter_end", "postal_zip"])
        .reset_index(drop=True)
    )


def _squared_distances(values: np.ndarray, centers: np.ndarray) -> np.ndarray:
    return ((values[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2)


def _kmeans_plus_plus(
    values: np.ndarray, rng: np.random.Generator
) -> np.ndarray:
    first = int(rng.integers(0, len(values)))
    centers = [values[first].copy()]
    while len(centers) < N_CLUSTERS:
        distances = _squared_distances(values, np.asarray(centers)).min(axis=1)
        total = float(distances.sum())
        if not np.isfinite(total) or total <= 0:
            raise ValueError("Training inputs do not contain four distinct clusters")
        next_index = int(rng.choice(len(values), p=distances / total))
        centers.append(values[next_index].copy())
    return np.asarray(centers, dtype="float64")


def _fit_deterministic_kmeans(
    values: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, float, int]:
    """Fit Lloyd K-means with deterministic seeded K-means++ restarts."""

    if values.ndim != 2 or values.shape[1] != len(FEATURE_COLUMNS):
        raise ValueError("K-means training matrix has an invalid shape")
    if len(values) < N_CLUSTERS:
        raise ValueError("At least four complete observations are required")
    if not np.isfinite(values).all():
        raise ValueError("K-means training matrix must be finite")

    seed_generator = np.random.default_rng(RANDOM_SEED)
    best: tuple[np.ndarray, np.ndarray, float, int] | None = None
    for _ in range(N_INIT):
        child_seed = int(seed_generator.integers(0, np.iinfo(np.uint32).max))
        centers = _kmeans_plus_plus(values, np.random.default_rng(child_seed))
        iterations = 0
        for iterations in range(1, MAX_ITERATIONS + 1):
            distances = _squared_distances(values, centers)
            labels = distances.argmin(axis=1)
            updated = centers.copy()
            for cluster in range(N_CLUSTERS):
                members = values[labels == cluster]
                if len(members):
                    updated[cluster] = members.mean(axis=0)
                else:
                    # Deterministically split off the point farthest from its
                    # current center.  np.argmax resolves ties by row order.
                    farthest = int(distances.min(axis=1).argmax())
                    updated[cluster] = values[farthest]
            shift = float(np.sqrt(((updated - centers) ** 2).sum(axis=1)).max())
            centers = updated
            if shift <= CONVERGENCE_TOLERANCE:
                break
        final_distances = _squared_distances(values, centers)
        labels = final_distances.argmin(axis=1)
        inertia = float(final_distances[np.arange(len(values)), labels].sum())
        candidate = (centers, labels, inertia, iterations)
        if best is None or inertia < best[2] - 1e-12:
            best = candidate
    if best is None:  # pragma: no cover - N_INIT is a positive module constant
        raise RuntimeError("K-means produced no fitted model")
    return best


def _semantic_cluster_mapping(centers: np.ndarray) -> dict[int, int]:
    """Map raw clusters to the four document labels from fitted profiles.

    Labels are assigned only after fitting.  The lowest-volatility center is
    stable; the strongest combination of cooling price momentum and volatility
    is cooling; the largest rent-minus-price momentum center is rent-led; and
    the remaining center is high-growth/high-valuation.
    """

    price_index = FEATURE_COLUMNS.index(PRICE_MOMENTUM)
    rent_index = FEATURE_COLUMNS.index(RENT_MOMENTUM)
    volatility_index = FEATURE_COLUMNS.index(PRICE_VOLATILITY)
    remaining = set(range(N_CLUSTERS))

    stable = min(remaining, key=lambda item: (centers[item, volatility_index], item))
    remaining.remove(stable)
    cooling = max(
        remaining,
        key=lambda item: (
            centers[item, volatility_index] - centers[item, price_index],
            -item,
        ),
    )
    remaining.remove(cooling)
    rent_led = max(
        remaining,
        key=lambda item: (
            centers[item, rent_index] - centers[item, price_index],
            -item,
        ),
    )
    remaining.remove(rent_led)
    high_growth = remaining.pop()
    return {
        stable: 1,
        rent_led: 2,
        cooling: 3,
        high_growth: 4,
    }


def _training_data_fingerprint(training: pd.DataFrame) -> str:
    records: list[dict[str, Any]] = []
    for row in training.itertuples(index=False):
        record: dict[str, Any] = {
            "quarter_end": row.quarter_end.date().isoformat(),
            "postal_zip": str(row.postal_zip),
        }
        for column in FEATURE_COLUMNS:
            record[column] = float(getattr(row, column)).hex()
        records.append(record)
    payload = json.dumps(records, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _model_fingerprint(model: dict[str, Any]) -> str:
    payload = dict(model)
    payload.pop("model_fingerprint_sha256", None)
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def fit_market_regime_model(
    quarterly_inputs: pd.DataFrame,
    *,
    source_lineage: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Fit and return the fixed 2015Q2--2019Q4 regime model definition."""

    required = {"quarter_end", "postal_zip", *FEATURE_COLUMNS}
    missing = sorted(required - set(quarterly_inputs.columns))
    if missing:
        raise ValueError(f"Quarterly regime inputs are missing columns: {missing}")

    inputs = quarterly_inputs.copy()
    inputs["quarter_end"] = pd.to_datetime(inputs["quarter_end"], errors="coerce")
    observed_quarters = set(inputs["quarter_end"].dropna().unique())
    missing_quarters = [
        date.date().isoformat()
        for date in _required_training_quarters()
        if date.to_datetime64() not in observed_quarters
    ]
    if missing_quarters:
        raise ValueError(
            "Quarterly inputs do not span the fixed 2015Q2-2019Q4 training "
            f"window; missing quarters: {missing_quarters}"
        )

    training = inputs.loc[
        inputs["quarter_end"].between(TRAINING_START, TRAINING_END),
        ["quarter_end", "postal_zip", *FEATURE_COLUMNS],
    ].dropna(subset=list(FEATURE_COLUMNS))
    training = training.sort_values(["quarter_end", "postal_zip"]).reset_index(drop=True)
    if len(training) < N_CLUSTERS:
        raise ValueError("The fixed training window has fewer than four complete rows")

    raw_values = training.loc[:, FEATURE_COLUMNS].to_numpy(dtype="float64")
    means = raw_values.mean(axis=0)
    scales = raw_values.std(axis=0, ddof=0)
    if not np.isfinite(means).all() or not np.isfinite(scales).all():
        raise ValueError("Training scaler contains non-finite values")
    if (scales <= 0).any():
        constant = [
            FEATURE_COLUMNS[index] for index in np.flatnonzero(scales <= 0)
        ]
        raise ValueError(f"Training features have zero variation: {constant}")

    standardized = (raw_values - means) / scales
    raw_centers, raw_labels, _, iterations = _fit_deterministic_kmeans(standardized)
    mapping = _semantic_cluster_mapping(raw_centers)
    semantic_centers = np.empty_like(raw_centers)
    semantic_labels = np.empty_like(raw_labels)
    for raw_cluster, regime_id in mapping.items():
        semantic_centers[regime_id - 1] = raw_centers[raw_cluster]
        semantic_labels[raw_labels == raw_cluster] = regime_id
    distances = _squared_distances(standardized, semantic_centers)
    inertia = float(
        distances[np.arange(len(standardized)), semantic_labels - 1].sum()
    )

    regimes: list[dict[str, Any]] = []
    for regime_id in range(1, N_CLUSTERS + 1):
        members = semantic_labels == regime_id
        raw_mean = raw_values[members].mean(axis=0)
        regimes.append(
            {
                "regime_id": regime_id,
                "regime_label": REGIME_LABELS[regime_id],
                "training_observation_count": int(members.sum()),
                "center_standardized": {
                    column: float(semantic_centers[regime_id - 1, index])
                    for index, column in enumerate(FEATURE_COLUMNS)
                },
                "training_raw_mean": {
                    column: float(raw_mean[index])
                    for index, column in enumerate(FEATURE_COLUMNS)
                },
            }
        )

    complete_by_quarter = (
        training.groupby("quarter_end", sort=True)
        .size()
        .rename("complete_observation_count")
    )
    model: dict[str, Any] = {
        "schema_version": MODEL_SCHEMA_VERSION,
        "model_version": MODEL_VERSION,
        "algorithm": "numpy_kmeans_plus_plus_lloyd",
        "n_clusters": N_CLUSTERS,
        "random_seed": RANDOM_SEED,
        "n_init": N_INIT,
        "max_iterations": MAX_ITERATIONS,
        "convergence_tolerance": CONVERGENCE_TOLERANCE,
        "iterations_for_selected_fit": int(iterations),
        "feature_columns": list(FEATURE_COLUMNS),
        "training_start": TRAINING_START.date().isoformat(),
        "training_end": TRAINING_END.date().isoformat(),
        "application_start": APPLICATION_START.date().isoformat(),
        "standardization_ddof": 0,
        "scaler_mean": {
            column: float(means[index])
            for index, column in enumerate(FEATURE_COLUMNS)
        },
        "scaler_scale": {
            column: float(scales[index])
            for index, column in enumerate(FEATURE_COLUMNS)
        },
        "regimes": regimes,
        "label_assignment_rule": (
            "post-fit center ranking: lowest volatility=stable; highest "
            "volatility-minus-price-momentum=cooling; highest rent-minus-price "
            "momentum=rent-led; remaining center=high-growth/high-valuation"
        ),
        "training_observation_count": int(len(training)),
        "training_postal_zip_count": int(training["postal_zip"].nunique()),
        "training_quarter_count": int(training["quarter_end"].nunique()),
        "training_complete_by_quarter": {
            date.date().isoformat(): int(count)
            for date, count in complete_by_quarter.items()
        },
        "training_inertia": inertia,
        "training_data_sha256": _training_data_fingerprint(training),
        "source_lineage": source_lineage or [],
    }
    model["model_fingerprint_sha256"] = _model_fingerprint(model)
    return model


def _validate_model(model: dict[str, Any]) -> None:
    if model.get("model_fingerprint_sha256") != _model_fingerprint(model):
        raise ValueError("Market-regime model fingerprint does not match its contents")
    expected = {
        "schema_version": MODEL_SCHEMA_VERSION,
        "model_version": MODEL_VERSION,
        "n_clusters": N_CLUSTERS,
        "feature_columns": list(FEATURE_COLUMNS),
        "training_start": TRAINING_START.date().isoformat(),
        "training_end": TRAINING_END.date().isoformat(),
        "application_start": APPLICATION_START.date().isoformat(),
        "standardization_ddof": 0,
    }
    mismatched = {
        key: (model.get(key), value)
        for key, value in expected.items()
        if model.get(key) != value
    }
    if mismatched:
        raise ValueError(f"Market-regime model contract mismatch: {mismatched}")
    regimes = model.get("regimes")
    if not isinstance(regimes, list) or len(regimes) != N_CLUSTERS:
        raise ValueError("Market-regime model must contain four regimes")
    ids = [item.get("regime_id") for item in regimes]
    labels = [item.get("regime_label") for item in regimes]
    if ids != list(REGIME_LABELS) or labels != list(REGIME_LABELS.values()):
        raise ValueError("Market-regime model has invalid semantic IDs or labels")
    means = np.asarray([model["scaler_mean"][item] for item in FEATURE_COLUMNS])
    scales = np.asarray([model["scaler_scale"][item] for item in FEATURE_COLUMNS])
    centers = np.asarray(
        [
            [item["center_standardized"][column] for column in FEATURE_COLUMNS]
            for item in regimes
        ],
        dtype="float64",
    )
    if centers.shape != (N_CLUSTERS, len(FEATURE_COLUMNS)):
        raise ValueError("Market-regime center matrix has an invalid shape")
    if not np.isfinite(means).all() or not np.isfinite(scales).all() or not np.isfinite(centers).all():
        raise ValueError("Market-regime model contains non-finite parameters")
    if (scales <= 0).any():
        raise ValueError("Market-regime model scaler must be positive")


def save_market_regime_model(model: dict[str, Any], path: str | Path) -> Path:
    """Write a new model artifact without replacing an existing definition."""

    _validate_model(model)
    destination = Path(path)
    if destination.exists():
        raise FileExistsError(f"Refusing to overwrite frozen model: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(model, indent=2, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        if destination.exists():
            raise FileExistsError(f"Refusing to overwrite frozen model: {destination}")
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    return destination


def load_market_regime_model(path: str | Path) -> dict[str, Any]:
    """Load and validate a frozen market-regime model artifact."""

    source = Path(path)
    try:
        model = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Unable to read market-regime model {source}: {error}") from error
    if not isinstance(model, dict):
        raise ValueError("Market-regime model JSON must contain an object")
    _validate_model(model)
    return model


def _model_arrays(
    model: dict[str, Any],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    means = np.asarray(
        [model["scaler_mean"][column] for column in FEATURE_COLUMNS],
        dtype="float64",
    )
    scales = np.asarray(
        [model["scaler_scale"][column] for column in FEATURE_COLUMNS],
        dtype="float64",
    )
    centers = np.asarray(
        [
            [regime["center_standardized"][column] for column in FEATURE_COLUMNS]
            for regime in model["regimes"]
        ],
        dtype="float64",
    )
    return means, scales, centers


def classify_quarterly_regime_inputs(
    quarterly_inputs: pd.DataFrame, model: dict[str, Any]
) -> pd.DataFrame:
    """Classify complete rows by nearest frozen standardized center."""

    _validate_model(model)
    result = quarterly_inputs.copy().sort_values(
        ["quarter_end", "postal_zip"]
    ).reset_index(drop=True)
    complete = result.loc[:, FEATURE_COLUMNS].notna().all(axis=1)
    result["complete_regime_inputs"] = complete
    result["missing_regime_input_count"] = (
        result.loc[:, FEATURE_COLUMNS].isna().sum(axis=1).astype("Int64")
    )
    result["missing_regime_inputs"] = result.loc[:, FEATURE_COLUMNS].apply(
        lambda row: "|".join(row.index[row.isna()]), axis=1
    )
    result["classification_status"] = np.where(
        complete, "classified", "missing_input"
    )
    result["zip_market_regime_id"] = pd.Series(pd.NA, index=result.index, dtype="Int64")
    result["zip_market_regime_label"] = pd.Series(
        pd.NA, index=result.index, dtype="string"
    )
    result["zip_market_regime_distance"] = np.nan

    means, scales, centers = _model_arrays(model)
    zscores = pd.DataFrame(
        np.nan, index=result.index, columns=ZSCORE_COLUMNS, dtype="float64"
    )
    if complete.any():
        values = result.loc[complete, FEATURE_COLUMNS].to_numpy(dtype="float64")
        standardized = (values - means) / scales
        distances = _squared_distances(standardized, centers)
        nearest = distances.argmin(axis=1)
        classified_index = result.index[complete]
        regime_ids = nearest + 1
        result.loc[classified_index, "zip_market_regime_id"] = regime_ids
        result.loc[classified_index, "zip_market_regime_label"] = [
            REGIME_LABELS[int(item)] for item in regime_ids
        ]
        result.loc[classified_index, "zip_market_regime_distance"] = np.sqrt(
            distances[np.arange(len(nearest)), nearest]
        )
        zscores.loc[classified_index, :] = standardized
    result = pd.concat([result, zscores], axis=1)
    result["market_regime_model_version"] = model["model_version"]
    result["market_regime_model_fingerprint_sha256"] = model[
        "model_fingerprint_sha256"
    ]
    return result


def _transition_output(
    classified_context: pd.DataFrame,
    application_quarters: pd.DatetimeIndex,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    classified = classified_context.loc[
        classified_context["complete_regime_inputs"],
        [
            "quarter_end",
            "postal_zip",
            "zip_market_regime_id",
            "zip_market_regime_label",
        ],
    ].copy()
    for quarter_end in application_quarters:
        previous_quarter = (
            (quarter_end.to_period("Q") - 1).to_timestamp(how="end").normalize()
        )
        current = classified.loc[
            classified["quarter_end"].eq(quarter_end)
        ].rename(
            columns={
                "zip_market_regime_id": "to_regime_id",
                "zip_market_regime_label": "to_regime_label",
            }
        )
        previous = classified.loc[
            classified["quarter_end"].eq(previous_quarter)
        ].rename(
            columns={
                "zip_market_regime_id": "from_regime_id",
                "zip_market_regime_label": "from_regime_label",
            }
        )
        comparable = previous.merge(
            current,
            on="postal_zip",
            how="inner",
            validate="one_to_one",
            suffixes=("_previous", "_current"),
        )
        denominator = int(comparable["postal_zip"].nunique())
        classified_count = int(current["postal_zip"].nunique())
        missing_previous = classified_count - denominator
        complete_comparison = classified_count > 0 and missing_previous == 0
        denominator_status = (
            "no_current_classifications"
            if classified_count == 0
            else "complete"
            if complete_comparison
            else "incomplete_previous_quarter"
        )
        changed = int(
            comparable["from_regime_id"].ne(comparable["to_regime_id"]).sum()
        )
        counts = (
            comparable.groupby(["from_regime_id", "to_regime_id"], observed=True)
            .size()
            .to_dict()
        )
        for from_id, from_label in REGIME_LABELS.items():
            for to_id, to_label in REGIME_LABELS.items():
                count = int(counts.get((from_id, to_id), 0))
                rows.append(
                    {
                        "quarter_end": quarter_end,
                        "previous_quarter_end": previous_quarter,
                        "from_regime_id": from_id,
                        "from_regime_label": from_label,
                        "to_regime_id": to_id,
                        "to_regime_label": to_label,
                        "transition_zip_count": count,
                        "classified_zip_count": classified_count,
                        "comparable_zip_count": denominator,
                        "missing_previous_classification_count": missing_previous,
                        "transition_denominator_status": denominator_status,
                        # N_t is the current-quarter classified population,
                        # shared with the document's regime stock shares.
                        # A missing previous classification does not imply an
                        # unchanged regime, so the full-population percentage
                        # is unidentified unless every current ZIP is paired.
                        "transition_share_of_classified": (
                            100.0 * count / classified_count
                            if complete_comparison
                            else np.nan
                        ),
                        "transition_share_of_comparable": (
                            100.0 * count / denominator
                            if denominator
                            else np.nan
                        ),
                        "changed_regime_zip_count": changed,
                        "changed_regime_share_of_classified": (
                            100.0 * changed / classified_count
                            if complete_comparison
                            else np.nan
                        ),
                        "changed_regime_share_of_comparable": (
                            100.0 * changed / denominator
                            if denominator
                            else np.nan
                        ),
                        "is_regime_change": from_id != to_id,
                    }
                )
    return pd.DataFrame(rows)


def _summary_output(
    assignments: pd.DataFrame, transitions: pd.DataFrame
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for quarter_end, quarter in assignments.groupby("quarter_end", sort=True):
        classified = quarter.loc[quarter["complete_regime_inputs"]]
        universe_count = int(quarter["postal_zip"].nunique())
        classified_count = int(classified["postal_zip"].nunique())
        counts = classified["zip_market_regime_id"].value_counts().to_dict()
        quarter_transitions = transitions.loc[
            transitions["quarter_end"].eq(quarter_end)
        ]
        if quarter_transitions.empty:
            comparable_count = 0
            changed_count = 0
            changed_share = np.nan
        else:
            comparable_count = int(quarter_transitions["comparable_zip_count"].iloc[0])
            changed_count = int(
                quarter_transitions["changed_regime_zip_count"].iloc[0]
            )
            changed_share = quarter_transitions[
                "changed_regime_share_of_comparable"
            ].iloc[0]
        missing_previous = classified_count - comparable_count
        complete_comparison = classified_count > 0 and missing_previous == 0
        denominator_status = (
            "no_current_classifications"
            if classified_count == 0
            else "complete"
            if complete_comparison
            else "incomplete_previous_quarter"
        )
        for regime_id, regime_label in REGIME_LABELS.items():
            regime_count = int(counts.get(regime_id, 0))
            entered = int(
                quarter_transitions.loc[
                    quarter_transitions["is_regime_change"]
                    & quarter_transitions["to_regime_id"].eq(regime_id),
                    "transition_zip_count",
                ].sum()
            )
            exited = int(
                quarter_transitions.loc[
                    quarter_transitions["is_regime_change"]
                    & quarter_transitions["from_regime_id"].eq(regime_id),
                    "transition_zip_count",
                ].sum()
            )
            rows.append(
                {
                    "quarter_end": quarter_end,
                    "regime_id": regime_id,
                    "regime_label": regime_label,
                    "regime_zip_count": regime_count,
                    "classified_zip_count": classified_count,
                    "universe_zip_count": universe_count,
                    "regime_share_of_classified": (
                        100.0 * regime_count / classified_count
                        if classified_count
                        else np.nan
                    ),
                    "classification_coverage_share_of_universe": (
                        100.0 * classified_count / universe_count
                        if universe_count
                        else np.nan
                    ),
                    "comparable_zip_count": comparable_count,
                    "missing_previous_classification_count": missing_previous,
                    "transition_denominator_status": denominator_status,
                    "changed_regime_zip_count": changed_count,
                    "changed_regime_share_of_classified": (
                        100.0 * changed_count / classified_count
                        if complete_comparison
                        else np.nan
                    ),
                    "changed_regime_share_of_comparable": changed_share,
                    "entered_regime_zip_count": entered,
                    "entered_regime_share_of_classified": (
                        100.0 * entered / classified_count
                        if complete_comparison
                        else np.nan
                    ),
                    "entered_regime_share_of_comparable": (
                        100.0 * entered / comparable_count
                        if comparable_count
                        else np.nan
                    ),
                    "exited_regime_zip_count": exited,
                    "exited_regime_share_of_classified": (
                        100.0 * exited / classified_count
                        if complete_comparison
                        else np.nan
                    ),
                    "exited_regime_share_of_comparable": (
                        100.0 * exited / comparable_count
                        if comparable_count
                        else np.nan
                    ),
                }
            )
    return pd.DataFrame(rows)


def build_zip_market_regimes(
    zip_monthly_panel: pd.DataFrame,
    model_path: str | Path | None = None,
    *,
    source_lineage: list[dict[str, Any]] | None = None,
) -> MarketRegimeOutputs:
    """Build fixed-model 2020+ assignments, summaries, and transitions.

    If ``model_path`` exists, its scaler, centers, and labels are reloaded and
    the historical training data are not refit.  If it does not exist, the
    model is fit from the fixed training window and saved once.  Passing
    ``None`` fits an in-memory model, which is useful for tests but should not
    be used for a production refresh.
    """

    quarterly_inputs = build_quarterly_regime_inputs(zip_monthly_panel)
    if model_path is not None and Path(model_path).exists():
        model = load_market_regime_model(model_path)
    else:
        model = fit_market_regime_model(
            quarterly_inputs, source_lineage=source_lineage
        )
        if model_path is not None:
            save_market_regime_model(model, model_path)

    context_start = APPLICATION_START - pd.DateOffset(months=3)
    context_inputs = quarterly_inputs.loc[
        quarterly_inputs["quarter_end"].ge(context_start)
    ].copy()
    classified_context = classify_quarterly_regime_inputs(context_inputs, model)
    assignments = classified_context.loc[
        classified_context["quarter_end"].ge(APPLICATION_START)
    ].reset_index(drop=True)
    application_quarters = pd.DatetimeIndex(
        sorted(assignments["quarter_end"].dropna().unique())
    )
    transitions = _transition_output(classified_context, application_quarters)
    summary = _summary_output(assignments, transitions)
    return MarketRegimeOutputs(assignments, summary, transitions)


__all__ = [
    "APPLICATION_START",
    "FEATURE_COLUMNS",
    "MODEL_SCHEMA_VERSION",
    "MODEL_VERSION",
    "MarketRegimeOutputs",
    "PRICE_MOMENTUM",
    "PRICE_TO_RENT",
    "PRICE_VOLATILITY",
    "REGIME_LABELS",
    "RENT_MOMENTUM",
    "TRAINING_END",
    "TRAINING_START",
    "build_quarterly_regime_inputs",
    "build_zip_market_regimes",
    "classify_quarterly_regime_inputs",
    "fit_market_regime_model",
    "load_market_regime_model",
    "save_market_regime_model",
]
