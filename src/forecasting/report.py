"""Draw the Week 1 historical forecasts and summarize the held-out results."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
from reportlab.graphics import renderPDF, renderSVG
from reportlab.graphics.shapes import Drawing, Line, PolyLine, Rect, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Paragraph


INK = colors.HexColor("#142c37")
TEAL = colors.HexColor("#007d76")
OCHRE = colors.HexColor("#a05e16")
MUTED = colors.HexColor("#4a6471")
RULE = colors.HexColor("#d9e3e5")
PAPER = colors.HexColor("#eef4f4")


def _text(x, y, value, size=10, color=INK, anchor="start", bold=False):
    return String(x, y, str(value), fontName="Helvetica-Bold" if bold else "Helvetica",
                  fontSize=size, fillColor=color, textAnchor=anchor)


def _ticks(low, high):
    """Round the tick spacing to readable values, including for flat traces."""
    if high == low:
        low, high = low - 1, high + 1
    rough = (high - low) / 3
    power = 10 ** math.floor(math.log10(rough))
    step = next(s * power for s in (1, 2, 2.5, 5, 10) if s * power >= rough)
    return np.arange(math.floor(low / step), math.ceil(high / step) + 1) * step


def _chart(panel, *, width, height, ylabel, dollar=False):
    """Draw one horizon. The index holds target months; lines break at gaps."""
    drawing = Drawing(width, height)
    left, right, bottom, top = 47, width - 6, 22, height - 15
    index = pd.PeriodIndex(panel.index, freq="M")
    positions = index.asi8
    values = panel.to_numpy(dtype=float)
    ticks = _ticks(np.nanmin(values), np.nanmax(values))

    def x(n):
        return left + (right - left) * (n - positions[0]) / max(1, positions[-1] - positions[0])

    def y(v):
        return bottom + (top - bottom) * (v - ticks[0]) / (ticks[-1] - ticks[0])

    drawing.add(_text(0, height - 6, ylabel, size=8, color=MUTED))
    for v in ticks:
        yp = y(v)
        if abs(v) >= 1_000_000:
            label = f"{v / 1_000_000:.1f}m"
        elif abs(v) >= 10_000:
            label = f"{v / 1000:,.0f}k"
        else:
            label = f"{v:,.0f}"
        drawing.add(_text(left - 5, yp - 3, ("$" if dollar else "") + label, size=8,
                          color=MUTED, anchor="end"))
        drawing.add(Line(left, yp, right, yp, strokeColor=RULE, strokeWidth=0.5))
    step = max(1, math.ceil(len(index) / 4))
    marks = list(range(0, len(index), step))
    if len(index) - 1 - marks[-1] < step * 0.55:
        marks.pop()
    marks.append(len(index) - 1)
    for i in marks:
        drawing.add(_text(x(positions[i]), 5, index[i].strftime("%b %y"), size=8,
                          color=MUTED, anchor="end" if i == len(index)-1 else "middle"))
    for name, color, dash, line_width in (
        ("benchmark", OCHRE, [3, 3], 1.3), ("forecast", TEAL, None, 1.7),
        ("actual", INK, None, 2.1),
    ):
        points = []
        for i, value in enumerate(panel[name]):
            if (i and positions[i] != positions[i-1] + 1) or not np.isfinite(value):
                if len(points) >= 4:
                    drawing.add(PolyLine(points, strokeColor=color, strokeDashArray=dash,
                                         strokeWidth=line_width))
                points = []
            if np.isfinite(value):
                points.extend([x(positions[i]), y(value)])
        if len(points) >= 4:
            drawing.add(PolyLine(points, strokeColor=color, strokeDashArray=dash,
                                 strokeWidth=line_width))
    return drawing


def _legend(drawing, y, model_name, benchmark_name, width):
    x = 0
    for label, color, dash in (("Observed", INK, None), (model_name, TEAL, None),
                               (benchmark_name, OCHRE, [3, 3])):
        drawing.add(Line(x, y + 3, x + 17, y + 3, strokeColor=color, strokeWidth=1.8,
                         strokeDashArray=dash))
        drawing.add(_text(x + 21, y, label, size=9))
        x += width / 3


def _page_header(pdf, page, title, kicker):
    pdf.setFillColor(TEAL)
    pdf.setFont("Helvetica-Bold", 9)
    pdf.drawString(46, 745, "LOS ANGELES HOUSING DATABASE     /     RESEARCH MEMO 01")
    pdf.setFillColor(INK)
    pdf.setFont("Helvetica-Bold", 24)
    pdf.drawString(46, 707, title)
    pdf.setFont("Helvetica", 11)
    pdf.setFillColor(MUTED)
    pdf.drawString(46, 686, kicker)
    pdf.setStrokeColor(RULE)
    pdf.line(46, 52, 566, 52)
    pdf.setFont("Helvetica", 8)
    pdf.drawString(46, 36, "WEEK 1     /     HISTORICAL BACKTESTING")
    pdf.drawRightString(566, 36, f"CALIFORNIA HOUSING     /     {page}")


def _heading(pdf, top, title):
    pdf.setFont("Helvetica-Bold", 12.5)
    pdf.setFillColor(INK)
    pdf.drawString(46, top - 12, title)
    return top - 21


def _body(pdf, top, text, *, size=10, width=520, x=46):
    item = Paragraph(text, ParagraphStyle("body", fontName="Helvetica",
                     fontSize=size, leading=size * 1.4, textColor=INK))
    _, h = item.wrap(width, 735)
    item.drawOn(pdf, x, top - h)
    return top - h - 11


def _table(pdf, top, headers, rows, widths):
    height = 18
    pdf.setFillColor(PAPER)
    pdf.rect(46, top-height, sum(widths), height, fill=1, stroke=0)
    for n, row in enumerate([headers, *rows]):
        pdf.setFillColor(INK)
        pdf.setFont("Helvetica-Bold" if n == 0 else "Helvetica", 9)
        x = 46
        for i, (s, w) in enumerate(zip(row, widths)):
            if i == 0:
                pdf.drawString(x + 6, top - 12, s)
            else:
                pdf.drawRightString(x + w - 6, top - 12, s)
            x += w
        top -= height
        pdf.setStrokeColor(RULE)
        pdf.setLineWidth(.4)
        pdf.line(46, top, 46 + sum(widths), top)
    return top - 10


LA = "COUNTY_06037"
HORIZONS = (3, 6, 12)
LABELS = {
    "growth_ar1": "Growth AR(1)", "growth_ar3": "Growth AR(3)",
    "growth_ar6": "Growth AR(6)", "level_ar1": "Level AR(1)",
    "level_ar3": "Level AR(3)", "arima011": "ARIMA(0,1,1)",
    "naive": "No change", "seasonal_naive": "Seasonal naive",
    "mean3_ar1": "Mean AR(1)", "raw_ar1_mean3": "Raw AR(1)",
    "naive_mean3": "No change", "seasonal_naive_mean3": "Seasonal naive",
}


def _los_angeles(frame, variable, target="level"):
    return frame.loc[(frame.geo_id == LA) & (frame.variable_id == variable)
                     & (frame.target == target)]


def _main_start(frame):
    values = frame.loc[frame.model.isin(["naive", "naive_mean3"]), "train_start"].unique()
    if len(values) != 1:
        raise ValueError("Expected one primary training start for the baseline forecast.")
    return values[0]


def _point(frame, model, horizon=6, start=None):
    if start is None:
        start = _main_start(frame)
    rows = frame.loc[(frame.model == model) & (frame.horizon == horizon)
                     & (frame.train_start == start)]
    if len(rows) != 1:
        raise ValueError(f"Expected one accuracy record for {model}, {start}, h={horizon}")
    return rows.iloc[0]


def _pair(frame, model, benchmark, horizon):
    rows = frame.loc[(frame.train_start == _main_start(frame)) & (frame.horizon == horizon)
                     & frame.model.isin([model, benchmark])]
    wide = rows.pivot(index="target_date", columns="model", values="forecast")
    actual = rows.groupby("target_date").actual
    if not actual.nunique().eq(1).all() or wide.isna().any().any():
        raise ValueError("The plotted forecasts must have identical observed targets.")
    panel = wide.rename(columns={model: "forecast", benchmark: "benchmark"})
    panel["actual"] = actual.first()
    return panel.sort_index()


def _plots(output_dir, test):
    # Each SVG preserves the target dates, the outcome's natural unit and the horizon.
    specs = [
        ("zhvi", "level", "growth_ar3", "naive", "Home values", "Dollars", True),
        ("zori_smoothed_sa", "level", "growth_ar3", "naive", "Rent index", "Dollars per month", True),
        ("permits_total", "mean3", "mean3_ar1", "seasonal_naive_mean3",
         "Housing permits", "Housing units, trailing three-month mean", False),
        ("active_inventory", "level", "level_ar3", "seasonal_naive",
         "Active for-sale inventory", "Homes", False),
    ]
    for var, target, model, benchmark, name, unit, dollar in specs:
        frame = _los_angeles(test, var, target)
        if frame.empty:
            continue
        fig = Drawing(744, 626)
        fig.add(Rect(0, 0, 744, 626, fillColor=colors.white, strokeWidth=0, strokeColor=None))
        fig.add(_text(24, 595, f"{name} in Los Angeles County", size=21, bold=True))
        fig.add(_text(24, 572, "Held-out monthly targets. Each forecast uses observations "
                      "dated through its origin in the revised series.", color=MUTED))
        legend = Drawing(696, 16)
        _legend(legend, 3, LABELS[model], LABELS[benchmark], 640)
        legend.translate(24, 545)
        fig.add(legend)
        for i, h in enumerate(HORIZONS):
            base = 368 - 165 * i
            fig.add(_text(24, base + 144, f"{h} months ahead", size=11, bold=True))
            panel = _pair(frame, model, benchmark, h)
            chart = _chart(panel, width=694, height=134, ylabel=unit, dollar=dollar)
            chart.translate(24, base)
            fig.add(chart)
        fig.add(_text(24, 12, "Source data from the local housing DuckDB, revised provider "
                      "series. The horizontal axis shows the target month.", size=9, color=MUTED))
        renderSVG.drawToFile(fig, str(output_dir / f"{var}_forecasts.svg"))


def _growth(observations, geo_id, variable):
    rows = observations.loc[(observations.geo_id == geo_id)
                            & (observations.variable_id == variable)]
    series = rows.set_index(pd.PeriodIndex(rows.observation_date, freq="M")).value
    months = pd.period_range(series.index.min(), series.index.max(), freq="M")
    return 100 * np.log(series.reindex(months)).diff()


def _seasonality(observations, start, end):
    # Share of common monthly log-change variation explained by calendar month.
    variables = ["zhvi", "active_inventory"]
    growth = pd.concat([_growth(observations, LA, v).rename(v) for v in variables], axis=1)
    growth = growth.loc[start:end].dropna()
    residual = growth - growth.groupby(growth.index.month).transform("mean")
    return 100 * (1 - residual.var() / growth.var())


def _money(value):
    return f"${value:,.0f}"


def _check_bottom(y):
    if y < 52:
        raise ValueError(f"Week 1 memo exceeds the page frame (bottom {y:.1f} points).")


def _memo(output_dir, forecasts, scores, fits, selected, observations):
    test = forecasts.loc[forecasts.split == "test"]
    la = _los_angeles(test, "zhvi")
    val = _los_angeles(forecasts.loc[forecasts.split == "validation"], "zhvi")
    price = _los_angeles(scores.loc[scores.split == "test"], "zhvi")
    start = _main_start(price)
    chosen = _los_angeles(selected, "zhvi").set_index("horizon")
    pdf = Canvas(str(output_dir / "week1_memo.pdf"), pagesize=letter, invariant=1)
    pdf.setTitle("Week 1 historical housing forecasts")
    pdf.setAuthor("Los Angeles Housing Database")
    _page_header(pdf, 1, "What past home values predicted",
                 "Week 1 historical forecasts   |   Los Angeles County")
    horizons_beating = [h for h in HORIZONS if chosen.loc[h, "test_rmse"]
                        < _point(price, "naive", h).rmse]
    if horizons_beating:
        result = ("The validation-selected models beat the no-change forecast on test RMSE "
                  f"at horizons of {', '.join(str(h) for h in horizons_beating)} months.")
    elif all(chosen.loc[h, "test_rmse"] > _point(price, "naive", h).rmse for h in HORIZONS):
        result = ("Holding the last home value constant beat the validation-selected models "
                  "on test RMSE at all three horizons.")
    else:
        result = "None of the validation-selected models improved on the no-change benchmark."
    top = _body(pdf, 664,
        f"<b>{result}</b> We tested {la.origin.nunique()} monthly forecast origins "
        f"from {la.origin.min()} through {la.origin.max()}. Each model uses the county's own "
        "price history to predict Zillow's typical-home value in dollars.")
    top = _heading(pdf, top, "Independent test against the no-change benchmark")
    rows = []
    for h in HORIZONS:
        choice = chosen.loc[h]
        label = LABELS[choice.model]
        if choice.train_start != start:
            label += f", {choice.train_start[:4]}"
        rows.append([f"{h} months", label, _money(choice.test_mae),
                     _money(choice.test_rmse), _money(_point(price, "naive", h).rmse)])
    top = _table(pdf, top,
                 ["Horizon", "Validation choice", "Test MAE", "Test RMSE", "No-change RMSE"],
                 rows, [70, 127, 91, 99, 133])
    top = _heading(pdf, top, "Six-month forecasts from the three-lag growth model")
    chart = _chart(_pair(la, "growth_ar3", "naive", 6), width=520, height=117,
                   ylabel="Typical home value in dollars", dollar=True)
    renderPDF.draw(chart, pdf, 46, top - 117)
    legend = Drawing(520, 15)
    _legend(legend, 2, "Growth AR(3)", "No change", 475)
    renderPDF.draw(legend, pdf, 46, top - 134)
    top -= 148
    top = _heading(pdf, top, "Lag length, horizon and training window")
    first = fits.loc[(fits.geo_id == LA) & (fits.variable_id == "zhvi")
                     & (fits.train_start == start) & (fits.origin == val.origin.min())
                     & fits.model.isin(["growth_ar1", "growth_ar3", "growth_ar6"])]
    fitted = first.sort_values("train_rmse").iloc[0]
    best_lags = []
    for h in HORIZONS:
        candidates = [_point(price, m, h) for m in ["growth_ar1", "growth_ar3", "growth_ar6"]]
        best_lags.append(str(int(min(candidates, key=lambda row: row.rmse).model[-1])))
    lag_summary = (f"AR({best_lags[0]}) also had the lowest test RMSE of the three growth "
                   "models at every horizon." if len(set(best_lags)) == 1 else
                   f"The best growth models at 3, 6 and 12 months used {', '.join(best_lags)} "
                   "lags respectively.")
    top = _body(pdf, top,
        f"At the first validation origin ({val.origin.min()}), {LABELS[fitted.model]} had the "
        f"smallest training error ({fitted.train_rmse:.3f} percentage points, "
        f"{int(fitted.nobs)} fitted months). {lag_summary} That retrospective ranking uses "
        "held-out outcomes. The choices in the table used only the earlier validation period.")
    windows = price.loc[(price.model == "growth_ar3") & (price.horizon == 6)].sort_values("train_start")
    snapshot = fits.loc[(fits.geo_id == LA) & (fits.variable_id == "zhvi")
                        & (fits.model == "growth_ar3") & (fits.origin == val.origin.min())
                        ].set_index("train_start")
    coefficients = snapshot.coefficients.map(json.loads)
    starts = ", ".join(windows.train_start.str[:4])
    slopes = ", ".join(f"{coefficients[r]['log_growth_pct_lag_1']:.2f}" for r in windows.train_start)
    errors = ", ".join(_money(v) for v in windows.rmse)
    top = _body(pdf, top,
        f"Starting the AR(3) sample in {starts} gave first-origin lag-one coefficients of "
        f"{slopes}, respectively. Six-month test RMSE was {errors}. Later starts give pandemic-era "
        "price changes more weight and leave fewer months to estimate persistence. "
        "An expanding window adds a month at each origin. A rolling window also moves the start.")
    top = _body(pdf, top,
        f"At six months, AR(1) in price levels had RMSE of {_money(_point(price, 'level_ar1').rmse)}, "
        f"versus {_money(_point(price, 'growth_ar1').rmse)} for AR(1) in growth and "
        f"{_money(_point(price, 'arima011').rmse)} for ARIMA(0,1,1) on log levels.")
    longest = chosen.loc[12]
    long_run = la.loc[(la.model == longest.model) & (la.train_start == longest.train_start)
                      & (la.horizon == 12)]
    miss = long_run.loc[long_run.error.abs().idxmax()]
    top = _body(pdf, top,
        f"The largest miss for the selected 12-month model came from the {miss.origin} origin. "
        f"It predicted {_money(miss.forecast)} for {miss.target_date}, when the observed value "
        f"was {_money(miss.actual)}.")
    _check_bottom(top)
    pdf.showPage()

    _page_header(pdf, 2, "How forecasts varied by market",
                 "Common target months   |   Six-month horizon")
    top = _heading(pdf, 664, "California counties")
    county_rows = []
    county_scores = scores.loc[(scores.variable_id == "zhvi") & (scores.target == "level")
                               & (scores.split == "test") & scores.geo_id.str.startswith("COUNTY_")]
    for geo_id, group in county_scores.groupby("geo_id"):
        name = observations.loc[observations.geo_id == geo_id, "geo_name"].iloc[0]
        one, three = (_point(group, m, 6) for m in ("growth_ar1", "growth_ar3"))
        dates = pd.PeriodIndex(la.loc[la.horizon == 6, "target_date"].unique(), freq="M")
        vol = _growth(observations, geo_id, "zhvi").reindex(dates).std()
        county_rows.append((str(name).replace(" County", "").split(",")[0], one, three, vol))
    rows = [[name, _money(one.rmse), _money(three.rmse), f"{three.mape_pct:.2f}%",
             f"{vol:.2f} pp"] for name, one, three, vol in county_rows]
    top = _table(pdf, top, ["County", "AR(1) RMSE", "AR(3) RMSE", "AR(3) MAPE",
                             "Growth SD"], rows, [124, 100, 100, 100, 96])
    expensive = max(county_rows, key=lambda x: x[2].mape_pct)[0]
    volatile = max(county_rows, key=lambda x: x[3])[0]
    ar3_count = sum(r[2].rmse < r[1].rmse for r in county_rows)
    geography = (f"{expensive} had both the highest AR(3) mean absolute percentage error "
                 "(MAPE) and the most variable monthly price growth." if expensive == volatile
                 else f"{expensive} had the highest AR(3) mean absolute percentage error (MAPE). "
                 f"{volatile} had the most variable monthly price growth.")
    if (test.geo_id == "STATE_06").any():
        statewide = "The tables report a separate forecast for the observed statewide ZHVI."
    elif (observations.geo_id == "STATE_06").any():
        statewide = "Statewide ZHVI did not cover the common training and evaluation dates."
    else:
        statewide = "A statewide ZHVI series was unavailable in the local database."
    top = _body(pdf, top,
        f"All {len(county_rows)} counties had observations at every target month. "
        f"AR(3) beat AR(1) on six-month RMSE in {ar3_count} of them. "
        f"{geography} Growth SD is the standard deviation of monthly log changes on the "
        f"target dates, in percentage points (pp). {statewide}")
    top = _heading(pdf, top, "Rents, permits and homes for sale")
    rent = _los_angeles(scores.loc[scores.split == "test"], "zori_smoothed_sa")
    p = _point(price, "growth_ar3")
    if not rent.empty:
        r = _point(rent, "growth_ar3")
        top = _body(pdf, top,
            f"<b>Rent.</b> On identical six-month dates the three-lag model missed the "
            f"smoothed, seasonally adjusted Zillow rent index by {r.mape_pct:.2f}% on average, "
            f"versus {p.mape_pct:.2f}% for home values. Both models forecast monthly log "
            "growth and compound it from the observed level at the origin.")
    permits = _los_angeles(scores.loc[scores.split == "test"], "permits_total", "mean3")
    if not permits.empty:
        m, raw, seasonal = (_point(permits, model).rmse for model in
                            ("mean3_ar1", "raw_ar1_mean3", "seasonal_naive_mean3"))
        top = _body(pdf, top,
            f"<b>Permits.</b> On the same trailing three-month mean of permitted units, "
            f"test RMSE was {m:,.0f} for AR(1) on the mean, {raw:,.0f} for AR(1) on raw "
            f"permits followed by averaging, and {seasonal:,.0f} for seasonal naive. "
            "Adjacent means share two months, which creates persistence mechanically. Census "
            "flags many published permit estimates as imputed. Monthly survey coverage expanded in 2022.")
    inventory = _los_angeles(scores.loc[scores.split == "test"], "active_inventory")
    if not inventory.empty:
        s = _seasonality(observations, start, la.origin.min())
        seasonal, ar3 = (_point(inventory, model).rmse for model in ("seasonal_naive", "level_ar3"))
        top = _body(pdf, top,
            f"<b>Inventory.</b> Six-month RMSE was {seasonal:,.0f} homes for seasonal naive "
            f"and {ar3:,.0f} for level AR(3). Through the first test origin, calendar month "
            f"accounted for {s.active_inventory:.0f}% of monthly log-change variation in "
            f"unadjusted inventory versus {s.zhvi:.0f}% in Zillow's already smoothed and "
            "seasonally adjusted home values. The benchmark uses the level twelve months before the target.")
    top = _heading(pdf, top, "How to read the backtest")
    top = _body(pdf, top,
        f"Validation used monthly origins {val.origin.min()} to {val.origin.max()}. Each "
        "regression fits current growth or level to earlier months (its lags). A fitted value "
        "uses that estimation sample. A forecast instead predicts beyond the origin, using its "
        "own predictions when future lags are needed. Error is forecast minus actual. MAE "
        "averages absolute errors. RMSE gives large misses more weight.")
    top = _body(pdf, top,
        "Provider histories in the current DuckDB incorporate revisions and smoothing. These "
        "retrospective tests do not reconstruct what was published at each origin. Monthly "
        "origins also yield overlapping multi-month errors. Results apply to these counties, "
        "outcomes and dates. The CSV tables retain the sample months, coefficients and errors.")
    _check_bottom(top)
    pdf.showPage()
    pdf.save()


def write_report(output_dir: Path) -> None:
    """Generate memo and figures using only the completed Week 1 CSV exports."""
    output_dir = Path(output_dir)
    forecasts = pd.read_csv(output_dir / "forecasts.csv")
    scores = pd.read_csv(output_dir / "accuracy.csv")
    fits = pd.read_csv(output_dir / "regressions.csv")
    selected = pd.read_csv(output_dir / "selections.csv")
    observations = pd.read_csv(output_dir / "observations.csv")
    _plots(output_dir, forecasts.loc[forecasts.split == "test"])
    _memo(output_dir, forecasts, scores, fits, selected, observations)
