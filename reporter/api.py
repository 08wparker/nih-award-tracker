"""Paced client for the NIH RePORTER project-search API.

Everything here encodes a constraint verified against the live API, not the docs:

  POST https://api.reporter.nih.gov/v2/projects/search   (no auth)
  limit  <= 500        (501 -> HTTP 400)
  offset <= 14,999     (15,000 -> HTTP 400)

The offset ceiling is the sharp edge: a criteria set matching more than 15,000 records
cannot be paged to the end, and the API gives no warning -- you simply stop receiving
rows. `search` raises ResultSetTooLarge rather than returning a silently truncated
result, so callers are forced to partition. All-NIH Type-1 for a single fiscal year is
15,401 records, i.e. already over the line; partitioning by award family keeps the
largest partition (R01-equivalents) at roughly 4,600.

Request `criteria` keys are snake_case, `include_fields` are PascalCase, and response
keys come back snake_case. That inconsistency is the API's, not ours.
"""

import time
import warnings

import requests

# Local Python 3.9 links against LibreSSL, which urllib3 v2 warns about on import.
# Harmless here and pure noise in the logs.
warnings.filterwarnings("ignore", message=".*OpenSSL.*", module="urllib3")

SEARCH_URL = "https://api.reporter.nih.gov/v2/projects/search"

MAX_LIMIT = 500
MAX_OFFSET = 14_999
# Highest reachable record index. offset must stay <= MAX_OFFSET, so the last usable
# page starts at 14,999 -- but a page starting there still returns up to 500 rows.
MAX_REACHABLE = MAX_OFFSET + MAX_LIMIT

# ~1 req/s is the observed ceiling; a burst of 12 unpaced calls stalled the connection
# outright rather than returning 429. Pace generously -- the daily job is tiny anyway.
MIN_INTERVAL = 1.2

DEFAULT_FIELDS = [
    "ApplId", "ProjectNum", "ActivityCode", "AwardType", "FiscalYear",
    "AwardNoticeDate", "DateAdded", "AgencyIcAdmin", "AwardAmount",
    "ProjectTitle", "Organization", "ContactPiName",
]


class ReporterError(RuntimeError):
    """Any non-recoverable problem talking to RePORTER."""


class ResultSetTooLarge(ReporterError):
    """Criteria match more rows than the offset ceiling allows us to page through."""

    def __init__(self, total, criteria):
        super().__init__(
            "criteria match {:,} records, above the {:,} the API will page through; "
            "partition the query (by family, fiscal year, or date range). "
            "criteria={!r}".format(total, MAX_REACHABLE, criteria)
        )
        self.total = total
        self.criteria = criteria


class ReporterClient(object):
    """Rate-limited, retrying client. One instance per run; it holds the pacing clock."""

    def __init__(self, min_interval=MIN_INTERVAL, timeout=120, max_retries=5, log=None):
        self.min_interval = min_interval
        self.timeout = timeout
        self.max_retries = max_retries
        self.log = log or (lambda msg: None)
        self.session = requests.Session()
        self.session.headers.update(
            {"Content-Type": "application/json", "Accept": "application/json"}
        )
        self._last_call = 0.0
        self.request_count = 0

    def _wait(self):
        elapsed = time.time() - self._last_call
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)

    def _post(self, payload):
        """One paced POST with retry. Returns parsed JSON."""
        last_error = None
        for attempt in range(self.max_retries):
            self._wait()
            self._last_call = time.time()
            self.request_count += 1
            try:
                resp = self.session.post(SEARCH_URL, json=payload, timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = exc
            else:
                if resp.status_code == 200:
                    return resp.json()
                # 400 means we built a bad query -- retrying cannot help.
                if resp.status_code == 400:
                    raise ReporterError(
                        "HTTP 400 from RePORTER: {}".format(resp.text[:300])
                    )
                last_error = ReporterError(
                    "HTTP {}: {}".format(resp.status_code, resp.text[:200])
                )

            backoff = 2.0 * (2 ** attempt)
            self.log(
                "  retry {}/{} in {:.0f}s after {}".format(
                    attempt + 1, self.max_retries, backoff, last_error
                )
            )
            time.sleep(backoff)

        raise ReporterError("giving up after {} retries: {}".format(self.max_retries, last_error))

    def count(self, criteria):
        """Total records matching criteria, using a single 1-row request."""
        payload = {"criteria": criteria, "include_fields": ["ApplId"], "limit": 1, "offset": 0}
        return self._post(payload).get("meta", {}).get("total", 0)

    def search(self, criteria, include_fields=None, page_size=MAX_LIMIT):
        """Page through every record matching criteria.

        Raises ResultSetTooLarge if the result set exceeds what the offset ceiling
        permits, rather than returning a partial set.

        Sorting by appl_id is not cosmetic: paging an unsorted result set can drop or
        duplicate rows if the backend's ordering shifts between requests.
        """
        include_fields = include_fields or DEFAULT_FIELDS
        page_size = min(page_size, MAX_LIMIT)

        results = []
        offset = 0
        total = None

        while True:
            payload = {
                "criteria": criteria,
                "include_fields": include_fields,
                "limit": page_size,
                "offset": offset,
                "sort_field": "appl_id",
                "sort_order": "ASC",
            }
            data = self._post(payload)

            if total is None:
                total = data.get("meta", {}).get("total", 0)
                if total > MAX_REACHABLE:
                    raise ResultSetTooLarge(total, criteria)

            batch = data.get("results", [])
            results.extend(batch)

            if not batch or len(results) >= total:
                break

            offset += page_size
            if offset > MAX_OFFSET:
                # Defensive: the total check above should already have caught this.
                raise ResultSetTooLarge(total, criteria)

        return results


def build_criteria(activity_codes=None, fiscal_years=None, agencies=None,
                   award_types=("1",), notice_date=None, date_added=None):
    """Assemble a criteria dict, omitting empty filters.

    award_types defaults to ("1",) -- Type 1 is a brand-new award, as opposed to a
    Type 2 competing renewal. "New awards" throughout this project means Type 1.

    notice_date / date_added are (from, to) pairs of YYYY-MM-DD strings.
    """
    criteria = {}
    if activity_codes:
        criteria["activity_codes"] = list(activity_codes)
    if fiscal_years:
        criteria["fiscal_years"] = list(fiscal_years)
    if agencies:
        criteria["agencies"] = list(agencies)
    if award_types:
        criteria["award_types"] = list(award_types)
    if notice_date:
        criteria["award_notice_date"] = {
            "from_date": notice_date[0], "to_date": notice_date[1]
        }
    if date_added:
        criteria["date_added"] = {
            "from_date": date_added[0], "to_date": date_added[1]
        }
    return criteria
