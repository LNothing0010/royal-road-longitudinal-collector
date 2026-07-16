# Launch Intelligence

The collector treats discovery, data quality and analysis as separate layers. A list of fiction IDs is not considered a useful analytical result.

## Cohorts

### Current batch

The exact fiction IDs reported before the verified Newest frontier anchor in one run. This answers: **what was newly published since the previous complete collection?**

### Rolling cohort

All prospectively observed Newest fiction discovered during the preceding 168 hours by default. This answers: **which recent launches are gaining traction relative to comparable-age launches?**

A fiction can be first encountered on another public list earlier in the same run. Membership in the verified Newest frontier, rather than `first_seen_source` alone, determines launch-cohort eligibility.

## Required output

Every panel run writes:

- `reports/launch_analysis_latest.json` — complete structured report;
- `reports/launch_analysis_latest.md` — human-readable current batch, medians, leaders, leaderboard and exceptions;
- `reports/launch_analysis_latest.csv` — flat rolling-cohort table;
- immutable run-specific versions of all three files.

The collector's normal JSON output also embeds a concise launch-analysis summary, so a caller does not need to inspect SQLite to understand the result.

## Per-fiction fields

The report includes:

- title, author, URL and fiction ID;
- discovery source and Newest rank at discovery;
- first-seen, publication and latest metric timestamps;
- data availability and retry state;
- followers, total views, chapters, pages, ratings and other public counters;
- followers/day and views/day using a six-hour age floor;
- followers per 1,000 views;
- followers/chapter and views/chapter;
- growth and growth/day across available detail observations;
- current Rising Stars presence, best rank, list count and time to first RS entry;
- a transparent within-cohort launch index and its component percentiles.

## Launch index

The index is a weighted average of within-cohort percentiles:

- followers/day: 25%;
- views/day: 20%;
- followers per 1,000 views: 20%;
- followers/chapter: 15%;
- views/chapter: 10%;
- follower growth/day: 10% when longitudinal data exist.

Weights are renormalized across available components, and at least four components are required. Rising Stars status is deliberately excluded from the index because it is an outcome, not an input.

The index is descriptive, not a causal model or a prediction of eventual success.

## Data-quality states

- `complete`: followers, total views and chapter count are present;
- `partial`: a detail page was parsed but one or more core metrics are absent;
- `missing`: no usable detail observation and no confirmed terminal HTTP state;
- `unavailable`: the public detail URL returned 404 or 410.

Unavailable fiction is reported explicitly. It counts as resolved coverage but not core-metric coverage. A 404/410 is retried weekly rather than every hour, preventing removed works from poisoning every subsequent run.

Transient failures use exponential retry delays and remain unresolved until a usable detail snapshot is obtained.

## Commands

```bash
rrlab collect
rrlab analyze-launches
rrlab analyze-launches --lookback-hours 72
rrlab analyze-launches --run-id 12 --lookback-hours 168
```
