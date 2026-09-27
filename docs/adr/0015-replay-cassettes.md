# ADR 0015 — Replay cassettes

- Status: accepted
- Date: 2026-09-26

## Context
A real run leaves three files with request/response data, none of which can
replay a run:

- `calls.jsonl` — one row per call with usage, `stop_reason`, `model_version`,
  latency. It **summarizes** the response; it does not keep the assistant turn
  (text, tool calls, thinking blocks, signatures).
- `requests.jsonl` — the sanitized request body per call, an **audit log** only
  (ADR 0009 states this explicitly: "an audit log, not a replayable cassette").
- `episodes.jsonl` / `summary.json` — aggregates.

Also, the two fields that carry a turn verbatim — `Message.provider_data` and
`Response.provider_data` (thinking blocks, thought signatures, ADR 0006) — are
declared `exclude=True`, so they are never serialized at all.

So there is no way to re-run the benchmark offline from recorded real responses.
We want that for four reasons: deterministic CI without a key or quota,
regression tests over the runner/metrics on real-shaped data, demos that show
real numbers without billing, and letting a third party reproduce the *analysis*
of a published result without re-spending. The mock provider (deterministic,
`simulated=True`) covers "the pipeline runs", but its numbers are synthetic and
never publishable — it cannot stand in for a real recorded run.

## Decision
Add an explicit **record → replay** path. Recording is **always on for real runs**
(no flag); replay is a separate command that makes no network call.

### 1. Cassette file
One `cassette.jsonl` per run directory, written on **every run** (real and mock alike,
so the mock quickstart can demonstrate `dowbench replay` offline with no key). A replay
mirrors the source's simulated flag, so replaying a mock cassette stays SIMULATED and is
never mistaken for a result. One row per recorded call:

```
{
  "episode_id": "...", "attempt": "...", "turn": 0,
  "request_digest": "sha256:...",     # over the sanitized request (see §3)
  "request": { ...sanitized request body... },   # for auditing / drift diff
  "response": {
    "text": "...", "tool_calls": [...], "stop_reason": "tool_use",
    "usage": {...}, "model_version": "...", "latency_s": 0.0,
    "provider_data_b64": "..."         # base64 of the JSON provider_data (see §5)
  }
}
```

Every row passes through the **same `sanitize()`** that already guards
`requests.jsonl` (ADR 0009): credential-shaped keys dropped at any depth, key-shaped
values masked.

### 2. `dowbench replay <run_dir>`
A dedicated command replays the cassette in `<run_dir>` and rewrites the derived
files (`episodes.jsonl`, `summary.json`, `run.json`) with no network call and no
new spend. Internally a **`ReplayProvider`** implements the `Provider` protocol and
returns recorded responses. Rate limiting (ADR 0014) and the network are bypassed
entirely: replay makes no requests, so RPM/TPM/RPD do not apply.

### 3. Matching: in order, digest-checked
The agent loop is deterministic given the sequence of responses (turn N's request
is fully determined by responses 1..N-1). So replay matches **in recorded order
within an episode**, keyed by `(episode_id, attempt, turn)`, and does **not** hash
requests to look them up.

The `request_digest` is used only to **detect drift**: on replay we recompute the
digest of the request the loop actually produced and compare it to the recorded
one. A mismatch means the code changed how requests are built (prompt, tools,
ceiling), so the cassette no longer describes this code — replay **raises** rather
than returning a stale response. A missing turn also raises. Replay never silently
falls through to a real call.

The digest is computed over the sanitized request body with sorted keys, so it is
stable across machines and excludes anything secret.

### 4. Provider coverage
Gemini first, behind a **provider-generic interface**: the cassette format and the
`ReplayProvider` are provider-neutral (they store and replay the neutral
`Request`/`Response`), so Anthropic is added later without a format change.

### 5. `provider_data` on the cassette path only
Recording serializes `provider_data` (thought signatures, thinking blocks) **only on
the cassette path** — the `exclude=True` fields stay excluded from every other dump.
It is stored **base64-encoded** (`provider_data_b64`) after running the request/
response through `sanitize()`; base64 keeps opaque signature bytes intact across the
JSONL round-trip. Signatures are not credentials, but the **secrets test is extended
to cover the cassette file** so no key can ride along even after base64.

### 6. Where cassettes live
- **Test cassettes are synthetic**, committed under `tests/fixtures/` (tiny, no real
  provider data), so CI and regression tests run offline with no key and no real
  bytes in the repo.
- **Real cassettes** are written to the git-ignored `results/` tree and only copied
  to `results/cassettes/` when a result is deliberately published.

### `replayed`, and how it relates to `simulated`
A replay carries a distinct `replayed=True` marker on `run.json` and a report banner
("replayed from a prior real run; not an independent result"), because replaying real
recorded data is **not an independent new result** — publishing it as one would
double-count a run. `replayed` is orthogonal to `simulated`: a replay mirrors the
source's `simulated` flag, so replaying a **real** cassette is `simulated=False`,
`replayed=True` (real numbers, but not a fresh result), while replaying a **mock**
cassette stays `simulated=True` (synthetic numbers, never a result at all). When both
are true the report shows the stronger SIMULATED banner; the REPLAYED banner shows only
for a replayed real run.

## Alternatives rejected
- **Reuse `requests.jsonl` + `calls.jsonl`**: neither can drive the loop —
  requests.jsonl has no responses, calls.jsonl has no full turn.
- **Hash-based lookup instead of in-order**: brittle when a request repeats and it
  hides drift. The digest is kept only as a drift check on ordered replay.
- **A general VCR/HTTP-record library**: records at the HTTP layer where the key
  lives in headers (ADR 0009) and adds a dependency. Recording the adapter-level
  body/response keeps keys out by construction.
- **Treat every replay as `simulated`**: a real cassette's numbers are real; the mock
  banner would misrepresent them. Mirroring the source's flag plus a separate
  `replayed` marker is honest for both real and mock cassettes.
- **A `--record` flag**: recording is cheap and always useful, so it is unconditional
  on every run — one code path, no way to forget it.
- **Record only real runs**: would leave the mock quickstart unable to demonstrate
  replay offline. Recording the mock too costs nothing and keeps one code path; the
  mirrored `simulated` flag keeps a replayed mock clearly SIMULATED.

## Consequences
- A new offline, deterministic test/CI/demo path over real-shaped data, with no key
  and no spend.
- Two `exclude=True` fields become serializable on the cassette path only, base64,
  through the sanitizer and the secrets test.
- Cassettes must be regenerated when request construction changes; the digest check
  makes that a loud failure, not a silent wrong replay.

## Amendment (2026-09-27): two bugs in the first implementation
The first implementation shipped two bugs, both fixed and covered by a regression fixture
(`tests/fixtures/replay-regression`, config with a `rate_limit` and an orphan attempt):

1. **A replay was still wrapped in the rate limiter.** The rate-limit wrap keyed only on
   `rate_limit is set` and `not simulated`, so replaying a real run whose config carried a
   `rate_limit` wrapped the `ReplayProvider` in `RateLimitedProvider`. Replay makes no
   network call, so this was pure harm: it slept on real wall-clock time while reproducing a
   recorded run, and — because the runner calls `begin_episode` on the outermost provider
   only — the hook was swallowed and replay drifted. Fix: skip the wrap when replaying; and,
   should anything wrap a replay again, `RateLimitedProvider` now forwards `begin_episode` to
   its inner provider.
2. **`load_cassette` grouped by `episode_id` alone.** A run dir can hold more than one
   attempt of an episode: an interrupted attempt leaves its calls in the cassette but writes
   no episode row (ADR 0007). Grouping by episode spliced the orphan attempt's calls before
   the completed one, so replay consumed the orphan's first call and then drifted. Fix:
   match the cassette by `(episode_id, attempt)` and keep only the attempt named in
   `episodes.jsonl` (the one that completed), discarding orphans. This is why replay reads
   `episodes.jsonl`, and why a replayable run dir must include it alongside `cassette.jsonl`.
