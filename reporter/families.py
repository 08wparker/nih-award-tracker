"""Award families: the activity-code groupings we track.

A family is a *view* over activity codes, not a stored attribute. Awards are stored
once keyed on appl_id with their activity_code; family membership is derived. That
matters because the groups are not disjoint -- F32 is both its own tracked family and
a member of F-series -- and storing membership would duplicate rows.

FETCH_GROUPS is the disjoint set actually pulled from the API. F32 needs no separate
request because it falls out of the F-series pull.
"""

# NIH's R01-equivalent definition. Reverse-engineered against RePORTER's own
# "R01 Equivalents" activity-code filter and verified exact: for NHLBI Type-1 awards
# this list reproduces the 5 Aug 2026 web export to the award in every closed fiscal
# year (FY2022 651, FY2023 646, FY2024 659, FY2025 621).
#
# R56 is deliberately absent. R56 ("High Priority, Short-Term Project Award") is a
# bridge award for strong applications that missed the payline; RePORTER excludes it
# from R01 Equivalents, and including it inflated NHLBI's counts by 138 awards across
# FY2022-25. We still fetch and store R56 (see R01_EQUIVALENT_FETCH) so it is
# recoverable, but it is not reported as an R01-equivalent.
R01_EQUIVALENT = [
    "R01", "R37", "RF1", "RL1", "U01", "DP1", "DP2", "DP5", "R35", "RM1",
]

# Superset actually requested from the API, so the store retains R56 for later use.
R01_EQUIVALENT_FETCH = R01_EQUIVALENT + ["R56"]

# Career development awards.
K_SERIES = [
    "K01", "K02", "K05", "K07", "K08", "K12", "K18", "K22", "K23", "K24", "K25",
    "K26", "K38", "K43", "K76", "K99", "KL2",
]

# Individual fellowships.
F_SERIES = ["F30", "F31", "F32", "F33", "F99"]

# What we actually request from the API. Disjoint, so no award is fetched twice.
FETCH_GROUPS = {
    "r01_equivalent": R01_EQUIVALENT_FETCH,
    "k_series": K_SERIES,
    "f_series": F_SERIES,
}

# What we report on. F32 is broken out because its reporting lag is a different regime
# from the rest of the F-series (median 58 days vs 16 for F99), so pooling it into a
# single F completeness curve would badly mis-correct both.
FAMILIES = {
    "r01_equivalent": R01_EQUIVALENT,
    "k_series": K_SERIES,
    "f_series": F_SERIES,
    "f32": ["F32"],
}

FAMILY_LABELS = {
    "r01_equivalent": "R01-equivalents",
    "k_series": "K-series",
    "f_series": "F-series (fellowships)",
    "f32": "F32 (postdoctoral)",
}

# Display order in the dashboard's family selector.
FAMILY_ORDER = ["r01_equivalent", "k_series", "f_series", "f32"]

_CODE_TO_FAMILIES = {}
for _fam, _codes in FAMILIES.items():
    for _code in _codes:
        _CODE_TO_FAMILIES.setdefault(_code, []).append(_fam)

# Every activity code we know about, across all fetch groups.
ALL_CODES = sorted({c for codes in FETCH_GROUPS.values() for c in codes})


def families_for(activity_code):
    """Return the list of family keys an activity code belongs to (may be empty)."""
    return _CODE_TO_FAMILIES.get(activity_code, [])


def codes_for(family):
    """Return the activity codes making up a family."""
    return FAMILIES[family]
