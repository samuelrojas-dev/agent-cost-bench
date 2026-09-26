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
One `cassette.jsonl` per run directory, written on every real run. One row per
recorded call:

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

### Not "simulated", but not a fresh result either
Replayed usage is **real recorded data**, so a replay is not `simulated` (the mock
banner would be wrong). But a replay is also **not an independent new result** —
publishing it as one would double-count a run. A distinct `replayed=True` marker on
`run.json` and a report banner ("replayed from a prior real run; not an independent
result") keep it honest; `simulated` and `replayed` are mutually exclusive.

## Alternatives rejected
- **Reuse `requests.jsonl` + `calls.jsonl`**: neither can drive the loop —
  requests.jsonl has no responses, calls.jsonl has no full turn.
- **Hash-based lookup instead of in-order**: brittle when a request repeats and it
  hides drift. The digest is kept only as a drift check on ordered replay.
- **A general VCR/HTTP-record library**: records at the HTTP layer where the key
  lives in headers (ADR 0009) and adds a dependency. Recording the adapter-level
  body/response keeps keys out by construction.
- **Treat replay as `simulated`**: the numbers are real; the mock banner would
  misrepresent them. A separate `replayed` marker is honest.
- **A `--record` flag**: recording is cheap and always useful on a real run, so it
  is unconditional — one code path, no way to forget it.

## Consequences
- A new offline, deterministic test/CI/demo path over real-shaped data, with no key
  and no spend.
- Two `exclude=True` fields become serializable on the cassette path only, base64,
  through the sanitizer and the secrets test.
- Cassettes must be regenerated when request construction changes; the digest check
  makes that a loud failure, not a silent wrong replay.
