"""Turn the award-level store into weekly series, and into the dashboard's data.json."""

import numpy as np
import pandas as pd

from . import fiscal, lag
from .families import FAMILIES, FAMILY_LABELS, FAMILY_ORDER

ALL_ICS = "ALL"


def prepare(awards, fiscal_years=None):
    """Attach fiscal_week and drop rows that cannot be placed on the weekly axis."""
    df = awards.copy()
    if fiscal_years is not None:
        df = df[df["fiscal_year"].isin(fiscal_years)]
    df = df[df["award_notice_date"].notna()]
    df["fiscal_week"] = fiscal.fiscal_week(df["award_notice_date"], df["fiscal_year"])
    return df[df["fiscal_week"].notna()]


def as_of_view(prepared, as_of):
    """The store as it appeared on a past date -- rows RePORTER had published by then.

    This is what makes an honest backtest possible: `date_added` lets us reconstruct
    exactly the data a run on that date would have seen, with no look-ahead.
    """
    return prepared[prepared["date_added"] <= pd.Timestamp(as_of).normalize()]


def weekly_counts(prepared, family, ic=None):
    """Weekly award counts as a (52 x fiscal_year) frame of ints."""
    rows = prepared[prepared["activity_code"].isin(FAMILIES[family])]
    if ic and ic != ALL_ICS:
        rows = rows[rows["ic"] == ic]

    if rows.empty:
        return pd.DataFrame(index=pd.RangeIndex(1, fiscal.WEEKS_IN_FISCAL_YEAR + 1))

    table = rows.pivot_table(
        index="fiscal_week", columns="fiscal_year", values="appl_id", aggfunc="count"
    )
    full = table.reindex(range(1, fiscal.WEEKS_IN_FISCAL_YEAR + 1)).fillna(0)
    return full.astype(int)


def ic_volumes(prepared, family, fiscal_years=None):
    """Award count per IC for a family, used to order the IC dropdown sensibly."""
    rows = prepared[prepared["activity_code"].isin(FAMILIES[family])]
    if fiscal_years is not None:
        rows = rows[rows["fiscal_year"].isin(fiscal_years)]
    return rows["ic"].value_counts()


def nowcast_series(counts, curves, family, fiscal_year, as_of):
    """Apply the lag correction to one fiscal year's weekly counts.

    Returns dict of 52-length lists: point / low / high / fraction / status.
    """
    curve = curves[family]["curve"]
    curve_lo = curves[family].get("curve_lo")
    curve_hi = curves[family].get("curve_hi")
    point, low, high, fractions, status = [], [], [], [], []

    for week in range(1, fiscal.WEEKS_IN_FISCAL_YEAR + 1):
        observed = int(counts.get(week, 0)) if hasattr(counts, "get") else 0
        frac = lag.expected_fraction(curve, fiscal_year, week, as_of)
        f_lo = lag.expected_fraction(curve_lo, fiscal_year, week, as_of) if curve_lo else None
        f_hi = lag.expected_fraction(curve_hi, fiscal_year, week, as_of) if curve_hi else None
        est, lo, hi, state = lag.nowcast_week(observed, frac, f_lo, f_hi)
        point.append(None if est is None else round(est, 1))
        low.append(None if lo is None else round(lo, 1))
        high.append(None if hi is None else round(hi, 1))
        fractions.append(round(frac, 4))
        status.append(state)

    return {"point": point, "low": low, "high": high,
            "fraction": fractions, "status": status}


def last_observed_week(prepared, fiscal_year):
    """Latest fiscal week with any award in the given year (the data's true edge)."""
    rows = prepared[prepared["fiscal_year"] == fiscal_year]
    if rows.empty:
        return None
    return int(rows["fiscal_week"].max())


def pace_vs_baseline(prepared, curves, family, current_fy, baseline_fys, as_of):
    """Per-IC FY-to-date pace against that IC's own recent history.

    Raw counts are useless for comparing ICs -- NCI funds an order of magnitude more
    R01s than NIDCD -- so each IC is scored against itself: lag-corrected awards so far
    this year, divided by the mean awards those ICs had by the same fiscal week in the
    baseline years. 1.0 means on pace, 0.6 means 40% behind.
    """
    edge = last_observed_week(prepared, current_fy)
    if edge is None:
        return []

    rows = prepared[prepared["activity_code"].isin(FAMILIES[family])]
    curve = curves[family]["curve"]

    out = []
    for ic in sorted(set(rows["ic"].dropna())):
        ic_rows = rows[rows["ic"] == ic]

        current = ic_rows[
            (ic_rows["fiscal_year"] == current_fy) & (ic_rows["fiscal_week"] <= edge)
        ]
        observed = int(len(current))

        # Correct each week individually, then sum: completeness varies sharply across
        # the weeks in the window, so scaling the total by one factor would be wrong.
        by_week = current.groupby("fiscal_week")["appl_id"].count()
        corrected = 0.0
        for week in range(1, edge + 1):
            count = int(by_week.get(week, 0))
            frac = lag.expected_fraction(curve, current_fy, week, as_of)
            corrected += count / frac if frac >= lag.MASK_BELOW else count

        baselines = []
        for fy in baseline_fys:
            prior = ic_rows[
                (ic_rows["fiscal_year"] == fy) & (ic_rows["fiscal_week"] <= edge)
            ]
            baselines.append(len(prior))
        baseline = float(np.mean(baselines)) if baselines else 0.0

        # Tiny programmes produce meaningless ratios; keep them out of the chart.
        if baseline < 5:
            continue

        out.append({
            "ic": ic,
            "observed": observed,
            "corrected": round(corrected, 1),
            "baseline": round(baseline, 1),
            "pace": round(corrected / baseline, 3),
            "raw_pace": round(observed / baseline, 3),
            "baseline_by_year": {str(fy): int(len(ic_rows[
                (ic_rows["fiscal_year"] == fy) & (ic_rows["fiscal_week"] <= edge)
            ])) for fy in baseline_fys},
        })

    out.sort(key=lambda r: r["pace"])
    return out


def build_dashboard_data(awards, as_of, fiscal_years, min_ic_awards=25):
    """Assemble everything docs/index.html needs, as one JSON-serializable dict."""
    as_of = pd.Timestamp(as_of).normalize()
    prepared = prepare(awards, fiscal_years)
    curves = lag.fit_curves(prepared, as_of)

    current_fy = max(fiscal_years)
    baseline_fys = [fy for fy in fiscal_years if fy != current_fy]

    # One IC list across all families, ordered by overall volume, so switching family
    # never reshuffles or empties the dropdown.
    overall = prepared["ic"].value_counts()
    ics = [ic for ic, n in overall.items() if n >= min_ic_awards]

    series = {}
    for family in FAMILY_ORDER:
        per_ic = {}
        for ic in [ALL_ICS] + ics:
            counts = weekly_counts(prepared, family, ic)
            years = {}
            for fy in fiscal_years:
                col = counts[fy] if fy in counts.columns else pd.Series(dtype=int)
                years[str(fy)] = [int(col.get(w, 0))
                                  for w in range(1, fiscal.WEEKS_IN_FISCAL_YEAR + 1)]
            entry = {"counts": years,
                     "nowcast": nowcast_series(
                         counts[current_fy] if current_fy in counts.columns
                         else pd.Series(dtype=int),
                         curves, family, current_fy, as_of)}
            per_ic[ic] = entry
        series[family] = per_ic

    comparison = {
        family: pace_vs_baseline(prepared, curves, family, current_fy, baseline_fys, as_of)
        for family in FAMILY_ORDER
    }

    ic_totals = {
        family: {ic: int(v) for ic, v in ic_volumes(prepared, family).items()}
        for family in FAMILY_ORDER
    }

    return {
        "meta": {
            "as_of": as_of.strftime("%Y-%m-%d"),
            "fiscal_years": [int(fy) for fy in fiscal_years],
            "current_fiscal_year": int(current_fy),
            "baseline_fiscal_years": [int(fy) for fy in baseline_fys],
            "weeks_in_year": fiscal.WEEKS_IN_FISCAL_YEAR,
            "month_ticks": fiscal.month_tick_positions(),
            "families": FAMILY_ORDER,
            "family_labels": FAMILY_LABELS,
            "ics": ics,
            "ic_totals": ic_totals,
            "last_observed_week": {
                str(fy): last_observed_week(prepared, fy) for fy in fiscal_years
            },
            "mask_below": lag.MASK_BELOW,
            "solid_above": lag.SOLID_ABOVE,
            "total_awards": int(len(prepared)),
        },
        "completeness": {
            family: {
                "n": info["n"],
                "median_lag": info["median_lag"],
                "p90_lag": info["p90_lag"],
                "pooled": info["pooled"],
                "checkpoints": {
                    str(k): round(100 * info["curve"][k], 1)
                    for k in (7, 14, 28, 56, 90, 180)
                },
            }
            for family, info in curves.items()
        },
        "series": series,
        "comparison": comparison,
    }
