# ADR 0007 — Fixes from the adversarial review of spend and key handling

- Status: accepted
- Date: 2026-09-26

## Context
An independent review tried to break the budget guard and key handling at commit a43e504.
It confirmed four problems and raised four plausible ones. Two confirmed problems
contradicted earlier ADRs: ADR 0005 claimed stray Gemini settings could not bill another
account or redirect the key, and that "at most one call per run is unrecorded".

## Decision
1. **Gemini endpoint and live client pinned** (confirmed, high). `GOOGLE_GEMINI_BASE_URL`
   sent `GEMINI_API_KEY` to any host, and `GOOGLE_GENAI_CLIENT_MODE=replay` turned the
   client into a replay client whose canned responses would be recorded as real. The
   adapter now passes `base_url="https://generativelanguage.googleapis.com/"` and a
   `DebugConfig` with every field explicitly `None`. Reproduced before the fix
   (`ReplayApiClient http://127.0.0.1:9/attacker/`) and after (`BaseApiClient
   https://generativelanguage.googleapis.com/`); a test sets all four variables.
2. **Billed calls are written as they return** (confirmed, medium). Calls were written only
   when an episode ended, so any later exception lost every billed call of that episode,
   and a deterministic error re-billed on each resume. Now:
   - `run_episode(on_call=...)` hands each call to the store at once.
   - Every call and episode row carries an `attempt` id; call rows whose attempt has no
     episode row belong to an interrupted attempt.
   - A billed call whose usage cannot be priced (`UsageMappingError`, including a missing
     raw usage) is recorded with the provider's raw usage and an `error`, the episode ends
     as `errored`, and the run stops. Resume skips it rather than paying again.
   - Metrics treat `errored` like `censored` (cost is a lower bound: a success only if it
     already reached the threshold), exclude errored benign episodes from the baseline,
     and count them per defense.
3. **`--budget-usd` must be finite and non-negative** (confirmed, low). `nan` disabled the
   check because every comparison with it is false. The comparison is also written so that
   a NaN fails it.
4. **Two plausible holes closed before any spend**:
   - A non-simulated provider whose `count_tokens` returns `None` is refused: without a
     count, neither the ceiling nor the price-tier premise (ADR 0005) holds.
   - A defense whose `before_call` changes the model or the tools is refused: costs are
     priced by the planned model.

## Corrections to earlier ADRs
- ADR 0005, consequence "at most one call per run is unrecorded": false before this ADR.
  It is now true for calls interrupted by transport errors or timeouts (their billing is
  unknown); every call that returned is on disk.
- ADR 0005, key safety: held for `GOOGLE_API_KEY` and Vertex settings, not for the base-URL
  and replay variables until this ADR.

## Not fixed (accepted, documented)
- **Budget is per invocation.** `--budget-usd` bounds the pending episodes of one `run`;
  spend of earlier invocations and of interrupted attempts is not subtracted. Two `run`
  processes on the same run directory would each spend up to their budget. Mitigation for
  now: one run per directory, by hand. A lock file and a cumulative total are the fix if
  runs become unattended.
- **Orphan spend is not in the summary.** Calls of interrupted attempts are in
  `calls.jsonl` but not in any episode cost. They are harness overhead, not attack cost;
  a report of total billed spend should sum `calls.jsonl`, not episodes.
- **Proxy and TLS variables** (`HTTPS_PROXY`, `SSL_CERT_FILE`) are honored by both SDKs. That
  is standard and needed in this environment; a hostile proxy could see traffic.

## Process note
During the review one request reached `api.anthropic.com/v1/messages/count_tokens` with a
fake key (HTTP 401): the reviewer's script patched `httpx` while SDK 1.8 uses `httpx2`. No
real key was involved and nothing was billed, but it broke the rule in CLAUDE.md. Review
scripts must block sockets, not patch one HTTP library.
