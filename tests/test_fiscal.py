"""Fiscal-week binning tests.

The alignment test exists because of a real bug: `fiscal_week` reset the index of its
return value, so assigning it onto a *filtered* DataFrame aligned by position-turned-
label and scattered awards into arbitrary weeks. Every aggregate total stayed correct,
so nothing looked wrong until a single "week" was found to span eight months. Weekly
totals alone cannot catch this -- the invariant worth testing is that each bucket holds
only dates that belong in it.
"""

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reporter import fiscal  # noqa: E402


def test_week_one_starts_on_october_first():
    weeks = fiscal.fiscal_week(
        pd.Series(["2025-10-01", "2025-10-07", "2025-10-08"]), pd.Series([2026, 2026, 2026])
    )
    assert list(weeks) == [1, 1, 2]


def test_last_days_fold_into_week_52():
    weeks = fiscal.fiscal_week(
        pd.Series(["2026-09-29", "2026-09-30"]), pd.Series([2026, 2026])
    )
    assert list(weeks) == [52, 52]


def test_date_outside_its_fiscal_year_is_na():
    weeks = fiscal.fiscal_week(pd.Series(["2020-01-01"]), pd.Series([2026]))
    assert weeks.isna().all()


def test_preserves_caller_index():
    """The regression guard: a non-contiguous index must survive unchanged."""
    df = pd.DataFrame(
        {"d": pd.to_datetime(["2025-10-01", "2026-04-08", "2026-09-30"]), "fy": [2026] * 3},
        index=[17, 4001, 92],
    )
    weeks = fiscal.fiscal_week(df["d"], df["fy"])
    assert list(weeks.index) == [17, 4001, 92]
    assert list(weeks) == [1, 28, 52]


def test_assignment_onto_filtered_frame_is_correct():
    """End-to-end version of the bug: filter, assign, then check bucket membership."""
    dates = pd.date_range("2025-10-01", "2026-09-30", freq="D")
    df = pd.DataFrame({"award_notice_date": dates, "fiscal_year": 2026})
    df["keep"] = [i % 3 == 0 for i in range(len(df))]

    filtered = df[df["keep"]].copy()
    filtered["fiscal_week"] = fiscal.fiscal_week(
        filtered["award_notice_date"], filtered["fiscal_year"]
    )

    # No week may span more than 7 days of notice dates.
    spans = filtered.groupby("fiscal_week")["award_notice_date"].agg(
        lambda s: (s.max() - s.min()).days
    )
    assert spans.max() <= 6, "week spans {} days -- rows are misaligned".format(spans.max())

    # And each award must sit in the week its own date implies.
    expected = ((filtered["award_notice_date"] - pd.Timestamp("2025-10-01")).dt.days // 7 + 1)
    expected = expected.clip(upper=52)
    assert (filtered["fiscal_week"].astype(int) == expected).all()


def test_month_ticks_are_ordered_and_in_range():
    ticks = fiscal.month_tick_positions()
    assert [t["label"] for t in ticks][:3] == ["Oct", "Nov", "Dec"]
    weeks = [t["week"] for t in ticks]
    assert weeks == sorted(weeks)
    assert min(weeks) >= 1 and max(weeks) <= 52


def test_week_bounds_round_trip():
    for week in (1, 28, 51, 52):
        start, end = fiscal.week_bounds(2026, week)
        assert start <= end
        back = fiscal.fiscal_week(pd.Series([start]), pd.Series([2026]))
        assert int(back.iloc[0]) == week


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
