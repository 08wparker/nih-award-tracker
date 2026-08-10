"""Weekly new NHLBI R01-equivalent awards, one line per fiscal year.

The static figure, for slides and print. The interactive version covering every
institute and award family lives in docs/ -- this script is deliberately kept as a
single self-contained PNG of the original question.

Reads the API-backed store (data/awards.csv.gz), so it stays current with the daily
refresh; run scripts/bootstrap.py once if the store is missing. Awards are dated by
award notice date and binned into fiscal weeks (week 1 = Oct 1-7) so years overlay on
a comparable calendar.

    python3 plot_nhlbi_r01.py
"""

import os
import sys

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.ticker import MultipleLocator

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from reporter import AWARDS_PATH, FISCAL_YEARS  # noqa: E402
from reporter import fiscal, store  # noqa: E402
from reporter.families import R01_EQUIVALENT  # noqa: E402

OUT = "nhlbi_r01_weekly_by_fy.png"
IC = "NHLBI"

# Palette: ordinal blue ramp for the settled baseline years (light=oldest ->
# dark=newest), warm highlights for the two years of interest -- rose for FY2025,
# orange for the in-progress FY2026. Validated via dataviz/validate_palette.js:
# blues as a 3-step ordinal ramp ALL PASS; rose vs orange CVD dE 13.4-52.6 and rose
# vs the ramp dE 41.9-97.1, both ALL PASS including 3:1 contrast on the surface.
COLORS = {
    2022: "#86b6ef", 2023: "#2a78d6", 2024: "#184f95",  # blue ordinal ramp
    2025: "#d55181",                                    # rose highlight
    2026: "#eb6834",                                    # orange highlight
}
HIGHLIGHT = {2025, 2026}
INK, INK_2, INK_3 = "#0b0b0b", "#52514e", "#8a8880"
GRID = "#e6e5e1"

# ---------------------------------------------------------------- load & shape
if not os.path.exists(AWARDS_PATH):
    sys.exit("no award store at {}; run: python3 scripts/bootstrap.py".format(AWARDS_PATH))

raw = store.load(AWARDS_PATH)
raw = raw[
    (raw["ic"] == IC)
    & raw["activity_code"].isin(R01_EQUIVALENT)
    & raw["fiscal_year"].isin(FISCAL_YEARS)
    & raw["award_notice_date"].notna()
].copy()

raw["notice"] = raw["award_notice_date"]
raw["fy"] = raw["fiscal_year"].astype(int)
raw["fweek"] = fiscal.fiscal_week(raw["notice"], raw["fy"])
raw = raw[raw["fweek"].notna()]

years = sorted(raw["fy"].unique())
weekly = (
    raw.pivot_table(index="fweek", columns="fy", values="appl_id", aggfunc="count")
    .reindex(range(1, 53))
    .fillna(0)
)

# Truncate the in-progress year at its last observed award week; a flat tail would
# read as "NHLBI stopped funding" rather than "no data yet".
current_fy = max(years)
last_notice = raw.loc[raw["fy"] == current_fy, "notice"].max()
last_week = int(raw.loc[raw["fy"] == current_fy, "fweek"].max())
cumulative = weekly.cumsum()
cumulative.loc[last_week + 1 :, current_fy] = pd.NA
weekly.loc[last_week + 1 :, current_fy] = pd.NA

# Month gridline positions, from a non-leap fiscal year (Oct 2022 - Sep 2023).
ref = pd.Timestamp("2022-10-01")
months = [
    (((pd.Timestamp(f"{y}-{m:02d}-01") - ref).days // 7) + 1, pd.Timestamp(f"{y}-{m:02d}-01").strftime("%b"))
    for y, m in [(2022, 10), (2022, 11), (2022, 12)] + [(2023, m) for m in range(1, 10)]
]

# ---------------------------------------------------------------------- render
fig, (ax1, ax2) = plt.subplots(
    2, 1, figsize=(11.5, 8.8), sharex=True, gridspec_kw={"height_ratios": [1, 1], "hspace": 0.22}
)

for ax in (ax1, ax2):
    ax.set_facecolor("#fcfcfb")
    ax.grid(axis="y", color=GRID, lw=0.8, zorder=0)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=INK_2, length=0, labelsize=10)
    for wk, _ in months:
        ax.axvline(wk, color=GRID, lw=0.8, zorder=0)
    # Mark the stretch of the fiscal year the current-year export doesn't cover yet.
    ax.axvspan(last_week, 52, color="#f0efec", alpha=0.75, lw=0, zorder=1)

ends = []
for fy in years:
    is_current = fy == current_fy
    color = COLORS[fy]
    lw, z = (2.6, 6) if is_current else ((2.2, 5) if fy in HIGHLIGHT else (1.6, 3))
    style = dict(color=color, lw=lw, zorder=z, solid_capstyle="round")
    ax1.plot(weekly.index, weekly[fy], **style, label=f"FY{fy}")
    ax2.plot(cumulative.index, cumulative[fy], **style)

    end_wk = last_week if is_current else 52
    ends.append((fy, end_wk, int(cumulative.loc[end_wk, fy]), color, fy in HIGHLIGHT))

# Direct label every cumulative line -> identity never rests on color alone.
# FY22-24 finish within 13 awards of each other, so nudge labels apart vertically.
gap = 0.065 * max(t for _, _, t, _, _ in ends)
placed = []
for fy, end_wk, total, color, is_hl in sorted(ends, key=lambda e: e[2]):
    y = total if not placed else max(total, placed[-1] + gap)
    placed.append(y)
    ax2.plot([end_wk], [total], "o", ms=6, color=color, mec="#fcfcfb", mew=1.6, zorder=7)
    ax2.annotate(
        f"FY{fy}   {total}", (end_wk, total), xytext=(end_wk + 1.8, y),
        textcoords="data", va="center", fontsize=10.5,
        color=INK if is_hl else INK_2, fontweight="bold" if is_hl else "normal",
        arrowprops=dict(arrowstyle="-", color=color, lw=0.8, shrinkA=1, shrinkB=2),
    )

# Panel 1: weekly counts
ax1.set_title(
    "New NHLBI R01-equivalent awards per week", loc="left", fontsize=14,
    color=INK, fontweight="bold", pad=32,
)
ax1.set_ylabel("Awards issued that week", fontsize=10.5, color=INK_2)
ax1.yaxis.set_major_locator(MultipleLocator(10))
ax1.set_ylim(0, weekly.max().max() * 1.06)
ax1.legend(  # above the plot area so it never sits on top of a September spike
    frameon=False, ncol=5, fontsize=10.5, labelcolor=INK_2, handlelength=1.6,
    columnspacing=1.6, loc="lower left", bbox_to_anchor=(0, 1.02), borderaxespad=0,
)

# Panel 2: cumulative
ax2.set_title("Cumulative through the fiscal year", loc="left", fontsize=12, color=INK_2, pad=10)
ax2.set_ylabel("Awards issued to date", fontsize=10.5, color=INK_2)
ax2.set_xlim(1, 63)
ax2.set_ylim(0, max(placed) * 1.05)
ax2.set_xticks([wk for wk, _ in months])
ax2.set_xticklabels([lab for _, lab in months])
ax2.set_xlabel("Fiscal year week (Oct 1 = week 1)", fontsize=10.5, color=INK_2)

fig.text(
    0.012, 0.026,
    f"Source: NIH RePORTER API, data as of {raw['date_added'].max():%d %b %Y} — new (Type 1) R01-equivalent awards "
    "administered by NHLBI, dated by award notice date.",
    fontsize=8.5, color=INK_3,
)
fig.text(
    0.012, 0.006,
    f"FY{current_fy} is incomplete: shown through {last_notice:%d %b %Y} "
    f"(fiscal week {last_week} of 52). Shaded band = no FY{current_fy} data yet.",
    fontsize=8.5, color=INK_3,
)
fig.patch.set_facecolor("#fcfcfb")
fig.tight_layout(rect=[0, 0.045, 1, 1])
fig.savefig(OUT, dpi=200, facecolor=fig.get_facecolor())

# --------------------------------------------------------------------- tidy out
weekly.rename_axis("fiscal_week").to_csv("nhlbi_r01_weekly_counts.csv")

print(f"wrote {OUT}")
print("totals by FY:\n", raw.groupby("fy").size().to_string())
print(f"\nFY{current_fy} through week {last_week} ({last_notice:%Y-%m-%d})")
print("first award notice date by FY:\n", raw.groupby("fy")["notice"].min().to_string())
print("\ncount through week", last_week, "in each FY:\n",
      cumulative.loc[last_week].dropna().astype(int).to_string())
print("\npeak week:\n", weekly.idxmax().to_string())
