# ADR 0015 — Replay cassettes (proposed)

- Status: proposed
- Date: 2026-09-26

> This ADR is a **design proposal**, not an accepted decision. Nothing here is
> implemented yet. It is written to be reviewed and approved (or changed) before
> any code lands.

## Context
Today a real run leaves three files with request/response data, none of which can
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

## Proposed decision
Add an explicit **record → replay** path, off by default, never touching the real
run behaviour unless asked.

### 1. Cassette file
One `cassette.jsonl` per run directory, written only when recording is on. One row
per recorded call:

```
{
  "episode_id": "...", "attempt": "...", "turn": 0,
  "request_digest": "sha256:...",     # over the sanitized request (see §3)
  "request": { ...sanitized request body... },   # for auditing / drift diff
  "response": {
    "text": "...", "tool_calls": [...], "stop_reason": "tool_use",
    "usage": {...}, "model_version": "...", "latency_s": 0.0,
    "provider_data": { ... }          # signatures/thinking, needed for verbatim replay (ADR 0006)
  }
}
```

Every row passes through the **same `sanitize()`** that already guards
`requests.jsonl` (ADR 0009): credential-shaped keys dropped at any depth, key-shaped
values masked. `provider_data` (thought signatures) is captured raw but is not a
credential; the sanitizer still runs over it as defense in depth.

### 2. `ReplayProvider`
A provider that implements the `Provider` protocol and makes **no network call**.
On `complete()` / `count_tokens()` it returns the recorded response for the
current `(episode_id, attempt, turn)`. It is wired the same way as
`RateLimitedProvider` — a wrapper/source chosen in `build_provider`, selected by a
config field or a `--replay <dir>` flag (open question §6).

Rate limiting (ADR 0014) and the network are bypassed entirely under replay:
replay makes no requests, so RPM/TPM/RPD do not apply.

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

### 4. Not "simulated", but not a fresh result either
Replayed usage is **real recorded data**, so a replay is not `simulated` (the mock
banner would be wrong and misleading). But a replay is also **not an independent
new result** — publishing it as one would double-count a run. Proposal: add a
distinct `replayed=True` marker on `run.json` and a report banner ("replayed from a
prior real run `<commit>/<run>`; not an independent result"), separate from the
existing simulated banner. `simulated` and `replayed` are mutually exclusive.

### 5. Budget and reporting parity
Replay bills nothing new, but the budget/estimate machinery still runs over the
recorded usage so a replayed run's `summary.json` and report match the original —
that parity is exactly what makes replay useful for regression tests.

## Open questions for approval
1. **Selection surface**: a `--replay <run-dir>` CLI flag, a `replay:` config
   field, or a dedicated provider name `replay`? (Leaning `--replay`, so a cassette
   can replay against any config without editing it.)
2. **Recording surface**: a `--record` flag on `run` that adds cassette writing to
   a real run, vs. a separate `dowbench record` command. (Leaning `--record`, one
   code path.)
3. **Where committed cassettes live for tests**: a small `cassettes/` dir in the
   repo (tiny, sanitized) for CI/regression, separate from `results/raw/` (which is
   git-ignored). Size and what to commit need your call.
4. **Provider coverage**: record Gemini and Anthropic both, or start with one?
5. **`provider_data` persistence**: this flips two `exclude=True` fields to
   serializable *only on the cassette path*. Confirm that is acceptable (signatures
   are not secrets, and the sanitizer still runs).

## Alternatives considered
- **Reuse `requests.jsonl` + `calls.jsonl`**: rejected — requests.jsonl has no
  responses and calls.jsonl has no full turn (no text, tool calls, or signatures),
  so neither can drive the loop.
- **Hash-based lookup instead of in-order**: rejected as the primary key — brittle
  when a request legitimately repeats, and it hides drift. The digest is kept, but
  only as a drift check on top of ordered replay.
- **A general VCR/HTTP-record library**: rejected — it records at the HTTP layer,
  where the key lives in headers (ADR 0009), and adds a dependency. Recording the
  adapter-level body/response keeps keys out by construction.
- **Treat replay as `simulated`**: rejected — the numbers are real; the mock banner
  would misrepresent them. A separate `replayed` marker is honest.

## Consequences (if accepted)
- A new offline, deterministic test/CI/demo path over real-shaped data, with no
  key and no spend.
- Two `exclude=True` fields become serializable on the cassette path only; the
  sanitizer covers them.
- Cassettes must be regenerated when request construction changes (the digest check
  makes that a loud failure, not a silent wrong replay).
