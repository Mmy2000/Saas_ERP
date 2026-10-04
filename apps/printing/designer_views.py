"""The drag-and-drop document designer: page, live preview and save. Shared by the workspace
(Settings → Documents, the client) and the console (platform staff, inside the client's
tenant context). Everything posted is cleaned by apps.printing.builder before use."""

from __future__ import annotations

import json

from django.http import Http404, HttpResponseBadRequest, JsonResponse
from django.shortcuts import render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.iam.authz import permission_required

from .builder import PRESETS, clean_blocks, clean_page, preset, schema
from .documents import TYPES, _company
from .models import DocLanguage, DocLayout, Paper
from .render import design_for, own_design, render_document

MAX_BODY = 256 * 1024


def _payload(request) -> dict:
    if len(request.body) > MAX_BODY:
        raise ValueError("too large")
    data = json.loads(request.body or b"{}")
    if not isinstance(data, dict):
        raise ValueError("not an object")
    return data


def _apply(design, data: dict):
    design.blocks = clean_blocks(data.get("blocks"))
    design.page = clean_page(data.get("page"))
    if data.get("paper") in Paper.values:
        design.paper = data["paper"]
    if data.get("lang") in DocLanguage.values:
        design.lang = data["lang"]
    design.layout = DocLayout.BUILDER
    design.follows = ""  # designing a document gives it its own design
    design.use_custom = False
    return design


def ui_strings() -> dict:
    return {
        "blocks": _("Blocks"), "layers": _("Layers"), "page": _("Page"),
        "properties": _("Properties"), "style": _("Style"), "empty": _("Drop blocks here"),
        "nothing": _("Click a block in the page or in Layers to change it."),
        "duplicate": _("Duplicate"), "delete": _("Delete"), "up": _("Move up"),
        "down": _("Move down"), "column": _("Column %(n)s"), "saved": _("Saved."),
        "failed": _("Could not save. Check your connection and try again."),
        "unsaved": _("Unsaved changes"), "confirm_preset": _(
            "Replace the whole design with this starting point?"),
        "inherit": _("Inherit"), "clear": _("Clear"),
    }


UI_ICONS = ("grip-vertical", "copy", "trash-2", "arrow-up", "arrow-down", "plus",
            "chevron-down", "mouse-pointer-click")


def _icons() -> dict[str, str]:
    """SVG bodies for the editor (block icons and its own buttons)."""
    from apps.core.templatetags.ui import _icons as bundle

    from .builder import BLOCKS

    names = {*UI_ICONS, *(b["icon"] for b in BLOCKS.values())}
    icons = bundle()
    return {name: icons[name] for name in names if name in icons}


def designer_page(request, doc_type: str, *, urls: dict, accent_key: str, title: str,
                  back_label: str):
    if doc_type not in TYPES:
        raise Http404
    design = design_for(doc_type)
    blocks = clean_blocks(design.blocks) or preset(
        "thermal" if design.paper == Paper.ROLL80 else "classic")
    state = {"blocks": blocks, "page": clean_page(design.page), "paper": design.paper,
             "lang": design.lang}
    return render(request, "printing/documents/designer.html", {
        "kind": TYPES[doc_type], "title": title, "back_label": back_label, "urls": urls,
        "designer_data": {
            "schema": schema(), "state": state, "urls": urls, "strings": ui_strings(),
            "presets": {name: preset(name) for name in PRESETS},
            "papers": [[v, str(label)] for v, label in Paper.choices],
            "languages": [[v, str(label)] for v, label in DocLanguage.choices],
            "icons": _icons(),
        },
    })


def designer_preview(request, doc_type: str, *, accent_key: str):
    if doc_type not in TYPES:
        raise Http404
    try:
        data = _payload(request)
    except (ValueError, json.JSONDecodeError):
        return HttpResponseBadRequest()
    from apps.org.models import TenantProfile

    design = _apply(own_design(doc_type), data)
    doc = TYPES[doc_type].sampler(design, _company(TenantProfile.objects.first(),
                                                   _("Main branch")))
    return render_document(request, doc, design, accent_key=accent_key, toolbar=False,
                           use_custom=False, designer=True)


def designer_save(request, doc_type: str):
    if doc_type not in TYPES:
        raise Http404
    try:
        data = _payload(request)
    except (ValueError, json.JSONDecodeError):
        return HttpResponseBadRequest()
    design = _apply(own_design(doc_type), data)
    design.save()
    return JsonResponse({"ok": True, "blocks": design.blocks, "page": design.page,
                         "message": _("Saved.")})


# ---- workspace (the client) ----

def _workspace_accent(request) -> str:
    from apps.core.appearance import _workspace_accent as accent

    return accent(request)


@permission_required("org.settings.manage")
def designer(request, doc_type):
    from django.urls import reverse

    return designer_page(request, doc_type, accent_key=_workspace_accent(request), urls={
        "preview": reverse("document-designer-preview", args=[doc_type]),
        "save": reverse("document-designer-save", args=[doc_type]),
        "back": reverse("document-design", args=[doc_type]),
    }, title=str(TYPES[doc_type].label) if doc_type in TYPES else "",
        back_label=_("Documents"))


@permission_required("org.settings.manage")
@require_POST
def preview(request, doc_type):
    return designer_preview(request, doc_type, accent_key=_workspace_accent(request))


@permission_required("org.settings.manage")
@require_POST
def save(request, doc_type):
    return designer_save(request, doc_type)
