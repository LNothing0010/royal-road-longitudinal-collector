# Latest Updates impression-opportunity experiment

## Research question

Does a short Latest Updates residence window during a high-traffic hour create the same, more or less opportunity-to-see than a longer residence window during a low-traffic hour?

Example hypothesis:

- 18:00–19:00 UTC: a fiction remains visible for 5 minutes;
- 12:00–13:00 UTC: a fiction remains visible for 20 minutes;
- the two windows may nevertheless produce similar potential impressions if the independent traffic factor is roughly four times higher in the shorter window.

The hypothesis is testable only when residence and traffic demand are measured independently.

## What the repository observes directly

Every 15 minutes the exposure sampler records:

- membership and rank in Homepage Latest Updates;
- membership and rank on Latest Updates page 1;
- membership and rank on Newest Fictions page 1;
- homepage Rising Stars membership and rank;
- chapter timestamp when Royal Road exposes it;
- entry and exit bounds between adjacent samples.

This yields interval-censored residence. A fiction present at 12:00 and absent at 12:15 is not reported as having exactly 15 minutes of exposure; its disappearance occurred somewhere inside that interval.

## What the repository cannot observe publicly

Royal Road does not publish:

- visits to the homepage by minute;
- visits to Latest Updates by minute;
- card-level impressions;
- scroll-depth or rank-specific viewability.

Therefore residence time alone is not an impression count.

## Independent traffic probe

Use at least two continuously running Royal Road ad campaigns, preferably one Leaderboard and one Rectangle, across the same seven-day period.

For each campaign record at 15-minute intervals:

```csv
observed_utc,campaign_id,ad_format,cumulative_impressions
2026-07-20T00:00:00Z,leaderboard-week1,leaderboard,1000
2026-07-20T00:15:00Z,leaderboard-week1,leaderboard,1280
2026-07-20T00:00:00Z,rectangle-week1,rectangle,2000
2026-07-20T00:15:00Z,rectangle-week1,rectangle,2550
```

Store the observations at `data/traffic_probe.csv`.

Royal Road defines an ad impression as each time an ad is served. The campaign is used as a traffic sensor, not as evidence that a specific Latest Updates card was seen.

## Calibration

For every campaign:

1. calculate impression increments between adjacent snapshots;
2. divide by elapsed minutes;
3. normalize each campaign by its own median delivery rate;
4. calculate the median normalized factor across campaigns for every UTC hour;
5. reject resets, negative increments and intervals longer than 90 minutes.

Two formats are used to detect campaign-specific pacing. If their normalized hourly curves disagree materially, the profile is not considered reliable.

## Opportunity calculation

For each fiction episode:

```text
relative opportunity = Σ(duration minutes × independent traffic factor)
```

The model reports separate observable rank bands:

- any page-1 slot;
- top 10;
- top 5.

It does not invent a scroll probability. Absolute card impressions remain unavailable unless Royal Road or another first-party source supplies page-specific visit and viewability data.

## Bias guardrail

The impression-opportunity model does not read:

- fiction views;
- followers;
- favorites;
- ratings;
- click-through rate.

Those variables are downstream outcomes. They may later be divided by independently estimated opportunity units to compare conversion, but they can never be used to infer the opportunity itself.

## Decision criteria

A first directional read requires:

- at least seven full days;
- coverage of every UTC hour;
- at least 48 valid 15-minute traffic intervals;
- two ad formats with broadly consistent normalized traffic curves;
- enough completed residence episodes per hour to report medians and interquartile ranges.

A scheduling recommendation should compare:

- median residence by hour;
- median independent traffic factor by hour;
- relative opportunity for 5, 10 and 20 minutes;
- observed distribution, not only the mean;
- weekday and weekend separately once sample size permits.

## Commands

```bash
rrlab collect-exposure
rrlab analyze-exposure --lookback-hours 168
rrlab estimate-impression-opportunity \
  --exposure-json reports/exposure_analysis_latest.json \
  --probe-csv data/traffic_probe.csv
```
