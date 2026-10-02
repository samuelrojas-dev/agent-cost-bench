# Releasing dowbench to PyPI

dowbench publishes through **Trusted Publishing (OIDC)**: GitHub Actions authenticates to PyPI
with a short-lived token minted per run, so there is **no API token stored** in the repo or in
Actions secrets. The workflow is [`.github/workflows/release.yml`](../.github/workflows/release.yml).

The version is single-sourced from `__version__` in `src/dowbench/__init__.py`
(`[tool.hatch.version]` in `pyproject.toml`); nothing else needs editing to bump it.

## One-time setup (maintainer, before the first publish)

This needs a human with the PyPI/TestPyPI account — it cannot be automated from here.

1. Create accounts on **PyPI** (<https://pypi.org>) and **TestPyPI** (<https://test.pypi.org>), each
   with 2FA enabled.
2. On **both**, add a **pending trusted publisher** (Account → Publishing) for this project:
   - PyPI project name: `dowbench`
   - Owner: `samuelrojas-dev` · Repository: `agent-cost-bench`
   - Workflow filename: `release.yml`
   - Environment name: `pypi` on PyPI, `testpypi` on TestPyPI
3. In the GitHub repo, create two **Environments** (Settings → Environments) named `pypi` and
   `testpypi` to match. (Optional: add required reviewers on `pypi` so a publish waits for a click.)

No secret is ever added to the repository.

## Dry run to TestPyPI

Before the real release, run the workflow manually to publish to TestPyPI:

- GitHub → Actions → **release** → **Run workflow** (a `workflow_dispatch`).
- It builds, runs `twine check`, and publishes to TestPyPI via the `testpypi` environment.
- Verify the install from TestPyPI in a clean virtualenv:

  ```bash
  python -m venv /tmp/dowbench-test && . /tmp/dowbench-test/bin/activate
  pip install --index-url https://test.pypi.org/simple/ \
      --extra-index-url https://pypi.org/simple/ dowbench
  dowbench scan examples/agent_tools.json --format plain   # from a checkout, for the sample file
  ```

  (The extra index pulls runtime deps — pydantic/typer/pyyaml — from real PyPI, since TestPyPI
  may not mirror them.)

## Cutting a release

1. Bump `__version__` in `src/dowbench/__init__.py` (semver) and add a `CHANGELOG.md` entry.
2. Merge that to `main`.
3. Tag and push:

   ```bash
   git tag v0.1.0        # must equal __version__ — the workflow fails the build if they differ
   git push origin v0.1.0
   ```

4. The tag push triggers the workflow, which builds, checks, and publishes to **PyPI** via the
   `pypi` environment. Watch the run; on success the version appears at
   <https://pypi.org/project/dowbench/>.
5. Create a GitHub Release from the tag with the changelog notes (optional but recommended).

## Local build check (no publish)

```bash
python -m build                 # writes dist/*.whl and dist/*.tar.gz
python -m twine check dist/*
```

`dist/` is git-ignored; never commit built artifacts. CI builds fresh from the tagged source.
