# yc-radar

Daily hiring and growth signal for recent YC batches. No login, no API key, no
paid data provider. Reads YC's own public Algolia index — the same one your
browser hits when you visit the company directory.

## What you get each morning

- **New companies** in the batches you watch
- **Team size changes** — `4 → 18` is a hiring event you'd otherwise learn about
  weeks later from a LinkedIn post
- **Status changes** — Active → Acquired / Inactive
- **Product launches** — YC's own Launch YC date per company, so a first
  public launch (or a relaunch) shows up the day it happens
- **Stage, hiring-flag, and Top Company changes** — Early → Growth, a company
  toggling "actively hiring," or picking up the Top Company badge
- **New and closed roles** from Work at a Startup, grouped by company, with a
  flag on anyone opening 3+ roles at once
- **Founder handles** (LinkedIn / X) indexed once per company, for whatever you
  want to do downstream
- **Funding-news leads** (opt-out via `--skip-funding-news`) — see the caveat
  below before trusting these

## Run it

```bash
pip install requests
python -m yc_radar                                    # default: last ~2 years
python -m yc_radar --batches "Winter 2026,Spring 2026"
python -m yc_radar --skip-founders                    # fast path, no profile fetches
python -m yc_radar --skip-funding-news                # skip the Google News sweep
python -m yc_radar --max-funding-checks 50             # cap how many companies get checked per run
```

First run writes `yc_radar.db` and `digests/YYYY-MM-DD.md`. It'll report
everything as new — that's the baseline. The second run is where it gets useful.

## Run it daily for free

Push to GitHub and the included workflow takes over. Public repos get unlimited
Actions minutes; private repos get 2,000 free Linux minutes a month, which is
far more than this needs. A free account also defaults to a $0 spending limit,
so jobs stop rather than bill you.

Set a `SLACK_WEBHOOK` repo secret if you want it in a channel. It stays quiet on
days when nothing changed.

## Before you trust the first run

Verified live as of Aug 2026. Companies come from `YCCompany_production` via
Algolia, credentials scraped fresh off ycombinator.com/companies each run
(`discover_creds()` — key rotation won't break you, index *names* changing
would). If companies come back empty, open the YC directory in a browser with
devtools on the Network tab, find the request to `*.algolia.net`, and compare
the index name and `filters` field names against `COMPANY_INDEX` and
`_normalise_company` in `algolia.py`.

**Jobs work differently.** Work at a Startup retired its public Algolia job
index — `WaaSPublicJob_production` now 404s. There's no public API left for
it, so `jobs()` scrapes the logged-out job board's per-role-category pages
(`workatastartup.com/jobs/l/<role>`) instead. That surface only shows a
curated preview, not the full board — full listings require a login this
project intentionally doesn't use. Expect job coverage to be thin and
skewed toward whatever WaaS chooses to feature, not a complete sweep.

**Funding news is not a YC data source, and it's genuinely unverified.** YC's
own directory has no funding fields at all, so `news.py` searches Google News'
free public RSS for each company name and keeps only headlines with a dollar
figure next to a funding word (`raises $5M`, `$80M ... funding round`) — this
rules out idioms like "raises concerns" and hyphenated false matches like
"enterprise-grade", but it can't tell your YC company apart from an unrelated
company that happens to share its name. Short, common-word company names
(single dictionary words, generic brand-y names) are the most likely to
surface someone else's funding news. Every mention in the digest is a lead to
verify by opening the link, never a confirmed fact. Runs by default, checking
`--max-funding-checks` (default 200) companies per run, oldest-checked-first,
so a large batch list still gets full coverage over a few days rather than
favoring alphabetically-early names. Skip it entirely with
`--skip-funding-news` if the noise isn't worth it for your batches.

## Design notes

- **Batch partitioning is not optional.** Algolia caps any single query at 1,000
  hits. Sweeping by batch keeps every partition under the ceiling.
- **Founder enrichment only runs on companies you haven't seen before.** Rosters
  are near-static; re-crawling 2,000 profiles daily buys nothing and is rude.
- **Closed jobs are tracked, not deleted.** A role vanishing means it got filled
  or got cancelled. Both are signal.
- **The `.db` file is the state.** The Actions workflow commits it back after
  every run. Skip that and every day looks like day one.

## Extending it

`store.py` is source-agnostic — it diffs dicts. To add HN Launch threads, company
blog RSS, or careers-page diffing, write a collector that returns the same shape
and hand it to a new `sync_*` method. Nothing else needs to change.
