# ADR 0011 — Markdown report from recorded files

- Status: accepted
- Date: 2026-09-26

## Context
Publishing a result needs a readable report that anyone can regenerate and check against
the raw files. Hand-written tables drift from the data and invite copying mock numbers.

## Decision
- `dowbench report RUN_DIR [-o FILE]` renders `run.json` and `summary.json`, validated
  with their models, as Markdown: run metadata (provider, requested and served model
  versions, commit, ceiling, threshold, spend), a table per defense, a table per attack
  episode, and the exact config to reproduce the run.
- The output is a pure function of those two files: no clock, no network, stable
  ordering. The same run directory always renders the same bytes.
- A simulated run gets a banner at the top saying it is not a result.

## Alternatives rejected
- A chart in this change: it needs a plotting dependency, and there is no real result to
  plot yet. Add it when there is.
- Rendering from `episodes.jsonl` directly: duplicates the metric code; `summary.json` is
  already the output of `summarize`.

## Consequences
- The report shows only what `summary.json` holds; a metric change needs a re-run of
  `summarize`, not a new paid run.
