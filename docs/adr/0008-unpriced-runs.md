# ADR 0008 — Unpriced runs, capped in tokens

- Status: accepted
- Date: 2026-09-26

## Context
The first real Gemini test runs on a free-tier model whose price is not in the table (the
official pricing page is not reachable from the build environment, and ADR 0005 forbids
prices not copied from it). The budget guard was USD-only, so such a run could not start.
The test must also be a single, cheap episode, but every attack drags in its paired benign
task, so the smallest plannable run was two episodes.

## Decision
- `unpriced: true` in a run config means "do not price". Episode `cost_usd` is `null`,
  `estimate` reports tokens and no USD, and amplification is `null` (no USD baseline).
  Nothing is converted from tokens to dollars.
- Unpriced real runs require `--budget-tokens`, checked against the same worst case as the
  USD guard (`pending × ceiling.max_total_tokens`); `--budget-usd` is refused for them,
  since it cannot be checked. `--budget-tokens` also works as an extra cap on priced runs.
- `estimate` also prints the maximum number of model calls (`pending × max_turns`); each
  may be preceded by one free token-count request.
- `benign_tasks` lists benign tasks to run without any attack.

## Alternatives rejected
- Price free-tier models at $0: a free tier has quotas and may bill after them; zero would
  be an invented number.
- Amplification in tokens for unpriced runs: a different metric from ADR 0001; results
  would not be comparable. Revisit if free-tier results ever need ASR.

## Consequences
- An unpriced run gives token counts only; it cannot produce ASR or amplification.
- `unpriced` is a config choice, not an automatic fallback: a missing price on a priced
  run still fails.
