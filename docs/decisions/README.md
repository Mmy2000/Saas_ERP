# Architecture decision records

An ADR records a decision that is hard to reverse or that everyone must follow: the problem,
what was decided, what else was considered, and the consequences. ADRs exist so the next
developer doesn't undo a deliberate choice without knowing why it was made.

## Where the ADRs are

- **ADR-001 to ADR-018** are in `PROJECT_PLAN.md`, the design document. The code cites them:
  ADR-002 (RLS), ADR-004 (identity and memberships), ADR-005 (ledger), ADR-006 (parties),
  ADR-007 (one item per piece), ADR-008, ADR-009 (karat conversion), ADR-010 (pricing), ADR-016
  (Django 5.2 LTS), ADR-018, and others.
  - That file is being added to the repository. If it holds ADRs numbered 019 or higher,
    renumber the ones below so numbers stay unique.
- **ADR-019 onwards** are files in this folder.

| ADR | Title | Status | Date |
|---|---|---|---|
| [ADR-019](ADR-019-lint-without-formatter.md) | Enforce lint, not an auto-formatter | Accepted | 2026-10-24 |
| [ADR-020](ADR-020-quality-gates.md) | Quality gates: one local command, the same in CI | Accepted | 2026-10-24 |
| [ADR-021](ADR-021-platform-tasks-outside-a-transaction.md) | Platform tasks may run outside a transaction | Accepted | 2026-10-24 |
| [ADR-022](ADR-022-identity-across-workspaces.md) | Who may change a user who belongs to several workspaces | Accepted | 2026-10-25 |

## When to write one

Write an ADR for any of these:

- A change to tenancy, permissions, identity, money, ledger or stock rules.
- A new dependency, process or external service.
- A new pattern or registry.
- A change to an API contract or a stored format.
- A deliberate exception to a standard.

You don't need one for routine features that follow the standards.

## How

1. Copy the template below to `ADR-<next number>-<short-name>.md`, with status **Proposed**.
2. Open it in the same pull request as the code that needs it, or alone if the decision comes
   first. Reviewers discuss the ADR there.
3. When merged, set the status to **Accepted** and add it to the table above.
4. Never edit an accepted ADR's decision. To change it, write a new ADR and set the old one to
   **Superseded by ADR-nnn**.

## Template

```markdown
# ADR-nnn: <decision in a few words>

- Status: Proposed | Accepted | Superseded by ADR-nnn | Rejected
- Date: YYYY-MM-DD
- Deciders: <names or roles>

## Context
The problem and the forces at play, with facts (files, numbers, incidents).

## Decision
What we will do, stated so it can be followed and checked.

## Alternatives considered
Each option, and why it wasn't chosen.

## Consequences
What gets better, what gets worse, what must now be done or watched.
```
