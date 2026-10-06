"""Document designs inside the workspace (Settings → Documents) and printing any document."""

from __future__ import annotations

import copy

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.iam.authz import permission_required

from .design_forms import DesignForm
from .documents import TYPES, _company, grouped_types
from .render import LOOK, design_for, own_design, render_document


def _accent(request) -> str:
    from apps.core.appearance import _workspace_accent

    return _workspace_accent(request)


def _accent_hex(request) -> str:
    from .models import DocumentDesign
    from .render import accent_hex

    return accent_hex(DocumentDesign(), _accent(request))


def _kind(doc_type: str):
    if doc_type not in TYPES:
        raise Http404
    return TYPES[doc_type]


def with_look(design):
    """An unsaved design as it would print: if it follows another type, with that one's look."""
    if not design.follows or design.follows not in TYPES:
        return design
    shown = copy.copy(design)
    look = design_for(design.follows, _seen=frozenset({design.doc_type}))
    for name in LOOK:
        setattr(shown, name, getattr(look, name))
    return shown


def sample_doc(doc_type: str, design):
    from apps.org.models import TenantProfile

    company = _company(TenantProfile.objects.first(), _("Main branch"))
    return TYPES[doc_type].sampler(design, company)


def design_rows() -> list[tuple[str, list[dict]]]:
    """Every document type, grouped, with its stored design and what it prints with."""
    return [(group, [{"kind": kind, "own": own_design(kind.key), "shown": design_for(kind.key)}
                     for kind in kinds]) for group, kinds in grouped_types()]


@permission_required("org.settings.manage")
def designs(request):
    return render(request, "printing/documents/designs.html", {"groups": design_rows()})


@permission_required("org.settings.manage")
def design_edit(request, doc_type):
    kind = _kind(doc_type)
    design = own_design(doc_type)
    form = DesignForm(request.POST or None, instance=design, doc_type=doc_type,
                      accent=_accent_hex(request))
    if request.method == "POST" and form.is_valid():
        form.apply().save()
        messages.success(request, _("Saved."))
        return redirect("document-design", doc_type)
    return render(request, "printing/documents/design_form.html", {
        "kind": kind, "form": form, "design": design, "shown": design_for(doc_type),
        "preview_url": request.path + "preview/"})


@permission_required("org.settings.manage")
@require_POST
def design_preview(request, doc_type):
    """The sample document with the unsaved form values (live preview)."""
    _kind(doc_type)
    form = DesignForm(request.POST, instance=own_design(doc_type), doc_type=doc_type,
                      accent=_accent_hex(request))
    design = with_look(form.apply() if form.is_valid() else form.instance)
    return render_document(request, sample_doc(doc_type, design), design,
                           accent_key=_accent(request), toolbar=False)


def print_document(request, doc_type, pk, *, as_pdf=False):
    """Any document in this client's design. The same permission and scoping as the
    document's own page, so nobody prints what they could not open."""
    kind = _kind(doc_type)
    actor = getattr(request, "actor", None)
    if actor is None or not request.user.is_authenticated:
        from django.contrib.auth.views import redirect_to_login

        return redirect_to_login(request.get_full_path())

    @permission_required(kind.permission)
    def view(request):
        record = kind.finder(request).filter(pk=pk).first()
        if record is None:
            raise Http404
        design = design_for(doc_type)
        ctx = {"can_cost": request.actor.can("inventory.item.view_cost")}
        doc = kind.builder(record, design, ctx)
        if as_pdf:
            return _pdf(request, doc, design)
        return render_document(request, doc, design, accent_key=_accent(request))

    return view(request)


def print_document_pdf(request, doc_type, pk):
    """The same document as a PDF file (to download, e-mail or send on WhatsApp)."""
    return print_document(request, doc_type, pk, as_pdf=True)


def _pdf(request, doc, design):
    from .pdf import PdfUnavailable, pdf_response
    from .render import document_filename, document_pdf

    try:
        content = document_pdf(request, doc, design, accent_key=_accent(request))
    except PdfUnavailable:
        return render(request, "printing/pdf_unavailable.html", status=503)
    return pdf_response(content, document_filename(doc),
                        inline=request.GET.get("inline") == "1")


def print_sales_invoice(request, invoice):
    design = design_for("sales_invoice")
    doc = TYPES["sales_invoice"].builder(invoice, design, {})
    return render_document(request, doc, design, accent_key=_accent(request))
