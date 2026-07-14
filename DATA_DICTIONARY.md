# Data dictionary

## Observation grain

- `run`: one coherent UTC collection cycle.
- `source_snapshot`: one public Royal Road listing page within a run.
- `listing_membership`: one fiction's ordered presence on one page in one run.
- `metric_observation`: public counters observed for one fiction from one source in one run.
- `metadata_observation`: mutable public presentation fields, taxonomy and marketing links.
- `release_event`: deduplicated public chapter event.
- `metric_delta`: difference between canonical observations near a defined time horizon.
- `list_transition`: entry, exit, rise, fall or flat movement between consecutive snapshots.

## Important fields

- `source_name`: stable collector source, such as `rs_main` or `newest`.
- `rank`: page order. It is an RS rank only for `rs_*` sources.
- `word_count`: exact public word count only when explicitly observable.
- `word_count_estimate`: derived estimate; currently `page_count × 275` when no exact count exists.
- `word_count_source`: provenance of the word count or estimate.
- `date_precision`: `unix`, `absolute`, `relative` or `unknown`.
- `complete`: only asserted for sources with an explicit expected count, especially RS top 50.

## Missing values

Unavailable public fields are stored as `NULL`. They must never be replaced with invented values. Estimates have separate columns and provenance.
