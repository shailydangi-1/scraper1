"""Pulls founders + social handles off individual YC company profile pages.

The directory index doesn't carry founder rosters -- those live on the profile
page. YC has shipped several frontend frameworks over the years, so we try the
structured payloads first and fall back to link extraction.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

import requests

LINKEDIN_RE = re.compile(r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/in/[A-Za-z0-9\-_%]+")
TWITTER_RE = re.compile(r"https?://(?:www\.)?(?:twitter|x)\.com/([A-Za-z0-9_]{1,15})(?:[/?#]|$)")

NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
INERTIA_RE = re.compile(r'data-page="([^"]+)"')


def fetch_founders(session: requests.Session, yc_url: str, delay: float = 0.5) -> list[dict]:
    try:
        html = session.get(yc_url, timeout=30).text
    except requests.RequestException:
        return []
    time.sleep(delay)

    for extractor in (_from_next_data, _from_inertia):
        founders = extractor(html)
        if founders:
            return founders
    return _from_raw_links(html)


def _from_next_data(html: str) -> list[dict]:
    m = NEXT_DATA_RE.search(html)
    if not m:
        return []
    try:
        return _walk_for_founders(json.loads(m.group(1)))
    except json.JSONDecodeError:
        return []


def _from_inertia(html: str) -> list[dict]:
    m = INERTIA_RE.search(html)
    if not m:
        return []
    try:
        import html as html_mod

        return _walk_for_founders(json.loads(html_mod.unescape(m.group(1))))
    except (json.JSONDecodeError, ValueError):
        return []


def _walk_for_founders(obj: Any) -> list[dict]:
    """Depth-first hunt for a 'founders' array anywhere in the payload.

    Structure-agnostic on purpose: YC moves this key around between redesigns
    and we'd rather find it wherever it landed than pin an exact path.
    """
    if isinstance(obj, dict):
        for key in ("founders", "activeFounders", "people"):
            val = obj.get(key)
            if isinstance(val, list) and val and isinstance(val[0], dict):
                parsed = [_parse_founder(f) for f in val]
                if any(p["name"] for p in parsed):
                    return parsed
        for v in obj.values():
            found = _walk_for_founders(v)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _walk_for_founders(v)
            if found:
                return found
    return []


def _parse_founder(f: dict) -> dict:
    blob = json.dumps(f)
    li = LINKEDIN_RE.search(blob)
    tw = TWITTER_RE.search(blob)
    return {
        "name": f.get("full_name") or f.get("name") or f.get("fullName"),
        "title": f.get("title") or f.get("role"),
        "linkedin": li.group(0) if li else None,
        "twitter": tw.group(1) if tw else None,
    }


def _from_raw_links(html: str) -> list[dict]:
    """Last resort: we know there are founders, we just can't name them."""
    lis = sorted(set(LINKEDIN_RE.findall(html)))
    tws = sorted({h for h in TWITTER_RE.findall(html) if h.lower() != "ycombinator"})
    if not lis and not tws:
        return []
    out = [{"name": None, "title": None, "linkedin": u, "twitter": None} for u in lis]
    out += [{"name": None, "title": None, "linkedin": None, "twitter": h} for h in tws]
    return out
