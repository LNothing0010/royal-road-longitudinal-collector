# Methodology

The collector separates:

1. **Raw public observations** — listing pages, fiction pages and visible chapter events.
2. **Derived variables** — deltas, list transitions, overlap and rank-50 cutoffs.
3. **Manual interventions** — ads, shoutouts and external promotion only when a source is recorded.
4. **Model outputs** — versioned separately in `model_registry`.

No causal claim should be inferred from one correlation. Candidate effects require temporal ordering, matched comparisons, repeated events and sensitivity to confounders such as genre, author history, prior audience, fiction age, release cadence, list overlap and promotion.

The non-RS panel is intentionally included to reduce survivor bias. `newest` and `latest_updates` capture launches and release behavior; `weekly_popular` and `active_popular` provide strong-run comparison cohorts outside Rising Stars.
