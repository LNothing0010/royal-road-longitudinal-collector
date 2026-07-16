# Latest Updates page-visit experiment

## Research question

Does a short residence window on Royal Road's official Latest Updates page during a high-traffic hour create the same, more or less potential exposure than a longer residence window during a low-traffic hour?

Example hypothesis:

- 18:00–19:00 UTC: a fiction remains on page 1 for 5 minutes;
- 12:00–13:00 UTC: a fiction remains on page 1 for 20 minutes;
- the two windows may produce similar potential exposure if the page receives roughly four times as many visits per minute in the shorter window.

## Definitions

- **Page visit:** an estimated visit to `royalroad.com/fictions/latest-updates`.
- **Potential impression:** one page visit occurring while a fiction is present on page 1. This is an opportunity to see the fiction, not proof that the visitor scrolled to its rank.
- **Fiction view:** a Royal Road view recorded on the fiction. Views are an outcome used to evaluate Latest Updates performance, not an input used to estimate page traffic.

## What the repository observes directly

Every five minutes the exposure sampler records:

- membership and rank on Latest Updates page 1;
- membership and rank in Homepage Latest Updates;
- membership and rank on Newest Fictions page 1;
- homepage Rising Stars membership and rank;
- chapter timestamp when Royal Road exposes it;
- entry and exit bounds between adjacent samples.

Residence remains interval-censored. A fiction present at 12:00 and absent at 12:05 disappeared somewhere inside that interval; the repository must not claim false second-level precision.

## External traffic sources

Traffic estimates are imported from services such as Semrush or Similarweb.

Useful Semrush datasets include:

- **Top Pages:** monthly estimated traffic for the exact Latest Updates page;
- **Daily Traffic:** day-by-day traffic for a domain, subdomain, or subfolder;
- domain/subfolder baselines used to contextualize the page share.

A monthly or daily page total does not identify hour-of-day demand. The 12:00–13:00 versus 18:00–19:00 experiment requires one of:

1. hourly estimates for the exact Latest Updates page; or
2. an exact-page daily/monthly baseline plus an hourly Royal Road domain series from the same provider and period.

Estimates from different providers are never silently averaged into a single point. The report stores each provider estimate and reports minimum, median, maximum, and provider count.

## Input schema

Store observations at `data/external_page_traffic.csv`:

```csv
provider,target_url,scope,granularity,period_start_utc,period_end_utc,visits
semrush,royalroad.com/fictions/latest-updates,page,month,2026-06-01T00:00:00Z,2026-07-01T00:00:00Z,1200000
semrush,royalroad.com,domain,month,2026-06-01T00:00:00Z,2026-07-01T00:00:00Z,52080000
provider-x,royalroad.com,domain,hour,2026-07-20T12:00:00Z,2026-07-20T13:00:00Z,85000
```

Allowed scopes: `page`, `subfolder`, `domain`.
Allowed granularities: `hour`, `day`, `month`.

## Calculation

### Direct hourly page traffic

When the provider supplies hourly visits to the exact page:

```text
potential page visits = hourly page visits × residence fraction of the hour
```

### Page baseline plus hourly domain traffic

When the provider supplies a page baseline and a matching domain baseline:

```text
page share = page baseline visits / domain baseline visits
estimated page visits in window = hourly domain visits × page share × residence fraction
```

The page baseline and domain hourly series must come from the same provider. Cross-provider splicing is not allowed.

## Rank and viewability

A visit to page 1 is not proof that every card was visible. The report therefore records:

- total page-visit opportunity;
- whether the fiction was in the top 10;
- whether the fiction was in the top 5.

No unsupported scroll-depth probability is invented. A future measured scroll curve may be added as a separate calibration layer.

## Views as outcome

Once page visits are independently estimated, fiction views may be evaluated as:

```text
views per 1,000 estimated page visits = fiction view delta / estimated page visits × 1,000
```

This statistic is descriptive. Views may also arise from followers, direct links, ads, shout-outs, search, Rising Stars, or other surfaces. Stronger causal analysis should use repeated chapter releases from the same fiction with fiction fixed effects, release-age controls, weekday/hour controls, and intervention flags.

## Data-quality states

- `ready`: at least one episode overlaps usable hourly page traffic or a valid same-provider page-share calibration.
- `uncalibrated`: residence is known but page traffic cannot be allocated to the episode's hour.
- provider spread: the report exposes disagreement between traffic-estimation services.

## Commands

```bash
rrlab collect-exposure
rrlab analyze-exposure --lookback-hours 168
rrlab estimate-page-visit-opportunity \
  --exposure-json reports/exposure_analysis_latest.json \
  --traffic-csv data/external_page_traffic.csv
```
