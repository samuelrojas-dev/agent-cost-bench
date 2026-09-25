# ADR 0003 — Safety ceiling and censored episodes

- Status: accepted
- Date: 2026-09-25

## Context
Measuring an undefended agent under a DoW attack could, by definition, spend without bound.
The project runs on free tiers and small budgets, so every episode needs a hard cap, and the
cap must not silently distort the results.

## Decision
- Every episode, including the `none` defense, runs under a **safety ceiling**:
  `max_turns`, `max_tokens_per_call` and `max_total_tokens`.
- Before each call the runner asks the provider to count the request's input tokens. If
  `spent + input + max_tokens` would exceed `max_total_tokens`, the episode stops as
  **censored** without sending the call. With a provider that can count tokens, an episode
  therefore never exceeds `max_total_tokens`; if the provider cannot count, the ceiling is
  checked after each call and the overshoot is bounded by one call.
- The ceiling is part of the harness, not a defense; it is reported separately from defense
  aborts.
- Censored episodes report their cost as a lower bound (`>=`) and are handled per ADR 0001.
- The pre-run estimate uses `episodes × max_total_tokens × highest per-token price` as the
  worst case, and `run` refuses to start when that exceeds the given budget.

## Consequences
- The ceiling should be set well above the benign cost so that the threshold of ADR 0001 can
  be reached before censoring.
- Reports must show the censored count next to every median.
