# Royal Road Longitudinal Collector v0.4

A longitudinal Royal Road research panel with two complementary coverage layers:

1. an hourly, verified moving frontier that captures every fiction appearing ahead of the previous Newest Fictions anchor;
2. a slow, bounded historical catalog backfill that walks the public Newest Fictions pagination with overlap and repeated verification passes.

The system also captures the six required Rising Stars lists, comparison cohorts outside Rising Stars, fiction-level public metrics and visible chapter activity.

## Sources collected

### Rising Stars — expected top 50

- Main
- Fantasy
- Action
- Adventure
- Drama
- Psychological

### Non-RS comparison panel

- complete prospective Newest Fictions frontier;
- Latest Updates;
- Popular This Week;
- Ongoing Fictions;
- historical `catalog_backfill` registry.

The non-RS population is required to measure base rates, near-misses, slow-burn growth, strong runs without RS and aggressive launches that fail.

## Complete-new-fiction frontier

Every hourly run starts at Newest Fictions page 1 and continues until it encounters a stable fiction-ID anchor from the previous successful run. It then reads one extra overlap page to protect against pagination movement.

Only fiction IDs positioned before the first prior anchor are labelled as newly launched. Unknown rows after the boundary are excluded from the prospective cohort so historical overlap cannot create false new launches.

The frontier has a hard page ceiling. If the previous anchor is not reached, coverage is marked incomplete, the workflow fails visibly, the old anchor is retained, and all diagnostics are still persisted. Missing rows are never invented.

## Historical catalog backfill

A separate daily workflow reads 75 new catalog pages per run with a three-page overlap. Its cursor is stored in `data/catalog_state.json`. When the public final page is reached, the pass is marked complete and another verification pass starts from page 2.

Historical backfill supplies a broad fiction registry and author/genre context. It does not reconstruct unavailable historical follower trajectories, old Rising Stars positions, ads or shoutouts.

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
- frontier completeness reports, backfill cursor state and repeated-pass status;
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
rrlab catalog-status
rrlab backfill-catalog
rrlab export
```

Set a descriptive `RR_USER_AGENT` with a contact address. The collector is rate-limited and uses only public pages. It does not use logins, private mobile endpoints, CAPTCHA bypasses, rotating proxies or invented metrics. It does not download fiction chapter text.

## Commands

```bash
rrlab collect                 # hourly panel plus verified Newest frontier
rrlab collect --no-details    # listing pages only
rrlab backfill-catalog        # one bounded historical catalog chunk
rrlab catalog-status          # frontier and backfill coverage state
rrlab validate-latest         # latest Rising Stars panel, not a catalog-only run
rrlab latest rs_main
rrlab latest catalog_backfill
rrlab entrants rs_fantasy
rrlab history 175996
rrlab diagnostics-seed
rrlab export
```

## GitHub Actions

- **Royal Road hourly collection** runs at minute 7 of every hour.
- **Royal Road catalog backfill** runs once daily at 03:37 UTC.
- Both workflows share one write-concurrency group, so they cannot modify the SQLite database simultaneously.
- Canonical data and diagnostics are committed even when a quality gate fails.
- ZIP exports and reports are retained as workflow artifacts for 90 days.

Recommended repository secret:

- `RR_USER_AGENT` — a safe descriptive fallback is used when omitted.

Repository workflow permission must be **Read and write**.

## ChatGPT Work / MCP

```bash
pip install -e ".[mcp]"
python -m rrlab.mcp_server
```

Tools include collection, catalog status, latest-source reading, entrant detection, fiction history and ZIP export. A permanently reachable MCP endpoint still requires hosting; the collector itself can run on GitHub Actions or any cron-capable server.

## Current limits

- A prospective census is complete only from the first validated frontier baseline onward.
- Historical catalog snapshots cannot recover metrics that were never observed at launch time.
- Royal Road does not expose every desired variable publicly.
- Exact word count may be absent; page-based estimates are never mixed with exact counts.
- Public promotion detection still needs evidence collection and manual review.
- Git commits are suitable for the pilot. A larger multi-month panel should eventually move SQLite/raw snapshots to durable object storage or PostgreSQL.
