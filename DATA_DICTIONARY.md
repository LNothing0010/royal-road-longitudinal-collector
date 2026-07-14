# Data dictionary

## Observation grain

- `run`: one coherent UTC collection cycle. Panel runs and catalog-only backfill runs share the same append-only run table.
- `source_snapshot`: one coherent source result within a run. `newest` can aggregate several consecutively read pages; `catalog_backfill` aggregates one bounded daily page range.
- `listing_membership`: one fiction's ordered presence in one source result and run.
- `metric_observation`: public counters observed for one fiction from one source in one run.
- `metadata_observation`: mutable public presentation fields, taxonomy and marketing links.
- `release_event`: deduplicated public chapter event.
- `metric_delta`: difference between canonical observations near a defined time horizon.
- `list_transition`: entry, exit, rise, fall or flat movement between consecutive snapshots.

## Catalog state and reports

- `data/catalog_state.json`: atomic cursor and anchor state for the prospective frontier and historical backfill.
- `frontier_anchor_ids`: stable fiction IDs from the latest successfully verified Newest frontier.
- `frontier_latest.json`: latest proof of whether the previous frontier was reached.
- `catalog_backfill_latest.json`: latest historical page-range scan and cursor advance.
- `catalog_complete_once`: true only after at least one bounded pass reaches the public final page.
- `backfill_pass`: current repeated verification pass number.

## Important fields

- `source_name`: stable collector source, such as `rs_main`, `newest` or `catalog_backfill`.
- `rank`: source order. It is an RS rank only for `rs_*` sources. For paginated Newest/catalog scans, it is the observed absolute page position calculated with 20 rows per page.
- `complete`: for RS, exact top-50 completeness. For `newest`, proof that the prior anchor was reached within the configured page ceiling. For `catalog_backfill`, whether that chunk reached the public final page.
- `new_fiction_ids`: fiction IDs positioned before the first prior Newest anchor. Rows after the anchor are never labelled as new launches.
- `overlap_unknown_fictions_excluded`: previously unseen IDs found only after the frontier boundary and deliberately excluded from the prospective launch cohort.
- `word_count`: exact public word count only when explicitly observable.
- `word_count_estimate`: derived estimate; currently `page_count × 275` when no exact count exists.
- `word_count_source`: provenance of the word count or estimate.
- `date_precision`: `unix`, `absolute`, `relative` or `unknown`.

## Missing values

Unavailable public fields are stored as `NULL`. They must never be replaced with invented values. Estimates have separate columns and provenance.

## Cohort rule

The historical catalog registry and the prospective launch cohort are separate analytical populations. A fiction first discovered in historical backfill must not be treated as a newly observed launch merely because it was absent from the local database before that backfill run.
