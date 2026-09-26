# ADR 0010 — Cumulative budget and one run per directory

- Status: accepted
- Date: 2026-09-26

## Context
ADR 0007 left two spend risks open. `--budget-usd` bounded only the pending episodes of one
invocation, so every `--resume` could spend a full new budget on top of what earlier
attempts had already billed. And nothing stopped two `run` processes on the same run
directory, each within its own budget, doubling spend and interleaving their files.

## Decision
- **Cumulative budget.** `--budget-usd` and `--budget-tokens` cap the whole run directory.
  A run starts only if what `calls.jsonl` already records plus the worst case of the
  pending episodes fits every given budget. `calls.jsonl` holds every billed call, so
  errored episodes and interrupted attempts count too. `estimate` prints what the
  directory already spent; `run.json` records `spent_tokens` and `spent_usd` at the start
  and end of every invocation.
- **One run per directory.** `execute()` holds `run.lock`, created atomically with O_EXCL
  and holding pid, host and start time, for the whole invocation, and removes it on exit,
  including on errors. A second run on the directory is refused before any call.
- **The check that counts is inside the lock.** `execute()` computes spent and worst case
  and checks the budget after taking the lock, so no caller can skip it and no parallel
  run can race it. The CLI runs the same check earlier only to refuse before building a
  provider.

## Alternatives rejected
- `fcntl.flock`: released automatically if the process dies, but POSIX only and unreliable
  on some network filesystems.
- Treating a lock as stale when its pid is gone: a pid can be reused, and the holder may be
  on another host. A wrong guess allows parallel spend. A leftover lock needs a human.
- Budget per invocation with a separate "total" flag: two flags for one limit invite using
  the wrong one. The flags now mean the total.

## Consequences
- Resuming with the same `--budget-usd` fails once earlier spend plus the pending worst
  case exceeds it; raise the budget explicitly.
- After a crash or `kill -9`, `run.lock` stays and the next run is refused until it is
  deleted by hand.
- An errored call whose usage could not be mapped counts with the usage that was mapped
  (possibly zero); its raw usage is in `calls.jsonl` for manual review.
- The lock guards one run directory. Two directories on the same account still spend in
  parallel; the provider's own spend limits are the backstop for that.
