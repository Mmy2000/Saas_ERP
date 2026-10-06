"""Rendering printed documents: a built-in layout or the client's custom HTML (console)."""

from __future__ import annotations

import logging

from django.http import HttpResponse
from django.template import Context, Engine
from django.template.loader import render_to_string
from django.utils.translation import gettext as _

from .documents import TYPES, Doc
from .models import DocLayout, DocumentDesign, Paper

logger = logging.getLogger(__name__)

# Custom HTML is rendered by its own engine: no template loaders (so no {% include %} or
# {% extends %} of server files), only the i18n and ui (num) tags, autoescaping on, and a
# context of plain data built by apps.printing.documents. It cannot reach models or queries.
CUSTOM_ENGINE = Engine(
    loaders=[], app_dirs=False, autoescape=True, string_if_invalid="",
    libraries={"ui": "apps.core.templatetags.ui", "i18n": "django.templatetags.i18n"},
)

PAPER_CSS = {
    Paper.A4: ("A4", "12mm", "auto"),
    Paper.A5: ("A5", "8mm", "auto"),
    Paper.ROLL80: ("80mm auto", "3mm", "74mm"),
}


def accent_hex(design: DocumentDesign, accent_key: str) -> str:
    if design.color:
        return design.color
    from apps.core.appearance import ACCENTS

    return dict((key, swatch) for key, _label, swatch in ACCENTS).get(accent_key, "#9a6122")


# What "following" another document's design copies: its look. Columns and options stay the
# document's own (they differ per type).
LOOK = ("layout", "paper", "lang", "color", "show_logo", "header_note", "footer_note", "terms",
        "copies", "blocks", "page", "custom_html", "use_custom")


def own_design(doc_type: str) -> DocumentDesign:
    """The design row of `doc_type` as stored (inside the tenant context), or a new one. A
    type without a row follows the sales invoice."""
    from .documents import DEFAULT_SOURCE

    design = DocumentDesign.objects.filter(doc_type=doc_type).first()
    if design is None:
        follows = "" if doc_type == DEFAULT_SOURCE else DEFAULT_SOURCE
        design = DocumentDesign(doc_type=doc_type, follows=follows)
    return design


def design_for(doc_type: str, *, _seen: frozenset = frozenset()) -> DocumentDesign:
    """The design `doc_type` prints with: its own, or the look of the type it follows (one
    after another, never in a loop)."""
    design = own_design(doc_type)
    source = design.follows
    loop = source in _seen or len(_seen) >= 5
    if source and source != doc_type and source in TYPES and not loop:
        look = design_for(source, _seen=_seen | {doc_type})
        for name in LOOK:
            setattr(design, name, getattr(look, name))
    return design


def effective(design: DocumentDesign) -> tuple[str, str]:
    """(layout, paper): the thermal layout always prints on a roll, and a roll gets the thermal
    layout unless the client designs its own (the designer works on any paper)."""
    if design.layout == DocLayout.BUILDER:
        return DocLayout.BUILDER, design.paper
    if design.layout == DocLayout.THERMAL or design.paper == Paper.ROLL80:
        return DocLayout.THERMAL, Paper.ROLL80
    return design.layout, design.paper


def _context(doc: Doc, design: DocumentDesign, accent_key: str) -> dict:
    layout, paper = effective(design)
    size, margin, width = PAPER_CSS[paper]
    lang = "ar" if design.language == "both" else design.language
    doc.lang, doc.dir = lang, "rtl" if lang == "ar" else "ltr"
    color = accent_hex(design, accent_key)
    page_css = {"size": size, "margin": margin, "width": width}
    if layout == DocLayout.BUILDER:
        from .builder import clean_page

        page = clean_page(design.page)
        color = page["accent"] or color
        page_css.update(margin=f"{page['mt']}mm {page['mr']}mm {page['mb']}mm {page['ml']}mm",
                        font=page["size"], ink=page["color"], bg=page["bg"])
    return {
        "doc": doc,
        "design": {
            "layout": layout, "paper": paper, "color": color,
            "show_logo": design.show_logo and bool(doc.company.get("logo")),
            "header_note": design.header_note, "footer_note": design.footer_note,
            "terms": design.terms, "copies": range(max(1, min(design.copies or 1, 5))),
            "signatures": design.option(design.doc_type, "show_signatures")
            if design.doc_type in TYPES else False,
        },
        "page": page_css,
    }


def render_custom(html: str, context: dict) -> str:
    """Raises (TemplateSyntaxError, …) on a broken template."""
    return CUSTOM_ENGINE.from_string(html).render(Context(context, autoescape=True))


def render_document(request, doc: Doc, design: DocumentDesign, *, accent_key: str,
                    toolbar: bool = True, custom_html: str | None = None,
                    use_custom: bool | None = None, designer: bool = False) -> HttpResponse:
    """The printable page. `custom_html`/`use_custom` override the saved design (previews)."""
    return HttpResponse(document_html(request, doc, design, accent_key=accent_key,
                                      toolbar=toolbar, custom_html=custom_html,
                                      use_custom=use_custom, designer=designer))


def document_html(request, doc: Doc, design: DocumentDesign, *, accent_key: str,
                  toolbar: bool = True, custom_html: str | None = None,
                  use_custom: bool | None = None, designer: bool = False,
                  for_pdf: bool = False) -> str:
    context = _context(doc, design, accent_key)
    html = design.custom_html if custom_html is None else custom_html
    wants_custom = design.use_custom if use_custom is None else use_custom
    body, error = "", ""
    if wants_custom and html.strip():
        try:
            one = render_custom(html, context)
            copies = context["design"]["copies"]
            body = "".join(f'<div class="sheet">{one}</div>' for _copy in copies)
        except Exception as exc:  # a broken custom design must not stop the shop printing
            error = str(exc)
            logger.warning("custom document template failed: %s", exc)
    builder_body = ""
    if not body and context["design"]["layout"] == DocLayout.BUILDER:
        from .builder import clean_blocks, preset, render_blocks

        blocks = clean_blocks(design.blocks) or preset("classic")
        builder_body = render_blocks(blocks, context, designer=designer)
    return render_to_string("printing/documents/page.html", {
        **context, "custom_body": body, "custom_error": error,
        "toolbar": toolbar and not for_pdf, "for_pdf": for_pdf,
        "builder_body": builder_body, "designer": designer and not for_pdf,
        "auto_print": (request is not None and request.GET.get("print") == "1"
                       and not for_pdf),
    }, request=request)


def document_pdf(request, doc: Doc, design: DocumentDesign, *, accent_key: str) -> bytes:
    """The document as a PDF, from the same page the shop prints (see apps.printing.pdf)."""
    from . import pdf

    _layout, paper = effective(design)
    _inline_logo(doc)
    html = document_html(request, doc, design, accent_key=accent_key, for_pdf=True)
    return pdf.html_to_pdf(html, pdf.Paper(roll=True) if paper == Paper.ROLL80 else pdf.A4)


def record_pdf(doc_type: str, record) -> tuple[bytes, str]:
    """(PDF, file name) of one record in the client's design, with no request (background
    jobs, shared links). Costs are never shown: these go to customers."""
    from apps.org.models import TenantProfile

    design = design_for(doc_type)
    doc = TYPES[doc_type].builder(record, design, {"can_cost": False})
    profile = TenantProfile.objects.values_list("accent", flat=True).first() or ""
    content = document_pdf(None, doc, design, accent_key=profile)
    return content, document_filename(doc)


def document_filename(doc: Doc) -> str:
    name = " ".join(part for part in (str(doc.title), doc.number) if part) or "document"
    return f"{name}.pdf"


def _inline_logo(doc: Doc) -> None:
    """A logo kept outside this server (cloud storage) goes into the page itself: the PDF
    renderer fetches nothing from outside."""
    if (doc.company or {}).get("logo", "").startswith(("http://", "https://")):
        from apps.org.models import TenantProfile

        from .pdf import logo_src

        doc.company["logo"] = logo_src(TenantProfile.objects.first())


def no_document_type(doc_type: str) -> str:
    return _("Unknown document type: %(type)s") % {"type": doc_type}
