"""Aggregate the award store into docs/data.json for the dashboard.

    python3 scripts/build_dashboard.py
"""

import argparse
import json
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reporter import AWARDS_PATH, COMPLETENESS_PATH, DASHBOARD_DATA_PATH, FISCAL_YEARS  # noqa: E402
from reporter import aggregate, lag, store  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--store", default=AWARDS_PATH)
    ap.add_argument("--out", default=DASHBOARD_DATA_PATH)
    args = ap.parse_args()

    awards = store.load(args.store)

    # "As of" is the freshest thing RePORTER has told us about, not the wall clock:
    # a run on a day RePORTER published nothing must not shift every completeness
    # calculation forward and quietly inflate the nowcast.
    as_of = pd.to_datetime(awards["date_added"].max()).normalize()

    data = aggregate.build_dashboard_data(awards, as_of, FISCAL_YEARS)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as fh:
        json.dump(data, fh, separators=(",", ":"))

    # Persist the full curves separately -- too bulky for the dashboard payload, but
    # wanted for analysis and for spotting regime shifts over time.
    prepared = aggregate.prepare(awards, FISCAL_YEARS)
    curves = lag.fit_curves(prepared, as_of)
    with open(COMPLETENESS_PATH, "w") as fh:
        json.dump({"as_of": as_of.strftime("%Y-%m-%d"), "curves": curves}, fh)

    size = os.path.getsize(args.out) / 1024.0
    print("as of {}   {:,} awards".format(as_of.date(), data["meta"]["total_awards"]))
    print("wrote {} ({:.0f} KB), {} ICs x {} families".format(
        args.out, size, len(data["meta"]["ics"]), len(data["meta"]["families"])))
    print()
    print("completeness (% of awards visible within k days):")
    print(lag.curve_summary(curves).to_string())


if __name__ == "__main__":
    main()
