"""Characterization snapshots: record what the code produces today, then prove that a refactor
changes nothing (docs/standards/testing.md).

    assert_snapshots(PATH, {"cash sale": quote, "unknown piece": error, ...})

The values are made plain (Decimals as strings, dataclasses as dicts, model rows by code) and
compared with the JSON file at PATH. Database ids and anything holding today's year are left
out: they differ from run to run. To record the file (only when a change in the output is
intended; review the JSON diff in the pull request):

    RECORD_SNAPSHOTS=1 python -m pytest <the test>
"""

from __future__ import annotations

import dataclasses
import json
import os
from decimal import Decimal
from pathlib import Path

from django.db import models

from apps.core.errors import AppError

# Database ids: a fresh test database numbers rows differently from run to run.
IGNORED_FIELDS = {"id", "pk", "item_id", "lot_id", "karat_id", "offer_id", "cashback_offer_id"}


def _row(row: models.Model) -> str:
    """A model row by what identifies it to people (never its id or a dated document number)."""
    for attribute in ("barcode", "code", "name"):
        value = getattr(row, attribute, None)
        if value not in (None, ""):
            return f"<{type(row).__name__} {value}>"
    return f"<{type(row).__name__}>"


def plain(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, AppError):
        return {"error": type(value).__name__, "code": value.code, "message": value.message,
                "fields": plain(value.fields)}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: plain(getattr(value, f.name)) for f in dataclasses.fields(value)
                if f.name not in IGNORED_FIELDS}
    if isinstance(value, models.Model):
        return _row(value)
    if isinstance(value, dict):
        return {str(plain(k)) if not isinstance(k, str) else k: plain(v)
                for k, v in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted(plain(v) for v in value)
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return repr(value)


def assert_snapshots(path: Path, values: dict) -> None:
    current = {name: plain(value) for name, value in values.items()}
    if os.environ.get("RECORD_SNAPSHOTS") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(current, indent=1, ensure_ascii=False, sort_keys=True) + "\n",
                        encoding="utf-8")
        return
    assert path.exists(), f"No snapshot yet: record it with RECORD_SNAPSHOTS=1 ({path})"
    recorded = json.loads(path.read_text(encoding="utf-8"))
    assert sorted(current) == sorted(recorded), "The scenarios differ from the recorded ones."
    changed = [name for name in recorded if current[name] != recorded[name]]
    details = "\n".join(
        f"--- {name}\nrecorded: {json.dumps(recorded[name], ensure_ascii=False)[:1500]}\n"
        f"now:      {json.dumps(current[name], ensure_ascii=False)[:1500]}" for name in changed)
    assert not changed, f"{len(changed)} scenario(s) changed:\n{details}"
