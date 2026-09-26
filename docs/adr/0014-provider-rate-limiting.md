# ADR 0014 — Provider rate limiting

- Status: accepted
- Date: 2026-09-26

## Context
Real runs go against provider free tiers with three caps: requests per minute (RPM),
tokens per minute (TPM) and requests per day (RPD). The cumulative budget (ADR 0010) bounds
money and tokens, not request rate, so a run that fits the budget can still be throttled or
rejected. It has not been verified that `countTokens` is free of the request quota, so until
it is, every `countTokens` must be counted as a request next to the `generate` call it
precedes (ADR 0003).

## Decision
- A `rate_limit` config field (`RateLimit`: `rpm`, `tpm`, `rpd`, all > 0). Absent means no
  limiting.
- **Estimate** reports, from the worst case, the request count (generate + countTokens),
  the minimum wall time `max(requests / rpm, tokens / tpm)` and the days `ceil(requests / rpd)`.
- **Enforcement** applies to real providers only (the mock has no quota). A
  `RateLimitedProvider` wraps the provider so both `count_tokens` (0 tokens) and `complete`
  (reserving the call's `max_tokens`) pass through a `RateLimiter`, which paces RPM and TPM
  with a one-minute sliding window.
- **RPD** is a hard daily cap checked *between* episodes: before each episode the runner
  reserves its worst-case requests, and raises `RateLimitReached` when the day budget would
  be exceeded. The run stops with progress saved; `--resume` continues the next day. `run`
  exits with code 3.
- The clock and sleep are injected, so the limiter is deterministic in tests and never makes
  a call itself.

## Relation to ADR 0010 (cumulative budget)
Orthogonal and both enforced under the run lock: ADR 0010 caps spend (USD/tokens), ADR 0014
caps request rate (RPM/TPM/RPD). Neither duplicates the other; a run must satisfy both.

## Alternatives rejected
- Counting only `generate` calls: would undercount against the request quota while the
  `countTokens` cost is unverified, risking 429s. Counting both is the safe assumption.
- Reserving actual generate tokens for TPM: not known before the call; reserving
  `max_tokens` over-reserves, which is safe.
- Sleeping to span RPD within one run: a day is too long to hold a run open; stopping between
  episodes and resuming is simpler and loses nothing (episodes are recorded).

## Consequences
- The pilot on the Gemini free tier (`rpm 12`, `tpm 250000`, `rpd 500`) estimates 768
  requests, at least 64 minutes, across 2 days.
- TPM over-reservation makes pacing conservative; real throughput may be higher.
