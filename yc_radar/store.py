"""SQLite persistence + day-over-day diffing.

The scraper is stateless; this module is what turns "today's snapshot" into
"what changed since yesterday", which is the actual product.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from typing import Any, Iterable


def _scalar(v: Any) -> Any:
    """Algolia hands back lists for multi-value fields (locations, industries).
    SQLite won't bind those, so flatten before it reaches the driver."""
    if isinstance(v, (list, tuple)):
        return ", ".join(str(x) for x in v) or None
    if isinstance(v, dict):
        return json.dumps(v)
    return v


def _vals(row: dict, *extra: Any) -> tuple:
    return (*(_scalar(v) for v in row.values()), *extra)

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    id TEXT PRIMARY KEY, name TEXT, slug TEXT, batch TEXT, status TEXT,
    team_size INTEGER, website TEXT, one_liner TEXT, industry TEXT,
    location TEXT, stage TEXT, is_hiring INTEGER, top_company INTEGER,
    launched_at INTEGER, yc_url TEXT, first_seen TEXT, last_seen TEXT,
    last_funding_check TEXT
);
CREATE TABLE IF NOT EXISTS changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, company_id TEXT, company_name TEXT,
    field TEXT, old_value TEXT, new_value TEXT, observed_on TEXT
);
CREATE TABLE IF NOT EXISTS founders (
    company_id TEXT, name TEXT, title TEXT, linkedin TEXT, twitter TEXT,
    first_seen TEXT, PRIMARY KEY (company_id, name, linkedin)
);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY, company_id TEXT, company_name TEXT, title TEXT,
    role_type TEXT, location TEXT, min_salary INTEGER, max_salary INTEGER,
    url TEXT, first_seen TEXT, last_seen TEXT, closed_on TEXT
);
CREATE TABLE IF NOT EXISTS funding_mentions (
    link TEXT PRIMARY KEY, company_id TEXT, company_name TEXT, title TEXT,
    source TEXT, published TEXT, first_seen TEXT
);
CREATE INDEX IF NOT EXISTS idx_changes_date ON changes(observed_on);
CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(company_id);
CREATE INDEX IF NOT EXISTS idx_funding_company ON funding_mentions(company_id);
"""

# Fields worth waking up for. Everything else we store but don't report.
TRACKED_FIELDS = (
    "team_size", "status", "one_liner", "website",
    "stage", "is_hiring", "top_company", "launched_at",
)


class Store:
    def __init__(self, path: str = "yc_radar.db"):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.today = date.today().isoformat()

    def close(self) -> None:
        self.db.commit()
        self.db.close()

    # ---- companies -------------------------------------------------------

    def sync_companies(self, rows: Iterable[dict]) -> dict[str, list]:
        new_companies, changes = [], []
        for row in rows:
            prev = self.db.execute(
                "SELECT * FROM companies WHERE id = ?", (row["id"],)
            ).fetchone()

            if prev is None:
                cols = ", ".join(row)
                marks = ", ".join("?" * len(row))
                self.db.execute(
                    f"INSERT INTO companies ({cols}, first_seen, last_seen) "
                    f"VALUES ({marks}, ?, ?)",
                    _vals(row, self.today, self.today),
                )
                new_companies.append(row)
                continue

            for f in TRACKED_FIELDS:
                old, new = prev[f], row.get(f)
                if new is not None and str(old) != str(new):
                    change = {
                        "company_id": row["id"], "company_name": row["name"],
                        "field": f, "old_value": old, "new_value": new,
                    }
                    self.db.execute(
                        "INSERT INTO changes (company_id, company_name, field, "
                        "old_value, new_value, observed_on) VALUES (?,?,?,?,?,?)",
                        (*(_scalar(v) for v in change.values()), self.today),
                    )
                    changes.append(change)

            sets = ", ".join(f"{k} = ?" for k in row if k != "id")
            self.db.execute(
                f"UPDATE companies SET {sets}, last_seen = ? WHERE id = ?",
                (*[_scalar(v) for k, v in row.items() if k != "id"], self.today, row["id"]),
            )

        self.db.commit()
        return {"new": new_companies, "changed": changes}

    # ---- founders --------------------------------------------------------

    def sync_founders(self, company_id: str, founders: Iterable[dict]) -> list[dict]:
        added = []
        for f in founders:
            cur = self.db.execute(
                "INSERT OR IGNORE INTO founders (company_id, name, title, linkedin, "
                "twitter, first_seen) VALUES (?,?,?,?,?,?)",
                (company_id, _scalar(f.get("name")), _scalar(f.get("title")),
                 _scalar(f.get("linkedin")), _scalar(f.get("twitter")), self.today),
            )
            if cur.rowcount:
                added.append(f)
        self.db.commit()
        return added

    def known_company_ids(self) -> set[str]:
        return {r["id"] for r in self.db.execute("SELECT id FROM companies")}

    # ---- jobs ------------------------------------------------------------

    def sync_jobs(self, rows: Iterable[dict]) -> dict[str, list]:
        seen_ids, new_jobs = set(), []
        for row in rows:
            seen_ids.add(row["id"])
            prev = self.db.execute("SELECT id FROM jobs WHERE id = ?", (row["id"],)).fetchone()
            if prev is None:
                cols = ", ".join(row)
                marks = ", ".join("?" * len(row))
                self.db.execute(
                    f"INSERT INTO jobs ({cols}, first_seen, last_seen) VALUES ({marks}, ?, ?)",
                    _vals(row, self.today, self.today),
                )
                new_jobs.append(row)
            else:
                self.db.execute(
                    "UPDATE jobs SET last_seen = ?, closed_on = NULL WHERE id = ?",
                    (self.today, row["id"]),
                )

        # A posting that vanished from the board is a signal too -- either the
        # role got filled or the plan changed. Both worth knowing.
        closed = []
        if seen_ids:
            marks = ",".join("?" * len(seen_ids))
            closed = [
                dict(r) for r in self.db.execute(
                    f"SELECT * FROM jobs WHERE closed_on IS NULL AND id NOT IN ({marks})",
                    tuple(seen_ids),
                )
            ]
            self.db.execute(
                f"UPDATE jobs SET closed_on = ? WHERE closed_on IS NULL AND id NOT IN ({marks})",
                (self.today, *seen_ids),
            )

        self.db.commit()
        return {"new": new_jobs, "closed": closed}

    # ---- funding news ------------------------------------------------------

    def companies_due_for_funding_check(self, limit: int) -> list[dict]:
        """Rotate through companies oldest-checked-first so a capped run
        still reaches full coverage over a few days instead of always
        re-checking the same alphabetical prefix."""
        rows = self.db.execute(
            "SELECT id, name FROM companies ORDER BY "
            "last_funding_check IS NOT NULL, last_funding_check ASC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in rows]

    def mark_funding_checked(self, company_id: str) -> None:
        self.db.execute(
            "UPDATE companies SET last_funding_check = ? WHERE id = ?",
            (self.today, company_id),
        )
        self.db.commit()

    def sync_funding_mentions(self, company_id: str, company_name: str, mentions: Iterable[dict]) -> list[dict]:
        added = []
        for m in mentions:
            row = {
                "link": m["link"], "company_id": company_id, "company_name": company_name,
                "title": m.get("title"), "source": m.get("source"), "published": m.get("published"),
            }
            cur = self.db.execute(
                "INSERT OR IGNORE INTO funding_mentions (link, company_id, company_name, "
                "title, source, published, first_seen) VALUES (?,?,?,?,?,?,?)",
                (*(_scalar(v) for v in row.values()), self.today),
            )
            if cur.rowcount:
                added.append(row)
        self.db.commit()
        return added
