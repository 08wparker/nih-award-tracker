"""Award-level store: one row per appl_id, upserted idempotently.

Design notes:

* **Keyed on appl_id alone.** Family membership is derived from activity_code at read
  time (see families.py), so the non-disjoint F32/F-series overlap costs no duplicate
  rows and a re-classification never requires a refetch.

* **`first_seen` is ours, `date_added` is theirs.** RePORTER's `date_added` is what the
  whole nowcast rests on, but its semantics are undocumented -- nothing promises it
  won't be rewritten when a record is revised. So we independently stamp the date we
  first observed each award and, on every upsert, count how many stored `date_added`
  values changed. If that count is ever non-trivial, `date_added` is not the immutable
  ingestion timestamp we assume and the nowcast should switch to `first_seen`.
  `check_date_added_stability` reports this.
"""

import os

import pandas as pd

COLUMNS = [
    "appl_id", "project_num", "activity_code", "award_type", "fiscal_year",
    "ic", "award_notice_date", "date_added", "first_seen", "award_amount",
    "organization", "contact_pi_name", "project_title",
]

DATE_COLUMNS = ["award_notice_date", "date_added", "first_seen"]


def _coerce_date(series):
    """Parse to naive dates. RePORTER returns ISO datetimes at midnight, sometimes tz-aware."""
    parsed = pd.to_datetime(series, errors="coerce", utc=True)
    return parsed.dt.tz_localize(None).dt.normalize()


def normalize(records, observed_on):
    """Turn raw API dicts into a typed DataFrame matching COLUMNS."""
    if not records:
        return pd.DataFrame(columns=COLUMNS)

    raw = pd.DataFrame(records)
    out = pd.DataFrame(index=raw.index)

    out["appl_id"] = pd.to_numeric(raw.get("appl_id"), errors="coerce").astype("Int64")
    out["project_num"] = raw.get("project_num")
    out["activity_code"] = raw.get("activity_code")
    out["award_type"] = raw.get("award_type").astype(str) if "award_type" in raw else None
    out["fiscal_year"] = pd.to_numeric(raw.get("fiscal_year"), errors="coerce").astype("Int64")

    # agency_ic_admin is a nested object; the abbreviation is the IC identity we group on.
    if "agency_ic_admin" in raw:
        out["ic"] = raw["agency_ic_admin"].apply(
            lambda d: (d or {}).get("abbreviation") if isinstance(d, dict) else None
        )
    else:
        out["ic"] = None

    if "organization" in raw:
        out["organization"] = raw["organization"].apply(
            lambda d: (d or {}).get("org_name") if isinstance(d, dict) else None
        )
    else:
        out["organization"] = None

    out["award_notice_date"] = _coerce_date(raw.get("award_notice_date"))
    out["date_added"] = _coerce_date(raw.get("date_added"))
    out["first_seen"] = pd.Timestamp(observed_on).normalize()
    out["award_amount"] = pd.to_numeric(raw.get("award_amount"), errors="coerce")
    out["contact_pi_name"] = raw.get("contact_pi_name")
    out["project_title"] = raw.get("project_title")

    out = out.dropna(subset=["appl_id"])
    return out[COLUMNS]


def load(path):
    """Read the store, or an empty frame shaped like it if absent."""
    if not os.path.exists(path):
        return pd.DataFrame(columns=COLUMNS)
    df = pd.read_csv(path, compression="infer", low_memory=False)
    for col in DATE_COLUMNS:
        if col in df:
            df[col] = _coerce_date(df[col])
    df["appl_id"] = pd.to_numeric(df["appl_id"], errors="coerce").astype("Int64")
    df["fiscal_year"] = pd.to_numeric(df["fiscal_year"], errors="coerce").astype("Int64")
    if "award_type" in df:
        df["award_type"] = df["award_type"].astype(str)
    return df[COLUMNS]


def save(df, path):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    out = df.sort_values("appl_id").copy()
    for col in DATE_COLUMNS:
        out[col] = pd.to_datetime(out[col]).dt.strftime("%Y-%m-%d")
    out.to_csv(path, index=False, compression="gzip")


def upsert(existing, incoming):
    """Merge incoming rows into existing, preserving each award's original first_seen.

    Returns (merged, stats). Idempotent: upserting the same records twice yields the
    same frame and reports zero new rows the second time.
    """
    if existing is None or existing.empty:
        merged = incoming.drop_duplicates(subset="appl_id", keep="last").copy()
        return merged, {
            "new": len(merged), "updated": 0, "unchanged": 0, "date_added_changed": 0
        }

    incoming = incoming.drop_duplicates(subset="appl_id", keep="last")
    prior = existing.set_index("appl_id")
    fresh = incoming.set_index("appl_id")

    known = fresh.index.intersection(prior.index)
    new_ids = fresh.index.difference(prior.index)

    # Preserve the date we first observed each award; only new awards get today's stamp.
    fresh.loc[known, "first_seen"] = prior.loc[known, "first_seen"]

    drift = 0
    if len(known):
        before = prior.loc[known, "date_added"]
        after = fresh.loc[known, "date_added"]
        drift = int((before.notna() & after.notna() & (before != after)).sum())

    # Compare on the substantive columns to distinguish real revisions from no-ops.
    compare_cols = [c for c in COLUMNS if c not in ("appl_id", "first_seen")]
    changed = 0
    if len(known):
        b = prior.loc[known, compare_cols].astype(str)
        a = fresh.loc[known, compare_cols].astype(str)
        changed = int((b != a).any(axis=1).sum())

    combined = prior.copy()
    combined.loc[known] = fresh.loc[known]
    if len(new_ids):
        combined = pd.concat([combined, fresh.loc[new_ids]])

    merged = combined.reset_index()[COLUMNS]
    stats = {
        "new": int(len(new_ids)),
        "updated": changed,
        "unchanged": int(len(known)) - changed,
        "date_added_changed": drift,
    }
    return merged, stats


def check_date_added_stability(stats):
    """Warn if RePORTER appears to rewrite date_added, invalidating the nowcast basis."""
    drift = stats.get("date_added_changed", 0)
    seen = drift + stats.get("unchanged", 0) + stats.get("updated", 0)
    if seen and drift / float(seen) > 0.01:
        return (
            "WARNING: date_added changed on {} of {} previously-seen awards ({:.1%}). "
            "It may not be an immutable ingestion timestamp; consider basing the "
            "completeness curve on first_seen instead.".format(drift, seen, drift / float(seen))
        )
    return None
