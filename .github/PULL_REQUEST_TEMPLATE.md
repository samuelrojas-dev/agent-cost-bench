## What

What this change does, and why. Link the issue or ADR it belongs to.

## Kind

- [ ] fix
- [ ] feature
- [ ] docs
- [ ] refactor / chore
- [ ] test

## Checklist

- [ ] Small, focused change; Conventional Commits.
- [ ] An ADR added under `docs/adr/` for any non-obvious decision (or n/a).
- [ ] **No invented numbers** — every metric comes from a real, reproducible run.
- [ ] **No real provider call** added to tests or scripts; tests use the mock and stay offline.
- [ ] **No secrets** in code, logs, tests or commits.
- [ ] If a test's expected behavior changed, the PR says which and why.

## Verification

Paste the output (or confirm each passed):

```
ruff check .        && ruff format --check .
mypy src tests examples scripts
python -m pytest --cov=dowbench
python scripts/verify_cassettes.py
```
