# Security policy

dowbench is a benchmark of *denial-of-wallet* attacks against LLM agents. Its attack payloads
are synthetic and deliberately low-power, kept in `src/dowbench/attacks/data/`; they are for
measuring cost amplification, not for use against systems you do not own.

## Reporting a vulnerability

Please report security issues **privately**, not in a public issue.

- Use GitHub's private reporting: on the repository, go to **Security → Report a
  vulnerability** ([GitHub Security Advisories](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)).
- If that is unavailable to you, open a minimal public issue that says only that you have a
  security report and asks for a private channel — do **not** include details.

Please include enough to reproduce: affected version or commit, steps, and impact. We aim to
acknowledge a report within a few days.

## What is in scope

- A way to make dowbench itself spend beyond its `--budget-usd` / `--budget-tokens` guard, or
  to bypass the safety ceiling.
- Leakage of a provider key or other secret into any file dowbench writes (`calls.jsonl`,
  `requests.jsonl`, `cassette.jsonl`, logs) — the sanitizer (ADR 0009) is meant to prevent
  this.
- A cassette or config that causes code execution or path traversal on replay.

## What is not in scope

- The attack payloads doing what they are designed to do (amplifying cost against a
  cooperative model) — that is the benchmark working as intended.
- Cost incurred by running `dowbench run` against a real provider with a budget you set.

## Handling of secrets

dowbench never writes API keys to disk: adapters read exactly one environment variable, keys
never enter request bodies, and a sanitizer plus a gitleaks CI job are the backstops. If you
find a path that defeats this, it is in scope above.
