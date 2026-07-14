# Royal Road Longitudinal Collector v0.3

A coherent hourly research panel for Royal Road. It captures the six required Rising Stars lists and comparison cohorts outside Rising Stars, then follows fiction-level public metrics and chapter activity over time.

## Sources collected

### Rising Stars — expected top 50

- Main
- Fantasy
- Action
- Adventure
- Drama
- Psychological

### Non-RS comparison panel

- Newest Fictions
- Latest Updates
- Popular This Week
- Ongoing Fictions

The non-RS sources are essential for near-misses, slow-burn growth, strong runs without RS, and aggressive launches that fail.

## What is stored

- shared UTC run timestamp;
- fiction ID, title, author and URL;
- ordered source/list membership;
- followers, views, average views, favorites, pages, chapters, ratings and other public counters when available;
- exact word count when public, otherwise a separately labelled page-based estimate;
- first and latest chapter dates when parseable;
- genres, tags, status, cover, blurb hash, schedule text and public marketing links;
- chapter-release events;
- entries, exits and rank movement;
- 1h, 6h, 12h, 24h, 3d and 7d deltas;
- dynamic rank-50 cutoffs;
- immutable compressed JSON snapshots and optional compressed HTML.

## Local start

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
copy .env.example .env   # Windows
# cp .env.example .env  # macOS/Linux
rrlab doctor
rrlab init-db
rrlab collect
rrlab export
```

Set a descriptive `RR_USER_AGENT` with a contact address. The collector is rate-limited and uses only public pages. It does not use private mobile endpoints, logins, CAPTCHA bypasses or invented metrics.

## Commands

```bash
rrlab collect                 # full panel plus selected detail enrichment
rrlab collect --no-details    # listing pages only
rrlab latest rs_main
rrlab entrants rs_fantasy
rrlab history 175996
rrlab diagnostics-seed
rrlab export
```

## GitHub Actions

The included workflow runs at minute 7 of every hour, performs a deterministic preflight, collects the panel, commits canonical data and reports, and uploads the latest export plus diagnostics as a 90-day artifact.

Recommended repository secret:

- `RR_USER_AGENT` (a safe descriptive fallback is used when omitted)

Repository workflow permission must be **Read and write**.

## ChatGPT Work / MCP

```bash
pip install -e ".[mcp]"
python -m rrlab.mcp_server
```

Tools include collection, latest-source reading, entrant detection, fiction history and ZIP export. A permanently reachable MCP endpoint still requires hosting; the collector itself can run on GitHub Actions or any cron-capable server.

## Current limits

- Royal Road does not expose every desired variable publicly.
- Exact word count may be absent; page-based estimates are never mixed with exact counts.
- Public promotion detection still needs evidence collection and manual review.
- Git commits are suitable for the pilot. A larger multi-month panel should eventually move SQLite/raw snapshots to durable object storage or PostgreSQL.
