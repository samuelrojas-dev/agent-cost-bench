# ADR 0020 — Durable spend and transient-aware resume

- Status: accepted
- Date: 2026-09-29

## Context

Phase 1 of [ADR 0019](0019-path-to-1.0.md) fixes the two known correctness bugs
([#36](https://github.com/samuelrojas-dev/agent-cost-bench/issues/36),
[#37](https://github.com/samuelrojas-dev/agent-cost-bench/issues/37)).

**#36 — spend is not durable.** `runner/store.py` appends `calls.jsonl` with a plain
`open`/`write`/`close`; the bytes reach the OS page cache but are not `fsync`ed. A container
or machine crash can lose the last write(s). `budget.spent()` then reads a short file and the
cumulative-budget guard under-counts, so a `--resume` can exceed `--budget-usd`.

**#37 — a transient failure drops an episode on resume.** Adapters wrap only
`UsageMappingError` (a *terminal* condition: a billed response whose usage cannot be priced,
ADR 0007). A rate-limit (HTTP 429) or a 5xx from the SDK is not caught anywhere: it
propagates uncaught and aborts the whole run. The episode in flight leaves billed calls in
`calls.jsonl` but no episode row, and the run stops on a raw SDK traceback. Nothing retries a
blip, and `errored` conflates "unpriceable" with "temporarily unavailable".

## Decision

### Durability (#36)

Every append and every whole-file write in `RunStore` flushes and `fsync`s before returning
(`fh.flush(); os.fsync(fh.fileno())`). A record that `append_call`/`append_episode` has
returned from is on stable storage, so spend recomputed from `calls.jsonl` on the next
`--resume` is correct.

The residual window is inherent and documented: a call is billed the instant the provider
responds, and the record is written immediately after. A kill *between* the response and that
write loses that one call. Accounting is therefore **at-least-once and never silently under**
for anything that reached `append_call`; we do not claim exactly-once against a kill mid-write
of a single line. This is the honest guarantee a cost guard needs.

### Transient vs terminal (#37)

1. **A transient error is a named type.** `providers/base.py` gains
   `TransientProviderError` and `is_transient_error(exc)`. The classifier is SDK-agnostic:
   it treats an exception as transient when its `status_code`/`code` is one of
   `{408, 409, 425, 429, 500, 502, 503, 504}`, or its type name matches a timeout/connection
   error. Each adapter wraps its SDK call and re-raises a matching exception as
   `TransientProviderError`; anything else propagates unchanged.
2. **Bounded, visible retry.** A `RetryingProvider` decorator retries `complete` and
   `count_tokens` on `TransientProviderError` with exponential backoff (default 3 attempts,
   0.5 s base, capped), sleeping through an injectable clock so tests never wait. A 429/5xx is
   a *rejected* request — not billed — so retrying it adds no hidden spend (consistent with
   ADR 0010's "no SDK retries"; the Anthropic adapter keeps `max_retries=0`, ours is the only
   retry and it is bounded and logged). Retries sit inside recording and rate limiting, so
   each attempt still passes the limiter and only the successful response is recorded.
3. **Resume re-runs a transient failure.** If retries are exhausted, the episode is **not**
   written — no episode row, so its id is not in the resume `done` set and the next
   `--resume` runs it again. The run stops with `RunInterruptedError`, which says the episode
   will be retried on resume (distinct from `EpisodeErroredError`, the terminal path, which
   keeps its "resume skips it" message because re-running an unpriceable call cannot help).
   Retry granularity is the **episode**, not the call: a resumed episode re-bills the calls it
   already made before the blip. That double-count is accepted as the price of independent,
   restartable episodes; the cumulative-budget guard still counts every billed call.

### Configuration

`RunConfig` gains an optional `retry` block (`max_attempts`, `base_delay_s`, `max_delay_s`)
with the defaults above; `retry: {max_attempts: 1}` disables retrying. It applies only to
real providers (the mock has no transient errors, and replay makes no calls).

## Consequences

- A crashed run resumes with correct spend; `--budget-usd` is not silently exceeded.
- A rate-limit blip no longer aborts a long run, and a genuinely unfinished episode is
  re-run on resume instead of being skipped forever.
- Regression tests pin both: a mock that records a call then interrupts (spend counts it, and
  `os.fsync` is exercised), and a mock that raises a transient error — once (retry recovers)
  and past the retry budget (episode not in `done`; a second pass completes the matrix).
- `fsync` per line adds a syscall per call; runs are network-bound, so the cost is
  negligible next to a provider round-trip, and mock/replay runs stay fast.
