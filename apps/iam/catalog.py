"""The permission and limit catalog (§14.2), defined in code.

Each app declares its entries in `<app>/permissions.py`; IamConfig.ready() imports them all.
Role grants store permission codes and are validated against this registry, so a typo or a
removed permission can never be granted silently.
"""

from __future__ import annotations

from dataclasses import dataclass

WILDCARD = "*"  # every permission, present and future (Owner role only)
AUTHENTICATED = "authenticated"  # views any active member may use (e.g. /me)


@dataclass(frozen=True)
class PermissionDef:
    code: str
    label: str
    group: str


@dataclass(frozen=True)
class LimitDef:
    key: str
    label: str
    default: str  # Decimal as string; applied when a membership has no explicit value


_permissions: dict[str, PermissionDef] = {}
_limits: dict[str, LimitDef] = {}


def register_permissions(group: str, entries: list[tuple[str, str]]) -> None:
    for code, label in entries:
        if code in _permissions:
            raise ValueError(f"Permission {code} registered twice")
        _permissions[code] = PermissionDef(code=code, label=label, group=group)


def register_limit(key: str, label: str, default: str) -> None:
    if key in _limits:
        raise ValueError(f"Limit {key} registered twice")
    _limits[key] = LimitDef(key=key, label=label, default=default)


def permissions() -> dict[str, PermissionDef]:
    return dict(_permissions)


def limits() -> dict[str, LimitDef]:
    return dict(_limits)


def is_known_permission(code: str) -> bool:
    return code == WILDCARD or code in _permissions
