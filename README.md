# Royal Road Longitudinal Collector v0.4

A longitudinal Royal Road research panel with three complementary layers:

1. an hourly, verified moving frontier that captures every fiction appearing ahead of the previous Newest Fictions anchor;
2. immediate detail snapshots and longitudinal public metrics for newly launched fiction;
3. a slow, bounded historical catalog backfill that walks the public Newest Fictions pagination with overlap and repeated verification passes.

The system also captures the six required Rising Stars lists, comparison cohorts outside Rising Stars, visible chapter activity, and an automatic launch-intelligence product that turns collected rows into comparable cohorts, medians, leaders, growth rates and data-quality exceptions.

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

## Launch intelligence

A fiction-ID list is treated as discovery evidence, not as the final analytical output. Every successful panel run produces:

- `reports/launch_analysis_latest.json` — structured current-batch and rolling-cohort analysis;
- `reports/launch_analysis_latest.md` — human-readable tables, medians, leaders and exceptions;
- `reports/launch_analysis_latest.csv` — flat recent-launch cohort for external analysis;
- immutable run-specific versions of all three files.

The **current batch** is the exact set of fiction IDs found before the verified frontier anchor in that run. The default **rolling cohort** contains all prospectively observed Newest fiction discovered during the previous 168 hours.

Each row includes title, author, URL, discovery provenance, age, data availability, followers, views, chapters, ratings, age-normalized rates, follower conversion, per-chapter efficiency, longitudinal growth and current/first Rising Stars context.

A transparent within-cohort launch index combines followers/day, views/day, followers per 1,000 views, followers/chapter, views/chapter and follower growth/day. Rising Stars status is deliberately excluded from the score because it is an outcome. See [`LAUNCH_ANALYSIS.md`](LAUNCH_ANALYSIS.md) for the full methodology.

Removed or unavailable fiction is classified explicitly after HTTP 404/410 and retried weekly. Transient failures use exponential retry delays. These states appear in the report instead of silently lowering coverage or poisoning every subsequent run.

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
- frontier completeness reports, launch analyses, availability/retry states, backfill cursor state and repeated-pass status;
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
rrlab analyze-launches
rrlab catalog-status
rrlab backfill-catalog
rrlab export
```

Set a descriptive `RR_USER_AGENT` with a contact address. The collector is rate-limited and uses only public pages. It does not use logins, private mobile endpoints, CAPTCHA bypasses, rotating proxies or invented metrics. It does not download fiction chapter text.

## Commands

```bash
rrlab collect                              # panel, frontier, details and analysis
rrlab collect --no-details                 # listing pages only
rrlab analyze-launches                     # latest run, rolling 168-hour cohort
rrlab analyze-launches --lookback-hours 72
rrlab analyze-launches --run-id 12
rrlab backfill-catalog                     # one bounded historical catalog chunk
rrlab catalog-status                       # frontier and backfill coverage state
rrlab validate-latest                      # latest Rising Stars panel
rrlab latest rs_main
rrlab latest catalog_backfill
rrlab entrants rs_fantasy
rrlab history 175996
rrlab diagnostics-seed
rrlab export
```

## GitHub Actions

- **Royal Road hourly collection** receives redundant schedule opportunities at minutes 13, 33 and 53.
- A persisted-data cadence gate performs a collection only when the latest complete six-list panel is at least 55 minutes old; manual dispatch always forces a run.
- Each collection validates the current launch batch, writes JSON/Markdown/CSV analysis, and puts the main medians and leaders in the GitHub job summary.
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
- The launch index is descriptive and cohort-relative; it is not a causal estimate or a guarantee of future performance.
- Very young launches use a six-hour age floor to reduce unstable first-hour rates.
- Royal Road does not expose every desired variable publicly.
- Exact word count may be absent; page-based estimates are never mixed with exact counts.
- Public promotion detection still needs evidence collection and manual review.
- Git commits are suitable for the pilot. A larger multi-month panel should eventually move SQLite/raw snapshots to durable object storage or PostgreSQL.
