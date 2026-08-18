"""Renders the day's deltas as markdown. Empty sections are dropped."""

from __future__ import annotations

from datetime import date, datetime, timezone


def render(companies: dict, jobs: dict, founders: list[dict], batches: list[str],
           funding: list[dict] | None = None) -> str:
    parts = [f"# YC Radar — {date.today():%d %b %Y}", ""]
    body = []

    if companies["new"]:
        body.append(f"## {len(companies['new'])} new companies")
        for c in sorted(companies["new"], key=lambda x: x.get("batch") or ""):
            body.append(f"- **[{c['name']}]({c['yc_url']})** ({c['batch']}) — {c.get('one_liner') or '—'}")
        body.append("")

    growth = [c for c in companies["changed"] if c["field"] == "team_size"]
    if growth:
        # Biggest movers first -- a 4 -> 30 jump matters more than 40 -> 41.
        growth.sort(key=lambda c: _delta(c), reverse=True)
        body.append("## Team size changes")
        for c in growth:
            d = _delta(c)
            arrow = "↑" if d > 0 else "↓"
            body.append(f"- **{c['company_name']}** {c['old_value']} → {c['new_value']} ({arrow}{abs(d)})")
        body.append("")

    status = [c for c in companies["changed"] if c["field"] == "status"]
    if status:
        body.append("## Status changes")
        for c in status:
            body.append(f"- **{c['company_name']}**: {c['old_value']} → {c['new_value']}")
        body.append("")

    launches = [c for c in companies["changed"] if c["field"] == "launched_at"]
    if launches:
        body.append(f"## {len(launches)} product launches")
        for c in launches:
            body.append(f"- **{c['company_name']}** launched {_fmt_launch_date(c['new_value'])}")
        body.append("")

    stage = [c for c in companies["changed"] if c["field"] == "stage"]
    if stage:
        body.append("## Stage changes")
        for c in stage:
            body.append(f"- **{c['company_name']}**: {c['old_value']} → {c['new_value']}")
        body.append("")

    hiring = [c for c in companies["changed"] if c["field"] == "is_hiring"]
    if hiring:
        started = [c for c in hiring if c["new_value"] in ("1", 1, True)]
        stopped = [c for c in hiring if c not in started]
        if started or stopped:
            body.append("## Hiring status changes")
            for c in started:
                body.append(f"- **{c['company_name']}** started actively hiring")
            for c in stopped:
                body.append(f"- **{c['company_name']}** stopped actively hiring")
            body.append("")

    top_company = [c for c in companies["changed"] if c["field"] == "top_company"]
    gained_top = [c for c in top_company if c["new_value"] in ("1", 1, True)]
    if gained_top:
        body.append("## Top Company flag changes")
        for c in gained_top:
            body.append(f"- **{c['company_name']}** was flagged a Top Company")
        body.append("")

    if jobs["new"]:
        body.append(f"## {len(jobs['new'])} new roles")
        by_company: dict[str, list] = {}
        for j in jobs["new"]:
            by_company.setdefault(j.get("company_name") or "Unknown", []).append(j)
        # Companies opening several roles at once are the real hiring signal.
        for name, roles in sorted(by_company.items(), key=lambda kv: -len(kv[1])):
            flag = "  🔥" if len(roles) >= 3 else ""
            body.append(f"- **{name}** ({len(roles)}){flag}")
            for r in roles:
                body.append(f"    - [{r['title']}]({r['url']}) — {_loc(r)}{_pay(r)}")
        body.append("")

    if jobs["closed"]:
        body.append(f"## {len(jobs['closed'])} roles closed")
        for j in jobs["closed"][:25]:
            body.append(f"- {j.get('company_name')} — {j.get('title')}")
        body.append("")

    if founders:
        body.append(f"## {len(founders)} new founders indexed")
        for f in founders[:40]:
            links = " · ".join(
                x for x in (
                    f"[in]({f['linkedin']})" if f.get("linkedin") else "",
                    f"[x](https://x.com/{f['twitter']})" if f.get("twitter") else "",
                ) if x
            )
            body.append(f"- {f.get('name') or 'Unknown'} — {links or 'no socials listed'}")
        body.append("")

    if funding:
        body.append(f"## {len(funding)} funding mentions (unverified — headline match only)")
        for m in funding:
            src = f" ({m['source']})" if m.get("source") else ""
            body.append(f"- **{m['company_name']}**: [{m['title']}]({m['link']}){src}")
        body.append("")

    if not body:
        body = ["Nothing changed today.", ""]

    parts.extend(body)
    parts.append(f"---\n*Batches watched: {', '.join(batches)}*")
    return "\n".join(parts)


def _delta(c: dict) -> int:
    try:
        return int(c["new_value"]) - int(c["old_value"])
    except (TypeError, ValueError):
        return 0


def _loc(j: dict) -> str:
    loc = j.get("location")
    if isinstance(loc, list):
        loc = ", ".join(str(x) for x in loc[:2])
    return loc or "location n/a"


def _pay(j: dict) -> str:
    lo, hi = j.get("min_salary"), j.get("max_salary")
    if lo and hi:
        return f" · ${lo//1000}k–${hi//1000}k"
    return ""


def _fmt_launch_date(ts) -> str:
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%d %b %Y")
    except (TypeError, ValueError, OSError):
        return "recently"
