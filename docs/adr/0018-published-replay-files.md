# ADR 0018 — Publishing replay files for real runs

- Status: accepted
- Date: 2026-09-27

## Context
The README reports results from two real runs (pilot v2 and focus-bloat-verify) and gives
the sha256 of each `summary.json`. Until now nobody else could check them, because the run
directories live under `results/raw/`, which is git-ignored. Replay needs more than the
cassette: it reads `run.json` for the config and `episodes.jsonl` to know which attempt of
each episode completed (ADR 0015). Each cassette is about 1.1–1.3 MB, over the 500 KB limit
of the `check-added-large-files` pre-commit hook.

## Decision
1. Published runs go in `results/cassettes/<run>/` with `cassette.jsonl`, `run.json`,
   `episodes.jsonl` and the recorded `summary.json`. `results/raw/` stays ignored; files
   are copied out of it one run at a time, after an audit (no keys, emails, local paths or
   content outside the synthetic payloads).
2. `check-added-large-files` excludes `^results/cassettes/`. The 500 KB limit still applies
   everywhere else.
3. The files are plain git text files, not Git LFS: they are small, diffable, and a clone
   can replay them without extra tooling. `.gitattributes` keeps them LF, so the recorded
   `summary.json` hash survives checkout on Windows.

## Consequences
- Anyone can run `dowbench replay results/cassettes/<run>` and compare the hash, with no
  key and no spend.
- The repository grows by about 2.5 MB per pair of runs like these. If published runs get
  much larger, revisit LFS or compression.
- Publishing is a manual, audited step; nothing under `results/raw/` is published
  automatically.
