# Methodology

The collector separates:

1. **Raw public observations** — listing pages, fiction pages and visible chapter events.
2. **Derived variables** — deltas, list transitions, overlap and rank-50 cutoffs.
3. **Manual interventions** — ads, shoutouts and external promotion only when a source is recorded.
4. **Model outputs** — versioned separately in `model_registry`.

No causal claim should be inferred from one correlation. Candidate effects require temporal ordering, matched comparisons, repeated events and sensitivity to confounders such as genre, author history, prior audience, fiction age, release cadence, list overlap and promotion.

## Population design

The analysis uses distinct populations rather than mixing every observed fiction:

- **Prospective complete-new-fiction cohort:** fiction IDs appearing before the first stable anchor from the prior Newest Fictions run. This population supports launch base rates, entry hazards, failure rates and blind forecasting.
- **Historical catalog registry:** fiction discovered during bounded backfill. It supports author history, genre prevalence, current-state comparison and candidate matching, but it cannot supply unavailable launch-time trajectories.
- **Rising Stars panel:** exact top 50 for Main, Fantasy, Action, Adventure, Drama and Psychological.
- **Strong non-RS comparison panel:** Latest Updates, Popular This Week and Ongoing Fictions.

Historical backfill discovery time is not publication time. Backfilled fiction must never be relabelled as a new launch merely because it was previously absent from the local database.

## Frontier completeness

An hourly Newest run is prospectively complete only when it reaches at least one fiction ID preserved from the previous successful frontier within the configured page ceiling. One additional page is fetched for pagination-overlap diagnostics.

Only rows positioned before the first prior anchor are candidates for the new-fiction cohort. Unknown rows after the boundary are excluded. When the anchor is not reached, the old anchor is retained and the run is reported as incomplete rather than silently advancing the frontier.

## Historical backfill completeness

The daily catalog process advances a persistent page cursor with a three-page overlap. Overlap reduces skip risk from front-page insertions while stable fiction IDs provide deduplication. Reaching the public last page completes one pass; the next pass restarts from page 2 to measure residual omissions and pagination drift.

A single completed pass is a catalog census of publicly visible listing rows at scan time, not a reconstruction of historical rankings or metrics.
