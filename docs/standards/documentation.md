# Documentation conventions

Documentation is part of the change. A pull request that changes behaviour, setup or a rule
updates the page that describes it, in the same pull request.

## Where things are written

| What | Where |
|---|---|
| What the product does, setup, production, features per area | `README.md` |
| How to contribute, review, Definition of Done | `CONTRIBUTING.md` |
| The system's design and module map | `docs/architecture/` |
| The rules for writing code | `docs/standards/` |
| How to do common tasks | `docs/development/` |
| Running it in production | `docs/operations/` |
| Why a decision was made | `docs/decisions/` (ADRs) and `PROJECT_PLAN.md` (§ sections, ADR-001 to ADR-018) |
| Known problems | `docs/tech-debt.md` |
| Why a piece of code is the way it is | a comment or docstring next to it |

Put each fact in one place, and link to it from everywhere else. If a fact lives in the code
(a list of settings, a registry), document where to find it rather than copying it, unless the
copy is checked by a test.

## Writing

- Write for a developer who is new to the project but not to Django. Use the business's words
  (see the [glossary](../architecture/overview.md#glossary)) and explain them once.
- State facts the code shows. Cite `path/to/file.py` (and `:line` when it helps). Don't
  describe intended behaviour as if it existed: an unbuilt piece is "not built yet" with a
  register entry.
- Prefer short pages with tables and examples taken from the real code over long prose.
- Diagrams: Mermaid in the Markdown (GitHub renders it), only when relationships are easier to
  see than to read.
- English. Arabic belongs in the UI translations, not the docs.

## Code comments and docstrings

See [coding conventions](coding.md#comments-and-docstrings). In short:

- Module and function docstrings say what and the non-obvious rules.
- Comments say why.
- `§n.n` cites `PROJECT_PLAN.md`, `ADR-nnn` cites a decision, and `TD-nn` cites a known
  problem.

## ADRs

See [decisions](../decisions/README.md) for when to write one and the template.

## Keeping it true

- Reviewers check that the docs touched by a change were updated ([CONTRIBUTING](../../CONTRIBUTING.md#review-checklist)).
- When you find a page that is wrong, fix it in your pull request, or open a register entry if
  it's large.
