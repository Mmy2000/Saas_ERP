"""UI template helpers: icons, number formatting, navigation state."""

from __future__ import annotations

import json
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path

from django import template
from django.utils import formats
from django.utils.html import format_html
from django.utils.safestring import mark_safe

register = template.Library()

_ICONS_FILE = Path(__file__).resolve().parent.parent / "ui_icons.json"


@lru_cache(maxsize=1)
def _icons() -> dict[str, str]:
    return json.loads(_ICONS_FILE.read_text(encoding="utf-8"))


@register.simple_tag
def icon(name: str, css: str = "size-5", flip_rtl: bool = False) -> str:
    """Inline Lucide icon (bundled by `npm run fonts`). `flip_rtl` mirrors directional icons."""
    body = _icons()[name]  # KeyError on typos: fail loudly in tests
    classes = f"{css} shrink-0" + (" rtl:-scale-x-100" if flip_rtl else "")
    return format_html(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="1.75" stroke-linecap="round" '
        'stroke-linejoin="round" class="{}" aria-hidden="true">{}</svg>',
        classes, mark_safe(body),  # noqa: S308 - trusted, bundled at build time
    )


@register.filter
def num(value, places=2):
    """Grouped, fixed-decimal number in the active locale's format: 4,570.97. Empty → "—"."""
    if value is None or value == "":
        return "—"
    try:
        value = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return value
    # Round half-up here (§8.6); Django's formatter would otherwise round half-to-even.
    value = value.quantize(Decimal(1).scaleb(-int(places)), rounding=ROUND_HALF_UP)
    return formats.number_format(value, decimal_pos=int(places), use_l10n=True,
                                 force_grouping=True)


@register.filter
def percent(value, places=2):
    """A fraction as a percentage: 0.0225 → "2.25%". Trailing zeros dropped."""
    try:
        value = Decimal(str(value)) * 100
    except (InvalidOperation, ValueError):
        return value
    text = num(value, places)
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return f"{text}%"


@register.filter
def absolute(value):
    """|absolute: drop the sign (credits shown in their own column)."""
    try:
        return abs(Decimal(str(value)))
    except (InvalidOperation, ValueError):
        return value


@register.simple_tag(takes_context=True)
def nav_current(context, *url_names: str) -> str:
    """`aria-current="page"` when the current route is one of `url_names`."""
    match = getattr(context.get("request"), "resolver_match", None)
    if match is not None and match.view_name in url_names:
        return mark_safe(' aria-current="page"')
    return ""
