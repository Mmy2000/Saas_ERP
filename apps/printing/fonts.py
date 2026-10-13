"""Fonts a client uploads for its labels and printed documents (legacy GHSH14 font manager).

A font is checked by its content (TrueType, OpenType, WOFF or WOFF2), up to 5 MB, and kept with
the client's files. Labels and documents declare it with @font-face under a family name made from
its id, so the client's own text never reaches the CSS. The system fonts stay behind it, so
Arabic still shows when the chosen font has no Arabic letters.
"""

from __future__ import annotations

from django.db import IntegrityError, transaction
from django.utils.html import format_html
from django.utils.safestring import SafeString, mark_safe
from django.utils.translation import gettext as _

from apps.core.errors import NotFound, ValidationError
from apps.core.tenancy.context import require_current_tenant_id

from .models import PrintFont
from .services import MANAGE

MAX_FONT_BYTES = 5 * 1024 * 1024
SIGNATURES = (
    (b"\x00\x01\x00\x00", "truetype"),
    (b"true", "truetype"),
    (b"OTTO", "opentype"),
    (b"wOFF", "woff"),
    (b"wOF2", "woff2"),
)


def _error(field: str, message: str) -> ValidationError:
    return ValidationError(message, fields={field: [message]})


def font_format(upload) -> str | None:
    position = upload.tell() if hasattr(upload, "tell") else 0
    upload.seek(0)
    head = upload.read(4)
    upload.seek(position)
    return next((kind for magic, kind in SIGNATURES if head == magic), None)


def add_font(name: str, upload, *, actor=None) -> PrintFont:
    if actor is not None:
        actor.require(MANAGE)
    name = (name or "").strip()[:60]
    if upload is None:
        raise _error("file", _("Choose a font file."))
    if not name:
        name = upload.name.rsplit(".", 1)[0][:60] or "Font"
    if upload.size > MAX_FONT_BYTES:
        raise _error("file", _("The font must be 5 MB or smaller."))
    kind = font_format(upload)
    if kind is None:
        raise _error("file", _("Upload a TTF, OTF, WOFF or WOFF2 font."))
    if PrintFont.objects.filter(name__iexact=name).exists():
        raise _error("name", _("Already in the list."))
    try:
        with transaction.atomic():
            font = PrintFont(name=name, format=kind, tenant_id=require_current_tenant_id())
            font.file.save(upload.name, upload, save=False)
            font.save()
    except IntegrityError:
        raise _error("name", _("Already in the list.")) from None
    return font


def delete_font(font_id: int, *, actor=None) -> None:
    """Labels and documents using it go back to the system's font."""
    if actor is not None:
        actor.require(MANAGE)
    font = PrintFont.objects.filter(pk=font_id).first()
    if font is None:
        raise NotFound(_("Not found."))
    name = font.file.name
    font.delete()
    if name:
        font.file.storage.delete(name)


def font_face(font: PrintFont | None) -> SafeString:
    """The @font-face rule for `font` (nothing without one)."""
    if font is None or not font.file:
        return mark_safe("")  # noqa: S308 (empty)
    return format_html('@font-face {{ font-family: "{}"; src: url("{}") format("{}"); }}',
                       font.family, font.file.url, font.format)


def family(font: PrintFont | None) -> SafeString:
    """The family list to print with: the chosen font first, then the system's."""
    system = '"Inter", "Plex Arabic", system-ui, sans-serif'
    if font is None or not font.file:
        return mark_safe(system)  # noqa: S308 (constant)
    return format_html('"{}", {}', font.family, mark_safe(system))  # noqa: S308
