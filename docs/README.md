# Documentation

Start here if you're new to the project.

## Reading order for a new developer

1. [README](../README.md): what the product does, and local setup.
2. [Architecture overview](architecture/overview.md): hosts, tenant isolation, a request from
   start to finish, the ledger, permissions, jobs, the glossary.
3. [Modules](architecture/modules.md): the 26 apps, their layers, and the registries you extend.
4. [Architecture standards](standards/architecture.md): where code goes, and the patterns in use.
5. [Feature playbook](development/feature-playbook.md): one real feature, file by file.
6. [CONTRIBUTING](../CONTRIBUTING.md): branches, pull requests, review, Definition of Done.

## Standards

| Page | Covers |
|---|---|
| [Coding conventions](standards/coding.md) | formatting, naming, functions, numbers and text, comments, errors, types, dead code |
| [Architecture standards](standards/architecture.md) | where code goes, app layout, dependency direction, registries, patterns, SOLID, frontend |
| [API standards](standards/api.md) | versioning, permissions, requests, idempotency, responses, the error envelope, a new-endpoint checklist |
| [Database and data integrity](standards/database.md) | models, append-only tables, isolation migrations, transactions and locks, queries, migrations, test data |
| [Testing strategy](standards/testing.md) | commands, kinds of test, what every change must test, conventions, concurrency |
| [Security](standards/security.md) | isolation, authentication, authorization, input and output, files, secrets, dependencies, review checklist |
| [Errors and logging](standards/errors-and-logging.md) | which error to raise, catching, log levels, Console → Errors, jobs |
| [Performance](standards/performance.md) | queries, pagination, indexes, transactions, where time goes |
| [Documentation](standards/documentation.md) | where each kind of fact is written, and how |

## Development

- [Feature playbook](development/feature-playbook.md): requirement to merged pull request, with
  a checklist template.
- [Common tasks](development/common-tasks.md):
  - models
  - business rules
  - endpoints
  - permissions
  - pages
  - icons
  - jobs
  - external services
  - reports
  - management commands
  - configuration
  - translations
  - debugging

## Operations

- [Operations runbook](operations/README.md): configuration, processes, releases, rollback,
  monitoring, backups, incidents, missing safeguards.

## Decisions and state

- [Decisions (ADRs)](decisions/README.md): ADR-019 onwards here. ADR-001 to ADR-018 are in
  `PROJECT_PLAN.md`.
- [Technical debt register](tech-debt.md): known problems, prioritised.
- [Engineering audit, October 2026](audit/2026-10-engineering-audit.md), and its
  [verification report](audit/verification-2026-10.md).
