"""Parity, idempotency and guard-rail tests.

The parity test is the one that matters most: it pins our reverse-engineered
R01-equivalent definition against a frozen RePORTER web export. If NIH changes the
grouping, or someone "helpfully" adds R56 back, this fails loudly.

    python3 -m pytest tests/ -q      (or: python3 tests/test_parity.py)
"""

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reporter import AWARDS_PATH  # noqa: E402
from reporter import store  # noqa: E402
from reporter.api import MAX_LIMIT, MAX_OFFSET, MAX_REACHABLE, ResultSetTooLarge  # noqa: E402
from reporter.families import R01_EQUIVALENT, families_for  # noqa: E402

# Frozen RePORTER web export, kept gzipped as a permanent regression fixture.
EXPORT = "SearchResult_Export_05Aug2026_061136.csv.gz"

# Award counts in the frozen 5 Aug 2026 export: NHLBI, Type 1, R01 Equivalents.
# Closed fiscal years only -- FY2026 was still accruing when the export was taken.
EXPECTED_NHLBI_R01EQ = {2022: 651, 2023: 646, 2024: 659, 2025: 621}


def _load_export():
    df = pd.read_csv(EXPORT, skiprows=8, low_memory=False)
    return df.drop_duplicates(subset="Application ID")


def test_r01_equivalent_matches_frozen_export():
    """Our code list must reproduce RePORTER's own R01-Equivalents grouping exactly."""
    db = store.load(AWARDS_PATH)
    export = _load_export()

    for fy, expected in EXPECTED_NHLBI_R01EQ.items():
        ours = set(
            db[
                (db.fiscal_year == fy)
                & (db.ic == "NHLBI")
                & (db.activity_code.isin(R01_EQUIVALENT))
            ].appl_id.astype(int)
        )
        theirs = set(export.loc[export["Fiscal Year"] == fy, "Application ID"])

        assert len(theirs) == expected, "export FY{} moved: {}".format(fy, len(theirs))
        assert ours == theirs, (
            "FY{} mismatch: {} only in store, {} only in export".format(
                fy, len(ours - theirs), len(theirs - ours)
            )
        )


def test_export_awards_all_present():
    """No award in the export may be missing from the store, in any year."""
    db = store.load(AWARDS_PATH)
    export = _load_export()
    missing = set(export["Application ID"]) - set(db.appl_id.astype(int))
    assert not missing, "{} export awards absent from store".format(len(missing))


def test_r56_retained_but_not_reported():
    """R56 stays in the store for later use, but is not an R01-equivalent."""
    db = store.load(AWARDS_PATH)
    assert (db.activity_code == "R56").any(), "R56 should still be fetched and stored"
    assert "R56" not in R01_EQUIVALENT
    assert families_for("R56") == []


def test_f32_is_in_both_f_families():
    """F32 is reported both on its own and within F-series."""
    assert set(families_for("F32")) == {"f_series", "f32"}


def test_upsert_is_idempotent():
    """Upserting the same rows twice changes nothing and reports no new awards."""
    records = [
        {
            "appl_id": 1, "project_num": "1R01HL000001-01", "activity_code": "R01",
            "award_type": "1", "fiscal_year": 2026,
            "agency_ic_admin": {"abbreviation": "NHLBI"},
            "award_notice_date": "2026-01-15T00:00:00",
            "date_added": "2026-01-20T00:00:00",
            "award_amount": 500000, "organization": {"org_name": "X"},
            "contact_pi_name": "DOE, JANE", "project_title": "T",
        }
    ]
    first = store.normalize(records, observed_on="2026-01-20")
    merged, stats = store.upsert(None, first)
    assert stats["new"] == 1

    # Same records observed a week later: first_seen must not drift forward.
    second = store.normalize(records, observed_on="2026-01-27")
    merged2, stats2 = store.upsert(merged, second)
    assert stats2 == {"new": 0, "updated": 0, "unchanged": 1, "date_added_changed": 0}
    assert merged2.loc[0, "first_seen"] == pd.Timestamp("2026-01-20")
    assert len(merged2) == 1


def test_upsert_flags_date_added_drift():
    """A rewritten date_added must be surfaced -- the nowcast depends on it being stable."""
    base = {
        "appl_id": 2, "project_num": "1R01HL000002-01", "activity_code": "R01",
        "award_type": "1", "fiscal_year": 2026,
        "agency_ic_admin": {"abbreviation": "NHLBI"},
        "award_notice_date": "2026-01-15T00:00:00",
        "date_added": "2026-01-20T00:00:00", "award_amount": 1,
        "organization": {"org_name": "X"}, "contact_pi_name": "A", "project_title": "T",
    }
    merged, _ = store.upsert(None, store.normalize([base], "2026-01-20"))

    moved = dict(base, date_added="2026-03-01T00:00:00")
    _, stats = store.upsert(merged, store.normalize([moved], "2026-03-01"))
    assert stats["date_added_changed"] == 1
    assert store.check_date_added_stability(stats) is not None


def test_api_paging_ceiling_constants():
    """Guard the hard API limits so a refactor can't silently raise them."""
    assert MAX_LIMIT == 500
    assert MAX_OFFSET == 14_999
    assert MAX_REACHABLE == 15_499


def test_too_large_result_set_raises():
    """A result set past the paging ceiling must raise, never truncate silently."""
    err = ResultSetTooLarge(15_401, {"fiscal_years": [2025]})
    assert err.total == 15_401
    assert "partition" in str(err)


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS  {}".format(name))
            except AssertionError as exc:
                failures += 1
                print("FAIL  {}: {}".format(name, exc))
    print("\n{} failed".format(failures) if failures else "\nall passed")
    sys.exit(1 if failures else 0)
