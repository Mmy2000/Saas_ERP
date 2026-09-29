"""Where to open a document, and what to call it, given the (source_type, source_id) stored on
ledger entries and stock movements. Apps register their document types in AppConfig.ready()."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from django.apps import apps
from django.urls import NoReverseMatch, reverse


@dataclass(frozen=True)
class DocumentType:
    url_name: str
    label: Callable[[object], str] | str | None = None  # text, or computed from the document


_TYPES: dict[str, DocumentType] = {}


def register_document(source_type: str, url_name: str, label=None) -> None:
    _TYPES[source_type] = DocumentType(url_name, label)


def document_url(source_type: str, source_id) -> str | None:
    doc_type = _TYPES.get(source_type)
    if doc_type is None or source_id is None:
        return None
    try:
        return reverse(doc_type.url_name, args=[source_id])
    except NoReverseMatch:
        return None


def document_titles(refs: Iterable[tuple[str, int]]) -> dict[tuple[str, int], tuple[str, str]]:
    """(label, number) of each registered document, with one query per document type.
    Labels are rendered now, in the active language, not when the document was posted."""
    wanted: dict[str, set[int]] = defaultdict(set)
    for source_type, source_id in refs:
        doc_type = _TYPES.get(source_type)
        if doc_type is not None and doc_type.label is not None and source_id is not None:
            wanted[source_type].add(source_id)
    titles = {}
    for source_type, ids in wanted.items():
        label = _TYPES[source_type].label
        for doc in apps.get_model(source_type).objects.filter(pk__in=ids):
            text = label(doc) if callable(label) else str(label)
            titles[(source_type, doc.pk)] = (text, doc.number or "")
    return titles
