"""Tracking new NIH awards from the RePORTER API, corrected for reporting lag."""

DATA_DIR = "data"
AWARDS_PATH = "data/awards.csv.gz"
COMPLETENESS_PATH = "data/completeness.json"
STATE_PATH = "data/state.json"
DASHBOARD_DATA_PATH = "docs/data.json"

# Fiscal years the dashboard covers. FY2022 is the earliest year in the original export.
FISCAL_YEARS = [2022, 2023, 2024, 2025, 2026]
