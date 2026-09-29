from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.clickjacking import xframe_options_sameorigin

from apps.core.errors import ValidationError
from apps.iam.authz import permission_required
from apps.inventory.models import Item, StockTransferLine
from apps.org.models import TenantProfile
from apps.purchasing.models import SupplierInvoicePiece

from .labels import FIELDS, build_label, ensure_default_templates, paginate, zpl
from .models import LabelTemplate, Layout, Media
from .services import MANAGE, template_from

MAX_LABELS = 500


def _int(value, default: int, low: int, high: int) -> int:
    try:
        return min(max(int(value), low), high)
    except (TypeError, ValueError):
        return default


def _items(request) -> list[Item]:
    """Pieces to label: ?item=1&item=2, ?invoice=<purchase>, ?transfer=<stock transfer>."""
    ids = {int(v) for v in request.GET.getlist("item") if v.isdigit()}
    invoice, transfer = request.GET.get("invoice", ""), request.GET.get("transfer", "")
    if invoice.isdigit():
        ids |= set(SupplierInvoicePiece.objects.filter(line__invoice_id=int(invoice),
                                                       item__isnull=False)
                   .values_list("item_id", flat=True))
    if transfer.isdigit():
        ids |= set(StockTransferLine.objects.filter(transfer_id=int(transfer),
                                                    item__isnull=False)
                   .values_list("item_id", flat=True))
    items = Item.objects.select_related("category", "karat__metal", "branch").filter(pk__in=ids)
    scope = request.actor.branch_ids("inventory.stock.view")
    if scope is not None:
        items = items.filter(branch_id__in=scope)
    return list(items.order_by("barcode")[:MAX_LABELS])


def _template(request) -> LabelTemplate:
    ensure_default_templates(getattr(TenantProfile.objects.first(), "locale", "ar"))
    templates = LabelTemplate.objects.filter(is_active=True)
    chosen = request.GET.get("template", "")
    template = templates.filter(pk=int(chosen)).first() if chosen.isdigit() else None
    return template or templates.filter(is_default=True).first() or templates.first()


@permission_required("inventory.item.print_label")
def labels(request):
    template = _template(request)
    items = _items(request)
    copies = _int(request.GET.get("copies"), 1, 1, 20)
    skip = _int(request.GET.get("skip"), 0, 0, max(template.per_page - 1, 0))
    shop = TenantProfile.objects.first().display_name
    built = [build_label(template, item, shop) for item in items for _copy in range(copies)]
    query = request.GET.copy()
    query.pop("template", None)
    return render(request, "printing/labels.html", {
        "template": template, "templates": LabelTemplate.objects.filter(is_active=True),
        "pages": paginate(template, built, skip=skip), "items": items, "copies": copies,
        "skip": skip, "query": query.urlencode(), "count": len(built),
        "back": request.META.get("HTTP_REFERER", reverse("stock")),
    })


@permission_required("inventory.item.print_label")
def labels_zpl(request):
    template = _template(request)
    items = _items(request)
    if not items:
        raise Http404
    shop = TenantProfile.objects.first().display_name
    response = HttpResponse(zpl(template, items, copies=_int(request.GET.get("copies"), 1, 1, 20),
                                shop=shop), content_type="text/plain; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="labels.zpl"'
    return response


# --- templates --------------------------------------------------------------------------------

@permission_required(MANAGE)
def label_templates(request):
    ensure_default_templates(getattr(TenantProfile.objects.first(), "locale", "ar"))
    return render(request, "printing/templates.html", {
        "templates": LabelTemplate.objects.all()})


def _options(chosen: list[str]) -> list[tuple[str, str, bool]]:
    """Chosen fields first, in their print order, then the rest."""
    return ([(key, FIELDS[key], True) for key in chosen if key in FIELDS]
            + [(key, label, False) for key, label in FIELDS.items() if key not in chosen])


def _form(request, template=None):
    shown = template or LabelTemplate(width_mm=50, height_mm=25, fields=["barcode", "code"])
    return render(request, "printing/template_form.html", {
        "template": shown, "options_a": _options(shown.fields),
        "options_b": _options(shown.fields_b),
        "is_new": template is None, "medias": Media.choices,
        "layouts": Layout.choices,
        "endpoint": (reverse("label-template-detail", args=[template.pk]) if template
                     else reverse("label-template-list")),
        "method": "PUT" if template else "POST",
    })


@permission_required(MANAGE)
def label_template_new(request):
    return _form(request)


@permission_required(MANAGE)
def label_template_edit(request, pk):
    template = LabelTemplate.objects.filter(pk=pk).first()
    if template is None:
        raise Http404
    return _form(request, template)


class _Sample:
    """A made-up piece for previews when the shop has no stock yet."""

    barcode = "1010000123"
    karat_id = None
    karat = None
    gross_weight_g = 4.25
    list_making_rate = 250
    stone_weight_ct = 0.12
    label_price = None

    class category:  # noqa: N801
        name = "—"

    class branch:  # noqa: N801
        name = "—"


@xframe_options_sameorigin  # shown in the template form's frame
@permission_required(MANAGE)
def label_preview(request):
    """One label drawn from the (unsaved) form values, for the template form's preview."""
    data = request.GET.dict()
    data["fields"] = request.GET.getlist("fields")
    data["fields_b"] = request.GET.getlist("fields_b")
    data.setdefault("name", "preview")
    try:
        template = template_from(data)
        problem = ""
    except ValidationError as exc:
        template, problem = None, exc.message
        if exc.fields:
            problem = " ".join(" ".join(v) for v in exc.fields.values())
    item = (Item.objects.select_related("category", "karat__metal", "branch")
            .filter(status="in_stock").order_by("-id").first() or _Sample())
    shop = TenantProfile.objects.first().display_name
    return render(request, "printing/preview.html", {
        "template": template, "problem": problem or (_("Nothing to show.") if not template else ""),
        "pages": paginate(template, [build_label(template, item, shop)]) if template else [],
    })
