"""Talks to the public Algolia index that powers ycombinator.com, plus the
Work at a Startup job board.

No login, no API key of your own, no browser. YC rotates the search key
periodically, so we re-extract it from the live site on every run and only
fall back to the pinned defaults if that fails.

Work at a Startup retired its public Algolia job index (WaaSPublicJob_production
now 404s). The only unauthenticated jobs surface left is the server-rendered
board itself, which shows a curated preview per role category rather than the
full 1,000+-company listing -- logging in is required for that. `jobs()` below
scrapes that preview instead.
"""

from __future__ import annotations

import html
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Iterator

import requests

# Pinned fallbacks. If YC rotates these, extraction below should pick up the
# new ones automatically -- these only matter if the page layout changes too.
DEFAULT_APP_ID = "45BWZJ1SGC"
DEFAULT_API_KEY = "NDYwZTA4ZTUxMjhjMzMwNzhjMTIwZjNmOTFlMWIxNGE3NzcyNTNmMGE0ZTE4NDNmYzIzYTFhMTNlM2FiNDFhOA=="
COMPANY_INDEX = "YCCompany_production"

DIRECTORY_URL = "https://www.ycombinator.com/companies"
USER_AGENT = "yc-radar/1.0 (+research; contact: you@example.com)"

# Algolia hard-caps any single query at 1000 hits, so broad sweeps must be
# partitioned. Batch is the cleanest partition key for YC.
ALGOLIA_HIT_CAP = 1000

# workatastartup.com/jobs has no query-string filtering or pagination for
# logged-out visitors; each role category is a separate curated page instead.
# This is a preview, not the full board -- sweeping all of them is the closest
# an anonymous, no-login client can get.
JOBS_BASE_URL = "https://www.workatastartup.com"
JOB_ROLE_PATHS = (
    "/jobs/l/software-engineer", "/jobs/l/designer", "/jobs/l/recruiting",
    "/jobs/l/science", "/jobs/l/product-manager", "/jobs/l/operations",
    "/jobs/l/sales-manager", "/jobs/l/marketing", "/jobs/l/legal", "/jobs/l/finance",
)

# The job board renders batches as short codes (W26, S26, F26, P26 for Spring --
# "S" was already Summer's) instead of the "Winter 2026" form the company index
# and this project's --batches flag use.
_BATCH_SEASON = {"W": "Winter", "S": "Summer", "F": "Fall", "P": "Spring"}

_DATA_PAGE_RE = re.compile(r'data-page="(.*?)"\s+id=', re.S)
_SALARY_RANGE_RE = re.compile(r'\$([\d.]+)K\s*-\s*\$([\d.]+)K')


@dataclass
class AlgoliaCreds:
    app_id: str
    api_key: str
    source: str = "fallback"

    @property
    def host(self) -> str:
        return f"https://{self.app_id.lower()}-dsn.algolia.net"


def discover_creds(session: requests.Session) -> AlgoliaCreds:
    """Scrape the current Algolia app id + search key off the YC directory page.

    The key is a public, read-only search key embedded in YC's own frontend
    bundle -- the same one your browser uses when you visit the directory.
    """
    try:
        page_html = session.get(DIRECTORY_URL, timeout=30).text
        opts = re.search(r'window\.AlgoliaOpts\s*=\s*\{["\']app["\']\s*:\s*["\']([A-Z0-9]+)["\']\s*,\s*["\']key["\']\s*:\s*["\']([A-Za-z0-9=+/]+)["\']\}', page_html)
        if opts:
            return AlgoliaCreds(opts.group(1), opts.group(2), source="live")
    except requests.RequestException:
        pass
    return AlgoliaCreds(DEFAULT_APP_ID, DEFAULT_API_KEY, source="fallback")


class YCIndex:
    def __init__(self, creds: AlgoliaCreds, session: requests.Session, delay: float = 0.25):
        self.creds = creds
        self.session = session
        self.delay = delay

    def _query(self, index: str, params: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.creds.host}/1/indexes/{index}/query"
        headers = {
            "X-Algolia-Application-Id": self.creds.app_id,
            "X-Algolia-API-Key": self.creds.api_key,
            "Content-Type": "application/json",
        }
        body = {"params": "&".join(f"{k}={v}" for k, v in params.items())}
        resp = self.session.post(url, json=body, headers=headers, timeout=30)
        resp.raise_for_status()
        time.sleep(self.delay)  # be a polite guest
        return resp.json()

    def _paginate(self, index: str, filters: str = "", hits_per_page: int = 500) -> Iterator[dict]:
        page = 0
        while True:
            params = {"hitsPerPage": hits_per_page, "page": page}
            if filters:
                params["filters"] = requests.utils.quote(filters)
            data = self._query(index, params)
            hits = data.get("hits", [])
            yield from hits
            page += 1
            if page >= data.get("nbPages", 0) or page * hits_per_page >= ALGOLIA_HIT_CAP:
                break

    def companies(self, batches: list[str]) -> list[dict]:
        """Sweep one batch at a time to stay under the 1000-hit cap."""
        out, seen = [], set()
        for batch in batches:
            for hit in self._paginate(COMPANY_INDEX, filters=f'batch:"{batch}"'):
                cid = str(hit.get("id") or hit.get("objectID"))
                if cid in seen:
                    continue
                seen.add(cid)
                out.append(_normalise_company(hit))
        return out

    def jobs(self, batches: list[str]) -> list[dict]:
        """Sweep the logged-out job board's role-category pages.

        Not Algolia -- see the module docstring. `batches` filters the
        (already-partial) preview by company batch; pass an empty list to
        keep everything the board shows.
        """
        wanted = set(batches) if batches else None
        out, seen = [], set()
        for path in JOB_ROLE_PATHS:
            try:
                resp = self.session.get(f"{JOBS_BASE_URL}{path}", timeout=30)
                resp.raise_for_status()
                props = _inertia_props(resp.text)
            except (requests.RequestException, ValueError):
                # One role category being unreachable shouldn't sink the rest.
                continue
            finally:
                time.sleep(self.delay)  # be a polite guest

            for hit in props.get("jobs", []):
                jid = str(hit.get("id"))
                if jid in seen:
                    continue
                seen.add(jid)
                batch = _expand_batch_code(hit.get("companyBatch"))
                if wanted is not None and batch not in wanted:
                    continue
                out.append(_normalise_job_web(hit))
        return out


def _normalise_company(hit: dict) -> dict:
    slug = hit.get("slug") or ""
    return {
        "id": str(hit.get("id") or hit.get("objectID")),
        "name": hit.get("name"),
        "slug": slug,
        "batch": hit.get("batch"),
        "status": hit.get("status"),
        "team_size": _int_or_none(hit.get("team_size")),
        "website": hit.get("website"),
        "one_liner": hit.get("one_liner"),
        "industry": hit.get("industry"),
        "location": hit.get("all_locations") or hit.get("location"),
        "stage": hit.get("stage"),
        # Stored as 0/1, not bool -- SQLite round-trips booleans as ints, and
        # the change-detection in store.py does a str(old) != str(new)
        # comparison that would otherwise misfire every run ("1" != "True").
        "is_hiring": int(bool(hit.get("isHiring"))),
        "top_company": int(bool(hit.get("top_company"))),
        # Launch YC post date -- the closest free, no-login signal YC's own
        # data has to "this company shipped/launched something publicly".
        "launched_at": _int_or_none(hit.get("launched_at")),
        "yc_url": f"https://www.ycombinator.com/companies/{slug}" if slug else None,
    }


def _inertia_props(page_html: str) -> dict:
    """Pull the `props` object out of an Inertia.js `data-page` attribute."""
    m = _DATA_PAGE_RE.search(page_html)
    if not m:
        raise ValueError("no Inertia data-page payload found")
    return json.loads(html.unescape(m.group(1)))["props"]


def _expand_batch_code(code: str | None) -> str | None:
    if not code:
        return code
    season = _BATCH_SEASON.get(code[0])
    if not season or not code[1:].isdigit():
        return code
    return f"{season} 20{code[1:]}"


def _parse_salary_range(s: str | None) -> tuple[int | None, int | None]:
    if not s:
        return None, None
    m = _SALARY_RANGE_RE.search(s)
    if not m:
        return None, None
    return int(float(m.group(1)) * 1000), int(float(m.group(2)) * 1000)


def _normalise_job_web(hit: dict) -> dict:
    jid = str(hit.get("id"))
    min_salary, max_salary = _parse_salary_range(hit.get("salary"))
    return {
        "id": jid,
        "company_id": hit.get("companySlug") or "",
        "company_name": hit.get("companyName"),
        "title": hit.get("title"),
        "role_type": hit.get("roleType"),
        "location": hit.get("location"),
        "min_salary": min_salary,
        "max_salary": max_salary,
        "url": f"{JOBS_BASE_URL}/jobs/{jid}",
    }


def _int_or_none(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    # workatastartup.com 406s requests with the bare default Accept: */*,
    # identifying UA and all -- it just wants a real-looking Accept header.
    s.headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    s.headers["Accept-Language"] = "en-US,en;q=0.9"
    return s
