# Longitudinal tracking guarantees

## Invariant

Raw observations are append-only.

A fiction's metrics are keyed by the collection run, source and fiction ID. A later request never updates or deletes an earlier metric row. Every successful detail request creates a new `metric_observation` associated with a new `run_id`.

## Tracked cohort

`longitudinal_tracking` contains every fiction that:

- was prospectively observed in the verified `newest` frontier; or
- has ever produced a `fiction_detail` observation.

The `refresh-longitudinal` command requests every active tracked fiction on each invocation. The optional `--max-fictions` argument, or `RR_LONGITUDINAL_MAX_PER_RUN`, can impose an explicit operational cap. The default value `0` means no cap.

Deleted works and currently suppressed 404/410 retries remain tracked. Their skipped retry window is explicit rather than silently converted into a zero or missing metric.

## Fetch history

Every attempted detail request is appended to `detail_fetch_observation`, including:

- run and timestamp;
- success or failure;
- availability classification;
- HTTP status;
- exception type/message;
- raw JSON snapshot path when successful.

`detail_fetch_state` remains the current retry state. `detail_fetch_observation` is the immutable history of how that state was reached.

## Repeated analysis

Each longitudinal run recomputes the tracked cohort from all raw observations available at that run. For every fiction, the report records:

- current and immediately previous detail observation;
- elapsed time between them;
- absolute and percentage changes;
- views, followers, chapters, favorites and rating-count deltas;
- incremental views/day and followers/day;
- current Rising Stars state;
- current launch index and confidence.

A versioned row is appended to `analysis_observation` with primary key:

```text
(run_id, fiction_id, analysis_version)
```

This separates genuine performance changes from methodology changes. Re-running a future analysis version over the same raw data does not overwrite the older version.

## Outputs

Each run writes:

- `reports/longitudinal_analysis_run_<run_id>.json`
- `reports/longitudinal_analysis_run_<run_id>.md`
- `reports/longitudinal_analysis_run_<run_id>.csv`
- `reports/longitudinal_analysis_latest.*`
- `reports/longitudinal_history_latest.csv`

The history CSV contains one analysis row per fiction, run and methodology version.

## Commands

Refresh every tracked fiction and repeat the analysis:

```bash
rrlab refresh-longitudinal
```

Recompute and persist a new analysis row without performing network requests:

```bash
rrlab refresh-longitudinal --analysis-only
```

Apply an explicit temporary cap:

```bash
rrlab refresh-longitudinal --max-fictions 100
```

## Scheduling

The dedicated GitHub Actions workflow runs hourly. It shares the repository data-writer concurrency group with the other collectors so SQLite and report commits are serialized.
