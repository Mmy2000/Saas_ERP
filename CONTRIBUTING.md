# Contributing

How we work on Gweb. Read [docs/](docs/README.md) first; the
[architecture overview](docs/architecture/overview.md) is the place to start.

## Set up

Follow README "Local setup (Windows / PowerShell)". In short:

1. Create the database roles once with `ops/db/bootstrap.sql`.
2. Create `.env` from `.env.example`.
3. Install the Python packages: `venv\Scripts\python.exe -m pip install -r requirements/dev.txt`.
4. Install Chromium: `python -m playwright install chromium`.
5. Apply the schema: `manage.py migrate --database owner`.
6. Create a tenant: `manage.py create_tenant ...`.
7. Install Node packages only if you'll touch CSS or icons: `npm install`, then
   `npm run build`.

Check the setup with `.\venv\Scripts\python.exe ops\check.py`. Everything should pass on a
clean checkout.

## Branches

- **`main` is always releasable.** Nobody pushes to it directly; changes arrive by pull request
  with green CI.
- **One branch per change, cut from `main`, short-lived** (days, not weeks):
  - `feature/<short-name>` for new behaviour, e.g. `feature/expense-refunds`.
  - `fix/<short-name>` for bugs, e.g. `fix/cash-box-race`.
  - `chore/<short-name>` for tooling, docs and refactoring without behaviour change.
- **Keep the branch current** by rebasing on `main` (`git fetch && git rebase origin/main`) or
  merging it in. Resolve conflicts in migrations by renumbering *your* new migration, never an
  applied one.

## Commits

Follow the existing history:

- **Subject:** an imperative sentence saying what changes for the user or the code, under about
  72 characters, no prefix, no full stop. For example: `Add the diamond category card report`,
  `Send bank money to another branch in two steps`.
- **Body:** wrapped at about 72 characters. Say why, what's not obvious, and what was left out.
- **One logical change per commit.** Tests and docs go in the same commit as the code.
- **Never commit** `.env`, credentials, `var/`, or customers' data.

## Coding standards

The rules are in [docs/standards/](docs/README.md#standards). The ones reviewers will hold you
to first:

- Business rules live in **service functions** (atomic, lock-then-check, `actor.require`),
  never in views, serializers, templates or JS ([architecture](docs/standards/architecture.md)).
- **Tenant isolation is never weakened.** New models have `TenantScopedModel`,
  `EnableTenantRLS` and `GuardTenantFKs` ([security](docs/standards/security.md)).
- Money and weights are **Decimal**. Ledger effects go only through `post_entry` and
  `reverse_entry` ([database](docs/standards/database.md)).
- Every API action declares its permission, and money-moving actions are `@idempotent`
  ([API](docs/standards/api.md)).
- Every user-facing string is translated ([coding](docs/standards/coding.md#numbers-text-and-time)).

## Tests

- Every change comes with tests that fail without it
  ([testing](docs/standards/testing.md#what-every-change-must-test)).
- A bug fix comes with a regression test. Run it once without the fix to see it fail.
- Run `ops\check.py` before you push. CI runs the same checks.

## Pull requests

1. **Keep it small.** One feature or fix, ideally under about 400 changed lines, migrations and
   generated files excluded. Split large work into steps that each leave `main` working.
2. **Write the description for the reviewer:**
   - What and why: link the requirement or register entry.
   - How to try it.
   - Risks: migrations on big tables, behaviour changes, anything irreversible, and the
     rollback.
   - Screenshots for UI changes (Arabic and English, light and dark if relevant).
   - The checklist from the [feature playbook](docs/development/feature-playbook.md#a-template-to-start-from),
     if it's a feature.
3. **CI must be green.** The `checks` and `assets` jobs are required. Read the advisory
   `dependencies` job too.
4. **Get one approving review**, two for changes to tenancy, permissions, identity, the
   ledger, migrations on large tables, or the job worker.
5. **Merge with "Squash and merge"** (subject = PR title in the commit style above) unless the
   commits are each meaningful. Delete the branch afterwards.

## Review checklist

Reviewers check that the code works, and that it fits the design and can be maintained by
someone else. Comment with the reason, and separate "must fix" from "suggestion".

- **Correctness:** does it meet the requirement and the acceptance examples? Cancel paths and
  edge cases (zero, last unit, foreign currency, closed month)?
- **Placement:** business rules in services; views and serializers thin; no copy of logic that
  exists elsewhere; dependencies point up the layers
  ([modules](docs/architecture/modules.md)).
- **Readability:** names from the domain, small functions, comments that say why.
- **Security:** tenant context, permission declared and enforced, branch scope, no
  string-built SQL, no unexplained `mark_safe`, nothing sensitive logged
  ([checklist](docs/standards/security.md#review-checklist)).
- **Data:** atomic, lock before check, constraints for invariants, migrations safe to deploy
  and reversible, `sqlmigrate` read for tables with data
  ([database](docs/standards/database.md)).
- **Tests:** they would fail without the change. They cover refusals and the cancel path. No
  English-text or this-year assertions.
- **API compatibility:** no removed or renamed fields, codes or status codes in `/api/v1/` or
  the mobile endpoints.
- **Performance:** no queries in loops; lists paginated; nothing slow inside a transaction.
- **Errors and logging:** `apps.core.errors` with specific codes; no swallowed exceptions; logs
  without personal data.
- **Documentation:** README / docs updated; ADR for decisions; register entry for anything left
  undone.

## Definition of Done

A change is done when every applicable item is true:

- [ ] It meets the agreed requirement and acceptance examples.
- [ ] It follows the [standards](docs/README.md#standards), or the exception is recorded in an
  ADR.
- [ ] Tests are added or updated, and `python ops/check.py` passes locally and in CI.
- [ ] Security and authorization are addressed: permission, branch scope, tenant isolation.
- [ ] Migrations are reviewed (SQL read, safe to deploy, rollback written).
- [ ] Translations are complete (the check passes).
- [ ] API contracts and the mobile endpoints are unchanged, or changed through a documented
  migration.
- [ ] Documentation is updated: README for features, `docs/` for rules and operations, and an
  ADR for decisions.
- [ ] Known limitations are in the [debt register](docs/tech-debt.md), not hidden.
- [ ] The pull request is approved and merged. It's deployable with the
  [release steps](docs/operations/README.md#releasing-a-new-version).

## Releases

Releases are commits on `main` that passed CI, deployed with the
[release steps](docs/operations/README.md#releasing-a-new-version). Tag each deployed commit
`vYYYY.MM.DD` (and `.2`, `.3` for a second release that day), so rollbacks have a name.
