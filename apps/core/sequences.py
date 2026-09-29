"""Document number allocation (§6.6). Call only from inside the posting transaction."""

from __future__ import annotations

from django.db import transaction

from .models import DocumentSequence


def format_number(*, branch_code: int | None, prefix: str, fiscal_year: int, value: int,
                  padding: int) -> str:
    body = f"{prefix}-{fiscal_year}-{value:0{padding}d}"
    return body if branch_code is None else f"{branch_code:02d}-{body}"


def _next(doc_type: str, *, branch, fiscal_year: int, prefix: str) -> tuple[int, DocumentSequence]:
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("Sequences must be allocated inside the caller's transaction.")

    scope = {"doc_type": doc_type, "branch": branch, "fiscal_year": fiscal_year}
    DocumentSequence.objects.bulk_create(
        [DocumentSequence(**scope, prefix=prefix)], ignore_conflicts=True
    )
    sequence = DocumentSequence.objects.select_for_update().get(**scope)

    value = sequence.next_value
    sequence.next_value = value + 1
    sequence.save(update_fields=["next_value", "updated_at"])
    return value, sequence


def allocate_value(counter: str, *, branch=None) -> int:
    """Next integer of a plain counter that does not restart yearly (e.g. customer codes)."""
    value, _ = _next(counter, branch=branch, fiscal_year=0, prefix="")
    return value


def allocate_number(doc_type: str, *, branch, fiscal_year: int, prefix: str | None = None) -> str:
    """Return the next number, e.g. "02-SI-2026-000123", and advance the counter.

    The row lock is held until the caller's transaction ends, so concurrent posts in the same
    branch/type serialize here. Lock sequences last (§16.2) to keep lock ordering consistent.
    """
    value, sequence = _next(doc_type, branch=branch, fiscal_year=fiscal_year,
                            prefix=prefix or doc_type)
    return format_number(
        branch_code=branch.code if branch is not None else None,
        prefix=sequence.prefix,
        fiscal_year=fiscal_year,
        value=value,
        padding=sequence.padding,
    )
