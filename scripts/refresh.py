"""Daily refresh: pull awards RePORTER has added since the last run and upsert them.

    python3 scripts/refresh.py                 # incremental (default)
    python3 scripts/refresh.py --reconcile     # full refetch of every tracked FY
    python3 scripts/refresh.py --overlap 30    # widen the lookback window

The incremental path filters on `date_added` rather than award notice date. That is the
efficient choice -- it asks "what did RePORTER learn since I last looked?" directly, so
a daily run costs about 5 requests instead of the ~90 a full refetch needs, and it picks
up awards whose notice date is months old (the fellowship lag runs to 6+ months).

The window starts `--overlap` days *before* the last successful run, so a skipped,
delayed or half-finished run is absorbed on the next pass rather than leaving a hole.
`--reconcile` exists for the residual risk the overlap can't cover: records RePORTER
revises or withdraws without changing date_added. The workflow runs it weekly.
"""

import argparse
import datetime as dt
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reporter import AWARDS_PATH, FISCAL_YEARS, STATE_PATH  # noqa: E402
from reporter.api import ReporterClient, build_criteria  # noqa: E402
from reporter.families import FETCH_GROUPS  # noqa: E402
from reporter import store  # noqa: E402

DEFAULT_OVERLAP_DAYS = 7


def log(msg):
    print(msg, flush=True)


def read_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as fh:
            return json.load(fh)
    return {}


def write_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w") as fh:
        json.dump(state, fh, indent=2)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reconcile", action="store_true",
                    help="full refetch of every tracked fiscal year")
    ap.add_argument("--overlap", type=int, default=DEFAULT_OVERLAP_DAYS,
                    help="days to look back before the last run (default 7)")
    ap.add_argument("--store", default=AWARDS_PATH)
    args = ap.parse_args()

    client = ReporterClient(log=log)
    today = dt.date.today()
    today_str = today.isoformat()

    state = read_state()
    existing = store.load(args.store)
    before = len(existing)
    log("store: {:,} awards before refresh".format(before))

    started = time.time()
    totals = {"new": 0, "updated": 0, "unchanged": 0, "date_added_changed": 0}

    if args.reconcile:
        log("mode: reconcile (full refetch of FY{}-{})".format(
            min(FISCAL_YEARS), max(FISCAL_YEARS)))
        jobs = [
            (group, {"activity_codes": codes, "fiscal_years": [fy]}, "FY{}".format(fy))
            for group, codes in FETCH_GROUPS.items()
            for fy in FISCAL_YEARS
        ]
    else:
        last = state.get("last_refresh") or state.get("last_bootstrap")
        if last:
            start = dt.date.fromisoformat(last) - dt.timedelta(days=args.overlap)
        else:
            # No state: fall back to a wide window rather than silently fetching nothing.
            start = today - dt.timedelta(days=180)
            log("no prior state; falling back to a 180-day window")
        window = (start.isoformat(), today_str)
        log("mode: incremental, date_added {} .. {}".format(*window))
        jobs = [
            (group, {"activity_codes": codes, "fiscal_years": FISCAL_YEARS,
                     "date_added": window}, "delta")
            for group, codes in FETCH_GROUPS.items()
        ]

    for group, kwargs, label in jobs:
        criteria = build_criteria(**kwargs)
        records = client.search(criteria)
        incoming = store.normalize(records, observed_on=today_str)
        existing, stats = store.upsert(existing, incoming)
        for key in totals:
            totals[key] += stats[key]
        log("  {:<16s} {:<8s} fetched {:>5,}  new {:>4,}  updated {:>4,}".format(
            group, label, len(records), stats["new"], stats["updated"]))

    store.save(existing, args.store)

    warning = store.check_date_added_stability(totals)
    if warning:
        log("")
        log(warning)

    state.update({
        "last_refresh": today_str,
        "last_mode": "reconcile" if args.reconcile else "incremental",
        "awards": int(len(existing)),
        "last_run_new": totals["new"],
        "last_run_updated": totals["updated"],
        "last_run_date_added_changed": totals["date_added_changed"],
    })
    if args.reconcile:
        state["last_reconcile"] = today_str
    write_state(state)

    log("")
    log("store: {:,} awards (+{:,} new, {:,} updated)".format(
        len(existing), totals["new"], totals["updated"]))
    log("{} requests in {:.0f}s".format(client.request_count, time.time() - started))


if __name__ == "__main__":
    main()
