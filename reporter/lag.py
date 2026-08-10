"""Reporting-lag correction (a chain-ladder / nowcast for RePORTER's ingestion delay).

The problem: RePORTER publishes an award some days or months after its notice date, so
the trailing weeks of any chart are undercounted and read as a collapse in funding. The
size of the effect, measured from `date_added` over FY2024-25 Type-1 awards:

    within 7 days   R01-equivalents 48%   K-series 42%   F32  ~1%
    within 28 days                  97%             96%        22%
    within 180 days                ~100%           ~100%       94%

The fix: estimate a completeness curve C(k) = P(lag <= k) per family, then divide each
week's observed count by the share of that week we would expect to be visible by now.

C(k) is estimated by **chain ladder** on the reporting triangle of recent cohorts, not
as the lag ECDF of old fully-reported awards. Both were implemented and backtested; the
chain ladder won decisively, and the ECDF survives only as a fallback for families whose
recent triangle is too thin to fit. Measured on 213 partially-reported weeks:

    completeness   raw error   nowcast error   nowcast bias
      20-40%          72%           34%            0.92
      40-60%          68%           38%            0.90
      60-80%          39%           25%            0.97
      80-90%          16%           10%            0.98

Three things make this trustworthy rather than a fudge factor:

* **Recent cohorts.** Development factors come from cohorts currently part way through
  reporting, so the estimate tracks the present regime instead of assuming last year's.
* **Per family.** Fellowships and R01s differ by an order of magnitude; a pooled curve
  would over-correct R01s and badly under-correct F32.
* **Honest intervals.** The band combines Poisson noise in the count with the bootstrap
  spread of C(k) itself. Poisson alone covered 80-84% of truths against a nominal 95%;
  with both, backtest coverage is 96%.
* **Masking.** Below a floor of expected completeness the division amplifies noise
  without adding information, so those weeks are dropped rather than guessed at. For
  F32 that removes roughly the last two months, which is the honest answer.

The correction cannot create information. It restores the *level* of a week that is
partially reported; it cannot tell you about a week nothing has been reported from.
"""

import numpy as np
import pandas as pd
from scipy import stats

from . import fiscal
from .families import FAMILIES

MAX_LAG = 400          # days of curve to retain; beyond this treat as complete
MATURITY_DAYS = 365    # a cohort is "mature" once this old -- its lag is fully observed
TRAINING_YEARS = 3     # only fit on the last N years of mature cohorts
MIN_TRAINING_N = 100   # below this, fall back to a pooled curve
MASK_BELOW = 0.20      # weeks expected less than this complete are dropped entirely
SOLID_ABOVE = 0.98     # at or above this a week is drawn as settled, not provisional

# Chain-ladder settings. The cohort window is the key parameter: it must be long enough
# to estimate the tail but short enough to track the current regime.
MAX_DEV_WEEKS = 52
COHORT_WINDOW_WEEKS = 104
MIN_COHORT_AWARDS = 150  # below this the triangle is too thin; fall back to the ECDF
BOOTSTRAP_DRAWS = 200    # cohort resamples used for the completeness curve's own spread


def observed_lag(awards):
    """Days between award notice date and RePORTER ingestion, as a nullable Int64."""
    lag = (awards["date_added"] - awards["award_notice_date"]).dt.days
    # A handful of records carry a date_added preceding the notice date; treat those as
    # same-day rather than letting a negative lag distort the curve's left tail.
    return lag.clip(lower=0).astype("Int64")


def _ecdf(lags, max_lag=MAX_LAG):
    """Empirical C(k) for k = 0..max_lag, as a float array of length max_lag + 1."""
    lags = np.asarray(pd.Series(lags).dropna().astype(int))
    if lags.size == 0:
        return np.ones(max_lag + 1)
    counts = np.bincount(np.clip(lags, 0, max_lag), minlength=max_lag + 1)
    curve = np.cumsum(counts) / float(lags.size)
    return np.clip(curve, 0.0, 1.0)


def family_awards(awards, family):
    """Rows of the store belonging to a family."""
    return awards[awards["activity_code"].isin(FAMILIES[family])]


def _factors_to_curve(cum, observable, max_dev_weeks, max_lag):
    """Development factors -> daily completeness curve C(k)."""
    factors = np.ones(max_dev_weeks)
    for k in range(max_dev_weeks):
        usable = observable >= (k + 1)
        denom = cum[usable, k].sum()
        numer = cum[usable, k + 1].sum()
        if denom > 0 and numer >= denom:
            factors[k] = numer / denom

    tail = np.cumprod(factors[::-1])[::-1]
    weekly = np.ones(max_dev_weeks + 1)
    weekly[:max_dev_weeks] = 1.0 / np.maximum(tail, 1e-9)
    weekly = np.clip(np.maximum.accumulate(np.clip(weekly, 0.0, 1.0)), 0.0, 1.0)

    days = np.arange(max_lag + 1)
    knots = np.arange(max_dev_weeks + 1) * 7
    daily = np.interp(days, knots, weekly, left=weekly[0], right=1.0)
    return np.clip(np.maximum.accumulate(daily), 0.0, 1.0)


def _chain_ladder_curve(rows, as_of, max_dev_weeks=MAX_DEV_WEEKS,
                        cohort_window_weeks=COHORT_WINDOW_WEEKS, max_lag=MAX_LAG,
                        bootstrap=0, seed=0):
    """Estimate C(k) from the reporting triangle of recent cohorts.

    Why not simply take the lag ECDF of old, fully-observed awards? Because the lag
    regime moves. Backtesting the ECDF approach on FY2026 showed it claiming a week was
    93% reported when it was actually 33% -- whatever slowed NIH's FY2026 awards also
    slowed their posting, and a curve fit on FY2022-25 knew nothing about it.

    The chain ladder instead reads development out of cohorts that are *currently* part
    way through reporting. For each development step k -> k+1 it pools the cohorts old
    enough to have been observed at both, and takes the ratio of cumulative counts:

        f_k = sum_d N(d, <=k+1) / sum_d N(d, <=k)

    The product of the remaining factors converts a partial count into an estimated
    ultimate, so C(k) = 1 / prod_{j>=k} f_j. Recent cohorts dominate the early factors,
    which is exactly where a regime shift shows up first. The far tail still leans on
    older cohorts -- unavoidable, since only they have been observed that long -- but
    the tail moves the answer far less than the first few weeks do.

    Returns None when the triangle is too thin to be trusted, so the caller can fall
    back to the ECDF rather than act on noise.
    """
    as_of = pd.Timestamp(as_of).normalize()
    origin = as_of - pd.Timedelta(weeks=cohort_window_weeks)

    rows = rows[
        rows["award_notice_date"].notna()
        & rows["date_added"].notna()
        & (rows["award_notice_date"] >= origin)
        & (rows["award_notice_date"] <= as_of)
    ]
    if len(rows) < MIN_COHORT_AWARDS:
        return None, None, None

    notice = rows["award_notice_date"]
    cohort = ((notice - origin).dt.days // 7).astype(int)
    dev = (observed_lag(rows).astype(int) // 7).clip(upper=max_dev_weeks)

    n_cohorts = int(cohort.max()) + 1
    counts = np.zeros((n_cohorts, max_dev_weeks + 1))
    np.add.at(counts, (cohort.to_numpy(), dev.to_numpy()), 1.0)
    cum = counts.cumsum(axis=1)

    # A cohort's last fully-observed development step, measured from the end of its
    # week so we never credit a cohort with a step it has only partly lived through.
    cohort_end = origin + pd.to_timedelta(np.arange(n_cohorts) * 7 + 6, unit="D")
    observable = np.floor((as_of - cohort_end).days.to_numpy() / 7.0).astype(int)

    curve = _factors_to_curve(cum, observable, max_dev_weeks, max_lag)
    if not bootstrap:
        return curve, None, None

    # Resample whole cohorts with replacement to get the curve's own sampling spread.
    # Without this the nowcast interval reflects only Poisson noise in the observed
    # count and materially under-covers: backtest coverage was 80-84% against a nominal
    # 95% for the least-complete weeks.
    rng = np.random.default_rng(seed)
    draws = np.empty((bootstrap, max_lag + 1))
    for b in range(bootstrap):
        pick = rng.integers(0, n_cohorts, n_cohorts)
        draws[b] = _factors_to_curve(
            cum[pick], observable[pick], max_dev_weeks, max_lag
        )
    lo = np.percentile(draws, 2.5, axis=0)
    hi = np.percentile(draws, 97.5, axis=0)
    return curve, np.clip(lo, 1e-6, 1.0), np.clip(hi, 1e-6, 1.0)


def fit_curves(awards, as_of, maturity_days=MATURITY_DAYS,
               training_years=TRAINING_YEARS, max_lag=MAX_LAG, method="auto",
               bootstrap=BOOTSTRAP_DRAWS):
    """Fit C(k) per family, with bootstrap bounds where the chain ladder is used.

    Returns {family: {"curve", "curve_lo", "curve_hi", "method", "n", ...}}.
    """
    as_of = pd.Timestamp(as_of).normalize()
    newest = as_of - pd.Timedelta(days=maturity_days)
    oldest = as_of - pd.Timedelta(days=365 * training_years + maturity_days)

    lag_all = observed_lag(awards)
    mature_mask = (
        awards["award_notice_date"].notna()
        & (awards["award_notice_date"] <= newest)
        & (awards["award_notice_date"] >= oldest)
        & lag_all.notna()
    )
    pooled_curve = _ecdf(lag_all[mature_mask], max_lag)

    out = {}
    for family in FAMILIES:
        rows = family_awards(awards, family)
        lags = observed_lag(rows)
        mask = (
            rows["award_notice_date"].notna()
            & (rows["award_notice_date"] <= newest)
            & (rows["award_notice_date"] >= oldest)
            & lags.notna()
        )
        sample = lags[mask]

        curve, lo, hi, used = None, None, None, "ecdf"
        if method in ("auto", "chain_ladder"):
            curve, lo, hi = _chain_ladder_curve(
                rows, as_of, max_lag=max_lag, bootstrap=bootstrap
            )
            used = "chain_ladder"
        if curve is None:
            used = "ecdf"
            curve = pooled_curve if len(sample) < MIN_TRAINING_N else _ecdf(sample, max_lag)
            lo = hi = None
        method_used = used

        out[family] = {
            "curve": [round(float(v), 6) for v in curve],
            "curve_lo": None if lo is None else [round(float(v), 6) for v in lo],
            "curve_hi": None if hi is None else [round(float(v), 6) for v in hi],
            "method": method_used,
            "n": int(len(sample)),
            "median_lag": int(np.median(sample.astype(int))) if len(sample) else None,
            "p90_lag": int(np.percentile(sample.astype(int), 90)) if len(sample) else None,
            "pooled": bool(method == "ecdf" and len(sample) < MIN_TRAINING_N),
        }
    return out


def expected_fraction(curve, fiscal_year, week, as_of):
    """Share of a fiscal week's awards we expect to be visible by `as_of`.

    Averages C(k) across the days of the week, since a Monday award has had six more
    days to appear than the Sunday one. Assumes awards fall uniformly within the week;
    that only matters for the current partial week, where the value is small anyway.
    """
    as_of = pd.Timestamp(as_of).normalize()
    start, end = fiscal.week_bounds(fiscal_year, week)
    if start > as_of:
        return 0.0

    days = pd.date_range(start, min(end, as_of), freq="D")
    if len(days) == 0:
        return 0.0

    ages = np.clip(((as_of - days).days).to_numpy(), 0, len(curve) - 1)
    visible = np.asarray(curve)[ages]

    # Days of the week that have not happened yet contribute zero, not nothing --
    # otherwise the current partial week looks fully reported.
    total_days = (min(end, pd.Timestamp.max) - start).days + 1
    return float(visible.sum() / total_days)


def poisson_interval(count, alpha=0.05):
    """Exact (Garwood) Poisson confidence interval for an observed count."""
    lo = 0.0 if count == 0 else stats.chi2.ppf(alpha / 2.0, 2 * count) / 2.0
    hi = stats.chi2.ppf(1 - alpha / 2.0, 2 * (count + 1)) / 2.0
    return float(lo), float(hi)


def nowcast_week(observed, fraction, fraction_lo=None, fraction_hi=None, alpha=0.05):
    """Scale an observed count up to its expected final value.

    Returns (point, low, high, status) with status one of settled / provisional /
    masked.

    Two sources of uncertainty are combined. Poisson noise in the observed count gives
    an interval on Y; the bootstrap spread of the completeness curve gives an interval
    on f. Pairing the extremes -- Y_low/f_high and Y_high/f_low -- is deliberately
    conservative, and the conservatism is warranted: with Poisson noise alone the
    backtest covered only 80-84% of truths in the least-complete weeks against a
    nominal 95%.
    """
    if fraction <= 0 or fraction < MASK_BELOW:
        return None, None, None, "masked"
    if fraction >= SOLID_ABOVE:
        return float(observed), float(observed), float(observed), "settled"

    count_lo, count_hi = poisson_interval(int(observed), alpha)
    f_lo = fraction if fraction_lo is None else min(max(fraction_lo, 1e-6), fraction)
    f_hi = fraction if fraction_hi is None else max(min(fraction_hi, 1.0), fraction)

    return (
        float(observed) / fraction,
        count_lo / f_hi,
        count_hi / f_lo,
        "provisional",
    )


def curve_summary(curves):
    """Human-readable completeness table, for the README and logs.

    `hist_*` columns describe the observed lag of mature awards; the dayN columns are
    the fitted (usually chain-ladder) completeness for the *current* regime. They can
    differ substantially -- that gap is the regime shift the chain ladder exists to
    track -- so the column names keep the two apart.
    """
    checkpoints = [7, 14, 28, 56, 90, 180]
    rows = []
    for family, info in curves.items():
        curve = info["curve"]
        row = {"family": family, "method": info.get("method", "?"), "n": info["n"],
               "hist_median_lag": info["median_lag"], "hist_p90_lag": info["p90_lag"]}
        for k in checkpoints:
            row["day{}".format(k)] = round(100 * curve[min(k, len(curve) - 1)], 1)
        rows.append(row)
    return pd.DataFrame(rows).set_index("family")
