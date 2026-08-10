"""Backtest the lag correction against what actually happened.

`date_added` lets us rewind the store to any past date and see exactly what a run on
that date would have seen. So we can ask the only question that matters: if we had
nowcast a week back then, how close would we have been to its final count?

    python3 scripts/backtest.py [--horizons 30 60 90]

For each horizon the script rewinds, fits the completeness curves on data available
*then* (no look-ahead), nowcasts every week, and scores against today's counts. It
reports the nowcast's error next to the error of doing nothing, because the correction
is only worth shipping if it beats the raw numbers.

Only weeks that are effectively final today are scored -- otherwise we would penalise
the nowcast for correctly predicting awards that still have not been published.
"""

import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reporter import AWARDS_PATH, FISCAL_YEARS  # noqa: E402
from reporter import aggregate, lag, store  # noqa: E402
from reporter.families import FAMILY_ORDER, FAMILY_LABELS  # noqa: E402

TRUTH_COMPLETENESS = 0.95  # only score weeks today's data has essentially settled


def evaluate(prepared, curves_today, as_of_today, horizon, method="auto"):
    """Score the nowcast made `horizon` days ago against today's counts."""
    as_of_past = as_of_today - pd.Timedelta(days=horizon)
    past = aggregate.as_of_view(prepared, as_of_past)

    # Fit on what was knowable then. Refitting (rather than reusing today's curves) is
    # the point: it tests the whole procedure, not just the arithmetic.
    curves_past = lag.fit_curves(past, as_of_past, method=method)

    rows = []
    for family in FAMILY_ORDER:
        curve_past = curves_past[family]["curve"]
        lo_past = curves_past[family].get("curve_lo")
        hi_past = curves_past[family].get("curve_hi")
        curve_today = curves_today[family]["curve"]

        past_counts = aggregate.weekly_counts(past, family)
        true_counts = aggregate.weekly_counts(prepared, family)

        for fy in FISCAL_YEARS:
            for week in range(1, 53):
                start, _ = aggregate.fiscal.week_bounds(fy, week)
                if start > as_of_past:
                    continue

                # Require the week to be settled in *today's* data to count as truth.
                if lag.expected_fraction(curve_today, fy, week, as_of_today) < TRUTH_COMPLETENESS:
                    continue

                observed = int(past_counts[fy].get(week, 0)) if fy in past_counts.columns else 0
                truth = int(true_counts[fy].get(week, 0)) if fy in true_counts.columns else 0
                if truth == 0:
                    continue  # undefined percentage error

                frac = lag.expected_fraction(curve_past, fy, week, as_of_past)
                f_lo = lag.expected_fraction(lo_past, fy, week, as_of_past) if lo_past else None
                f_hi = lag.expected_fraction(hi_past, fy, week, as_of_past) if hi_past else None
                est, lo, hi, status = lag.nowcast_week(observed, frac, f_lo, f_hi)
                if status == "masked":
                    continue

                rows.append({
                    "family": family, "fiscal_year": fy, "week": week,
                    "fraction": frac, "status": status,
                    "observed": observed, "truth": truth, "nowcast": est,
                    "covered": bool(lo <= truth <= hi),
                    "raw_ape": abs(observed - truth) / float(truth),
                    "nowcast_ape": abs(est - truth) / float(truth),
                })
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--horizons", type=int, nargs="+", default=[30, 60, 90])
    ap.add_argument("--store", default=AWARDS_PATH)
    ap.add_argument("--method", default="auto", choices=["auto", "chain_ladder", "ecdf"])
    args = ap.parse_args()

    awards = store.load(args.store)
    prepared = aggregate.prepare(awards, FISCAL_YEARS)
    as_of_today = pd.to_datetime(prepared["date_added"].max()).normalize()

    curves_today = lag.fit_curves(prepared, as_of_today, method=args.method)

    print("store as of {} -- {:,} awards\n".format(as_of_today.date(), len(prepared)))
    print("Completeness curves fit today (% of awards visible within k days):")
    print(lag.curve_summary(curves_today).to_string())
    print()

    all_results = []
    for horizon in args.horizons:
        res = evaluate(prepared, curves_today, as_of_today, horizon, args.method)
        if res.empty:
            print("horizon {:>3}d: no scorable weeks".format(horizon))
            continue
        res["horizon"] = horizon
        all_results.append(res)

        print("=" * 76)
        print("Rewound {} days (to {})".format(
            horizon, (as_of_today - pd.Timedelta(days=horizon)).date()))
        print("=" * 76)

        summary = res.groupby("family").apply(
            lambda g: pd.Series({
                "weeks": len(g),
                "provisional": int((g.status == "provisional").sum()),
                "raw MAPE %": round(100 * g.raw_ape.mean(), 1),
                "nowcast MAPE %": round(100 * g.nowcast_ape.mean(), 1),
                "95% coverage %": round(100 * g.covered.mean(), 1),
            }), include_groups=False
        )
        summary.index = [FAMILY_LABELS[f] for f in summary.index]
        print(summary.to_string())

        prov = res[res.status == "provisional"]
        if not prov.empty:
            print("\n  provisional weeks only (where the correction actually acts):")
            sub = prov.groupby("family").apply(
                lambda g: pd.Series({
                    "weeks": len(g),
                    "mean completeness %": round(100 * g.fraction.mean(), 1),
                    "raw MAPE %": round(100 * g.raw_ape.mean(), 1),
                    "nowcast MAPE %": round(100 * g.nowcast_ape.mean(), 1),
                    "95% coverage %": round(100 * g.covered.mean(), 1),
                }), include_groups=False
            )
            sub.index = [FAMILY_LABELS[f] for f in sub.index]
            print(sub.to_string())
        print()

    if all_results:
        combined = pd.concat(all_results)
        prov = combined[combined.status == "provisional"]
        print("=" * 76)
        print("OVERALL, provisional weeks across all horizons: n={}".format(len(prov)))
        if not prov.empty:
            print("  raw MAPE      {:5.1f}%".format(100 * prov.raw_ape.mean()))
            print("  nowcast MAPE  {:5.1f}%   ({:.1f}x better)".format(
                100 * prov.nowcast_ape.mean(),
                prov.raw_ape.mean() / max(prov.nowcast_ape.mean(), 1e-9)))
            print("  95% coverage  {:5.1f}%".format(100 * prov.covered.mean()))
        os.makedirs("data", exist_ok=True)
        combined.to_csv("data/backtest_results.csv", index=False)
        print("\nper-week detail written to data/backtest_results.csv")


if __name__ == "__main__":
    main()
