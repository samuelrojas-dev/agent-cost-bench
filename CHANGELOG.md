# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Results (pilot)** section in the README, reporting the first real findings from the
  maintainer's local `gemini-3.5-flash-lite` runs (pilot v2 — 10 attacks × 5 defenses; and a
  focus run of `bloat-verify-001` × 5 defenses, 5 repeats). Numbers are attributed to those
  local runs and marked as not reproduced in CI (#26).
- Published the replay files for both runs under `results/cassettes/` (`cassette.jsonl`,
  `run.json`, `episodes.jsonl`, `summary.json`), so `dowbench replay` reproduces each
  `summary.json` byte for byte offline — no key, no spend (#27).
- Pilot v1 recorded in the README as an antecedent: the earlier 4 attacks × 4 defenses run
  (ASR 0 % in every cell) whose null result motivated the amplifying attacks of ADR 0016
  (#28).
- `dowbench estimate` output for `configs/pilot-gemini-v2.yaml` added to the README's
  Results (pilot) section — its worst-case budget envelope (75 episodes, 1.2M tokens / $3.00,
  900 requests across 2 days), noted as a ceiling, not the billed cost (#31).

### Changed

- README replay commands now point at the published `results/cassettes/` directories instead
  of only showing a hash to verify against (#27).
- Consolidated the two overlapping README result sections: "Replaying a real run" is now a
  brief pointer to "Results (pilot)", removing the duplicated, stale pilot narrative (#28).
- README status line now reflects that preliminary pilot findings are published, framed as
  early pilots (`n = 1`–`5`, directional not settled) rather than "no results yet" (#29).
