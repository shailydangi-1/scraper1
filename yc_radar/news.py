"""Best-effort funding-news signal via Google News' public RSS search.

YC's own company data has no funding fields at all -- no raise amount, no
investors, no round. There's no free, no-login YC source for this, so this
module reaches outside YC entirely. Google News RSS needs no API key and
costs nothing, but its `q=` parameter is a fuzzy relevance search, not a
strict boolean AND/OR engine -- quoting and grouping terms with parentheses
gets mostly ignored, so a query alone can't guarantee results even mention
the company. The title filter below is what actually enforces that: a hit
must contain the company name *and* a dollar amount anchored next to a
funding word. That second half matters more than it looks -- "raises" alone
also means "raises concerns" or "raises a flag", and plenty of YC company
names (Captain, Signals, Fort, Forum, Crow) are common English words that
collide constantly with unrelated headlines. Requiring a nearby dollar
figure is what tells routine word-collisions apart from an actual raise.

Even with both checks this is still a lead to verify, not a confirmed fact --
a same-named unrelated company with its own funding news will still pass.
"""

from __future__ import annotations

import re
from xml.etree import ElementTree
from typing import Any

import requests

NEWS_RSS_URL = "https://news.google.com/rss/search"

# A dollar figure adjacent to a funding word, in either order ("raises $5M"
# or "$5M ... funding round"). This is what separates a real raise from
# idiomatic uses of "raises"/"signals"/etc. that share no dollar amount.
_DOLLAR_FUNDING_RE = re.compile(
    r'\$[\d,.]+\s*(?:k|m|b|mm|bn|million|billion)?\b.{0,50}\b(raise|raised|raises|funding|series|round|valuation)\b'
    r'|\b(raise|raised|raises|secures|closes|lands|nets)\b.{0,30}\$[\d,.]+',
    re.IGNORECASE,
)


def search_funding_news(session: requests.Session, company_name: str, max_results: int = 5) -> list[dict[str, Any]]:
    query = f'"{company_name}" funding raises series valuation'
    params = {"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"}
    try:
        resp = session.get(NEWS_RSS_URL, params=params, timeout=30)
        resp.raise_for_status()
        root = ElementTree.fromstring(resp.content)
    except (requests.RequestException, ElementTree.ParseError):
        return []

    out = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        if not title or not _looks_like_funding_news(company_name, title):
            continue
        link = (item.findtext("link") or "").strip()
        if not link:
            continue
        source_el = item.find("source")
        out.append({
            "title": title,
            "link": link,
            "published": (item.findtext("pubDate") or "").strip() or None,
            "source": (source_el.text or "").strip() if source_el is not None else None,
        })
        if len(out) >= max_results:
            break
    return out


def _looks_like_funding_news(company_name: str, title: str) -> bool:
    # Plain substring/word-boundary checks still match a name inside a
    # hyphenated compound ("Grade" in "enterprise-grade", "Squid" in
    # "SQUID-Based") since \b treats "-" as a separator in its own right.
    # Requiring non-word-and-non-hyphen on both sides rules those out while
    # still matching normal punctuation/whitespace-delimited mentions.
    name_re = re.compile(rf'(?<![\w-]){re.escape(company_name)}(?![\w-])', re.IGNORECASE)
    if not name_re.search(title):
        return False
    return bool(_DOLLAR_FUNDING_RE.search(title))
