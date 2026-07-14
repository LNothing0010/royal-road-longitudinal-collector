# Catalog census operating plan

## Prospective completeness

The hourly Newest Fictions frontier is the canonical source for all new fiction from the first validated baseline onward. A run is complete only when it reaches a prior stable fiction-ID anchor. The prior anchor is retained when the page ceiling is exhausted.

## Historical completeness

The daily backfill advances 75 new pages with a three-page overlap and stable-ID deduplication. Reaching the public last page completes one pass. Subsequent passes measure residual omissions caused by pagination drift, deletion, moderation and newly inserted fiction.

## Traffic controls

- public HTML pages only;
- no authenticated sessions;
- no private/mobile endpoints;
- no CAPTCHA or access-control bypass;
- no proxy rotation;
- no fiction chapter-text download;
- serialized requests with a minimum delay;
- bounded pages per workflow run;
- immediate diagnostic failure on missing frontier continuity.

## Analytical separation

Historical registry observations must never be used as launch-time observations. Only prospective frontier entrants can populate the complete new-fiction denominator used for base rates, blind forecasting and time-to-Rising-Stars models.
