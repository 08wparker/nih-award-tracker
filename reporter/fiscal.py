"""Federal fiscal calendar helpers.

The fiscal year runs 1 Oct - 30 Sep and FY2026 began 1 Oct 2025. Plotting on a fiscal
week axis (week 1 = 1-7 Oct) is what lets several years be overlaid comparably: NIH's
award cycle is driven by the fiscal calendar, and the September end-of-year surge would
land in a different place on every line if we used calendar weeks.

Weeks are computed relative to the award's *stated* fiscal_year rather than derived from
the notice date, so binning can never disagree with the grouping.
"""

import numpy as np
import pandas as pd

WEEKS_IN_FISCAL_YEAR = 52


def fiscal_year_start(fiscal_year):
    """First day of the given federal fiscal year."""
    return pd.Timestamp(year=int(fiscal_year) - 1, month=10, day=1)


def fiscal_week(dates, fiscal_years):
    """Fiscal week (1-52) of each date, relative to its own fiscal year's 1 Oct.

    A 365-day year yields a partial 53rd week; it is folded into week 52 so every year
    has the same axis. Returns a nullable Int64 Series; dates outside their stated
    fiscal year come back as NA rather than silently landing in a wrong bucket.
    """
    dates = pd.to_datetime(pd.Series(dates))
    years = pd.Series(fiscal_years).astype("Int64")

    # Work in numpy and re-attach the caller's index at the end. Returning a
    # differently-indexed Series would align silently and scatter awards into the wrong
    # weeks when the caller assigns it back onto a filtered frame -- a bug that is
    # invisible in aggregate totals and only shows up as impossible week membership.
    index = dates.index
    anchors = pd.to_datetime(
        pd.Series(years.to_numpy()).map(
            lambda v: None if pd.isna(v) else "{:04d}-10-01".format(int(v) - 1)
        ),
        errors="coerce",
    ).to_numpy()

    offset = (dates.to_numpy() - anchors) / np.timedelta64(1, "D")
    with np.errstate(invalid="ignore"):
        week = np.floor(offset / 7.0) + 1.0
        valid = np.isfinite(offset) & (offset >= 0) & (offset <= 366)
        week = np.where(valid, np.minimum(week, WEEKS_IN_FISCAL_YEAR), np.nan)

    return pd.Series(week, index=index).astype("Float64").astype("Int64")


def week_bounds(fiscal_year, week):
    """(first_day, last_day) of a fiscal week. Week 52 absorbs the year's tail."""
    start = fiscal_year_start(fiscal_year) + pd.Timedelta(days=7 * (int(week) - 1))
    if int(week) >= WEEKS_IN_FISCAL_YEAR:
        end = pd.Timestamp(year=int(fiscal_year), month=9, day=30)
    else:
        end = start + pd.Timedelta(days=6)
    return start, end


def month_tick_positions():
    """Fiscal-week positions of each month boundary, for axis labelling.

    Uses a non-leap reference fiscal year so ticks sit at stable weeks.
    """
    ref_fy = 2023  # 1 Oct 2022 - 30 Sep 2023, no leap day
    ticks = []
    for month in [10, 11, 12, 1, 2, 3, 4, 5, 6, 7, 8, 9]:
        year = ref_fy - 1 if month >= 10 else ref_fy
        day = pd.Timestamp(year=year, month=month, day=1)
        week = ((day - fiscal_year_start(ref_fy)).days // 7) + 1
        ticks.append({"week": int(week), "label": day.strftime("%b")})
    return ticks
