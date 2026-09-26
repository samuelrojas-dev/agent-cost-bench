# ADR 0004 — Real provider adapters: pricing, settings and token counting

- Status: accepted
- Date: 2026-09-25

## Context
Phase 2 adds Claude and Gemini adapters. Several choices change what a measured cost means,
so they are fixed here before any real run.

## Decision
1. **Paid list prices, even on free tiers.** Runs on the Gemini free tier cost nothing,
   but a denial-of-wallet benchmark measures what a paying victim would be billed. Every
   real price entry cites the official pricing page and the date it was read.
2. **No server-side refusal fallbacks.** A fallback re-runs a declined request on another
   model inside the same call, which would mix two models' costs in one measurement.
   Refusals are recorded as the `refusal` stop reason instead.
3. **Provider defaults for thinking.** Adapters send no thinking or effort settings, so each
   model is measured as deployed by default. Reasoning tokens are still billed and reported
   separately (ADR 0002). Changing these settings is a future defense, not a hidden default.
4. **Native replay.** Assistant turns are sent back exactly as the provider returned them
   (Claude thinking blocks, Gemini thought signatures), as both APIs require.
5. **Unpriced usage fails loudly.** Usage that the price table cannot bill (server tools,
   1-hour cache writes, unknown fields, Gemini totals that do not add up) raises instead of
   being dropped. Tests pin each SDK's usage field set, so an SDK upgrade that adds a field
   fails CI.
6. **Token counting.** Claude uses the free `count_tokens` endpoint, so the ceiling is
   enforced before each call (ADR 0003). Gemini does not count before sending: an extra
   request per turn could spend free-tier request quota, and whether counting is billed or
   rate-limited has not been verified. Gemini's ceiling is checked after each call, so an
   episode can overshoot by at most one call. Revisit once counting cost is confirmed.

## Consequences
- Estimates for Gemini remain worst-case bounds; the one-call overshoot is small next to the
  ceiling but should be reported with Gemini results.
- Claude results depend on `count_tokens` being available on the account; this is verified
  before the first real run.
