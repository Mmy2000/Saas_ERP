# ADR-020: Quality gates: one local command, the same in CI

- Status: Accepted
- Date: 2026-10-24
- Deciders: project owner (Mahmoud), during the engineering standards pass

## Context

Until now, checking a change meant knowing four commands: `ruff check`, the translation check,
`makemigrations --check`, and `pytest` with a database. CSS and icons are built and committed by
hand. Nothing ran automatically, and nothing scanned dependencies for known vulnerabilities. The
repository is on GitHub, and the team works on Windows.

## Decision

- **`python ops/check.py`** runs, in order: lint, translations, migrations matching the models,
  and the test suite. `--quick` skips the tests, and arguments after `--` go to pytest.
  - It is plain Python, so it works the same in PowerShell, bash and CI, with no make or task
    runner.
- **GitHub Actions** (`.github/workflows/ci.yml`) runs on every pull request, on pushes to
  `main`, and every Monday:
  - **checks:** PostgreSQL 17 with the roles from `ops/db/bootstrap.sql`, Chromium for the PDF
    tests, then `ops/check.py`. Required.
  - **assets:** `npm ci && npm run build`, then fail if the committed CSS, icons or vendored
    files differ. Required.
  - **dependencies:** `pip-audit` and `npm audit`. Advisory (`continue-on-error`): a new
    advisory against a pinned package must not block unrelated work.
- **`main` is protected** in GitHub settings: "checks" and "assets" must pass before merging.
  This is configured on GitHub, not in the repository.
- **No pre-commit framework.** Developers run `ops/check.py --quick` before pushing; CI is the
  gate.

## Alternatives considered

- **pre-commit hooks.** A new tool to install on every Windows machine, and a second place
  listing the checks. They could be added later, calling `ops/check.py --quick`.
- **A Makefile.** Not available by default on Windows.
- **A blocking dependency scan.** It would turn unrelated pull requests red whenever a new
  advisory appears.

## Consequences

- One list of checks: `ops/check.py`. CI calls it rather than repeating its commands, so adding
  a check means editing one file.
- The workflow could not be run before it was pushed. The first CI run on GitHub is its real
  test: check that the database bootstrap and the asset comparison behave on Linux.
- Someone must read the advisory job's results, at least weekly.
