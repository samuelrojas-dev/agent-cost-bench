# Contributing to dowbench

Thanks for your interest. dowbench is a reproducible benchmark, so a few of its rules are
stricter than a typical project's — they exist to keep every published number trustworthy.

## Ground rules (non-negotiable)

- **No invented numbers.** Metrics, coverage and badges come only from a real, reproducible
  run — never typed by hand. See [`CLAUDE.md`](CLAUDE.md).
- **No real provider calls in tests or scripts.** Tests use the mock provider and never touch
  the network; a network guard fails any test that tries. A real `dowbench run` is the only
  place a provider is called, and only against a budget you set (`--budget-usd`).
- **Secrets never in code, logs, tests or commits** — only environment variables, with
  `.env.example`. A sanitizer (ADR 0009) and a gitleaks CI job are the backstops.
- **An ADR for every non-obvious decision**, as a short file in [`docs/adr/`](docs/adr/).

## Development setup

```bash
git clone https://github.com/samuelrojas-dev/agent-cost-bench
cd agent-cost-bench
pip install -e '.[dev]'
pre-commit install        # optional but recommended: runs ruff, gitleaks and mypy
```

Run everything the CI runs, locally, before you push:

```bash
ruff check . && ruff format --check .
mypy src tests examples scripts     # strict
python -m pytest --cov=dowbench     # offline, mock providers only
python scripts/verify_cassettes.py  # published cassettes still reproduce their hashes
```

## Adding an attack, a defense or a provider

You do **not** need to edit the core — dowbench discovers components through entry points
(see [ADR 0021](docs/adr/0021-plugin-entry-points.md) and
[`examples/plugin_example/`](examples/plugin_example/)). Ship yours from your own package, or
propose it here with an ADR if it changes a convention.

- **Attack**: a YAML entry under `src/dowbench/attacks/data/` (or a `dowbench.attacks` pack),
  held to the bulk-only guard in `tests/test_amplifying_attacks.py`.
- **Defense**: a subclass of `Defense` (`src/dowbench/defenses/base.py`), registered as a
  `dowbench.defenses` entry point.
- **Provider**: a `dowbench.providers` factory returning a `Provider`; map its usage strictly
  (ADR 0002) and add a mock-backed test — never a real call.

## Commits and pull requests

- **Small commits**, [Conventional Commits](https://www.conventionalcommits.org/) style
  (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`).
- One logical change per pull request. Fill in the PR template, including the output of the
  checks above.
- Reference the issue or ADR the change belongs to.

## Reporting bugs and requesting features

Open an issue with the matching template. For anything security-related, follow
[`SECURITY.md`](SECURITY.md) and report privately instead.

## License

By contributing you agree that your contributions are licensed under the project's
[Apache-2.0](LICENSE) license.
