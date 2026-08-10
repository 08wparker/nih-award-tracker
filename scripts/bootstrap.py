"""One-time full history pull: every Type-1 award in the tracked families, FY2022-26.

Partitioned by (fetch group x fiscal year) so no single query approaches the API's
15,000-record paging ceiling. Roughly 80 requests, ~6 minutes at the 1.2s pace.

    python3 scripts/bootstrap.py [--fiscal-years 2022 2023 ...] [--out data/awards.csv.gz]

Safe to re-run: it upserts into the existing store rather than replacing it.
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reporter import AWARDS_PATH, FISCAL_YEARS, STATE_PATH  # noqa: E402
from reporter.api import ReporterClient, build_criteria  # noqa: E402
from reporter.families import FETCH_GROUPS  # noqa: E402
from reporter import store  # noqa: E402


def log(msg):
    print(msg, flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fiscal-years", type=int, nargs="+", default=FISCAL_YEARS)
    ap.add_argument("--out", default=AWARDS_PATH)
    args = ap.parse_args()

    client = ReporterClient(log=log)
    today = time.strftime("%Y-%m-%d")

    existing = store.load(args.out)
    log("store: {:,} awards before bootstrap".format(len(existing)))

    started = time.time()
    total_new = 0

    for group, codes in FETCH_GROUPS.items():
        for fy in args.fiscal_years:
            criteria = build_criteria(activity_codes=codes, fiscal_years=[fy])
            records = client.search(criteria)
            incoming = store.normalize(records, observed_on=today)
            existing, stats = store.upsert(existing, incoming)
            total_new += stats["new"]
            log(
                "  {:<16s} FY{}  fetched {:>5,}  new {:>5,}  updated {:>4,}".format(
                    group, fy, len(records), stats["new"], stats["updated"]
                )
            )

    store.save(existing, args.out)

    state = {
        "last_bootstrap": today,
        "last_refresh": today,
        "fiscal_years": args.fiscal_years,
        "awards": int(len(existing)),
    }
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w") as fh:
        json.dump(state, fh, indent=2)

    log("")
    log("store: {:,} awards ({:,} new) written to {}".format(
        len(existing), total_new, args.out))
    log("{} requests in {:.0f}s".format(client.request_count, time.time() - started))


if __name__ == "__main__":
    main()
