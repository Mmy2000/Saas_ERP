"""Uploaded files, kept apart per client.

Layout under MEDIA_ROOT:

    platform/branding/…            the platform's own logos: public on every host
    tenants/<tenant id>/branding/… a client's logo: public on that client's hosts only
    tenants/<tenant id>/<other>/…  a client's other files: its signed-in members only

Files are never served by a plain static route: `serve` checks the host and the user first,
so one client can never fetch another's files, even by guessing a name (names are random too).
The platform console (platform staff) can open any client's files.
"""

from __future__ import annotations

import mimetypes
import uuid
from pathlib import Path, PurePosixPath

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.http import FileResponse, Http404
from django.utils.deconstruct import deconstructible
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_GET

MAX_IMAGE_BYTES = 2 * 1024 * 1024
# Raster formats only: an SVG can carry scripts and would run on our own origin.
IMAGE_SIGNATURES = {
    b"\x89PNG\r\n\x1a\n": ".png",
    b"\xff\xd8\xff": ".jpg",
    b"GIF87a": ".gif",
    b"GIF89a": ".gif",
    b"\x00\x00\x01\x00": ".ico",
}


def _image_extension(head: bytes) -> str | None:
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    for signature, extension in IMAGE_SIGNATURES.items():
        if head.startswith(signature):
            return extension
    return None


def validate_image(upload) -> None:
    """PNG, JPEG, WEBP, GIF or ICO, at most 2 MB, checked by content (not by the name)."""
    if upload.size > MAX_IMAGE_BYTES:
        raise ValidationError(_("The image must be 2 MB or smaller."), code="too_large")
    position = upload.tell() if hasattr(upload, "tell") else 0
    upload.seek(0)
    head = upload.read(16)
    upload.seek(position)
    if _image_extension(head) is None:
        raise ValidationError(_("Upload a PNG, JPEG, WEBP, GIF or ICO image."), code="not_image")


class ImageUploadInput(forms.ClearableFileInput):
    """File picker for logos: images only, and a "remove the current logo" box."""

    template_name = "core/widgets/image_upload.html"


def _random_name(filename: str) -> str:
    suffix = PurePosixPath(filename).suffix.lower()[:8]
    return f"{uuid.uuid4().hex}{suffix}"


@deconstructible
class TenantUploadPath:
    """upload_to for tenant-scoped models: tenants/<tenant id>/<folder>/<random name>."""

    def __init__(self, folder: str):
        self.folder = folder

    def __call__(self, instance, filename: str) -> str:
        if not instance.tenant_id:
            raise ValueError("A tenant file needs its tenant before it is saved.")
        return f"tenants/{instance.tenant_id}/{self.folder}/{_random_name(filename)}"

    def __eq__(self, other):
        return isinstance(other, TenantUploadPath) and other.folder == self.folder


@deconstructible
class PlatformUploadPath:
    """upload_to for platform files: platform/<folder>/<random name>."""

    def __init__(self, folder: str):
        self.folder = folder

    def __call__(self, instance, filename: str) -> str:
        return f"platform/{self.folder}/{_random_name(filename)}"

    def __eq__(self, other):
        return isinstance(other, PlatformUploadPath) and other.folder == self.folder


def replace_file(instance, field: str, upload) -> None:
    """Set a FileField to `upload` (a file, or False to clear it) and delete the old file."""
    current = getattr(instance, field)
    old_name = current.name if current else ""
    if upload is False:
        setattr(instance, field, None)
    elif upload:
        getattr(instance, field).save(upload.name, upload, save=False)
    else:
        return
    if old_name and old_name != (getattr(instance, field).name or ""):
        default_storage.delete(old_name)


def _allowed(request, parts: tuple[str, ...]) -> tuple[bool, bool]:
    """(may see it, may a shared cache keep it)."""
    if parts[0] == "platform":
        return True, True
    if parts[0] != "tenants" or len(parts) < 4 or not parts[1].isdigit():
        return False, False
    tenant = getattr(request, "tenant", None)
    if tenant is None:  # platform host: staff only
        user = request.user
        return bool(user.is_authenticated and user.is_platform_staff), False
    if int(parts[1]) != tenant.id:
        return False, False
    if parts[2] == "branding":
        return True, False
    return getattr(request, "membership", None) is not None, False


@require_GET
def serve(request, path: str):
    parts = PurePosixPath(path).parts
    if not parts or any(part in ("..", ".") or part.startswith(".") for part in parts):
        raise Http404
    allowed, public = _allowed(request, parts)
    if not allowed:
        raise Http404  # never confirm that another client's file exists
    root = Path(settings.MEDIA_ROOT).resolve()
    target = (root / Path(*parts)).resolve()
    if root not in target.parents or not target.is_file():
        raise Http404
    content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    response = FileResponse(target.open("rb"), content_type=content_type)
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "default-src 'none'; sandbox"
    # Names are random and change on every upload, so the files can be cached for long.
    response["Cache-Control"] = f"{'public' if public else 'private'}, max-age=86400"
    return response
