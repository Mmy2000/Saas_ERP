# ADR-019: Enforce lint, not an auto-formatter

- Status: Accepted
- Date: 2026-10-24
- Deciders: project owner (Mahmoud), during the engineering standards pass

## Context

The project is moving from one developer to a team, so the code style must stay consistent
without depending on memory.

- `ruff check` is configured (`pyproject.toml`: `E F W I B UP DJ S608`, line length 100) and
  the whole repository passes it.
- The code has a consistent hand style: continuation lines aligned with the bracket, related
  short statements on one line, section comments.
- `ruff format --check` reports that **540 of 799 files** would be rewritten.

## Decision

- Enforce `ruff check` (lint and import order) in CI and in `ops/check.py`.
- Don't adopt an auto-formatter now.
- Document the house style in [coding conventions](../standards/coding.md#formatting-and-lint),
  and check it in review.

## Alternatives considered

- **Adopt `ruff format` with one reformatting commit**, added to `.git-blame-ignore-revs`.
  - For: no style discussion in review, ever.
  - Against: a 540-file diff, conflicts with every open branch, and lost readability in places
    where the hand alignment carries meaning.
  - This remains a reasonable later step, once the team is larger and no long-lived branches
    are open.
- **No enforcement at all.** Rejected: lint already catches real bugs (`B`, `F`) and keeps
  imports sorted.

## Consequences

- No mass diff, and `git blame` stays useful.
- Formatting comments in review stay possible. Reviewers should keep them light and point to
  the conventions page.
- Revisit when the team agrees; the change is mechanical.
