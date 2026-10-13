"""Turning pieces into labels: which fields exist, what each prints, HTML parts and ZPL."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from django.utils import translation
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from apps.core.templatetags.ui import num

from .barcode import code128
from .models import LabelTemplate, Layout, Media

# key → name shown when designing a template, in display order.
FIELDS = {
    "barcode": gettext_lazy("Barcode (bars)"),
    "code": gettext_lazy("Barcode number"),
    "shop": gettext_lazy("Shop name"),
    "category": gettext_lazy("Category"),
    "karat": gettext_lazy("Karat"),
    "weight": gettext_lazy("Weight"),
    "making": gettext_lazy("Making charge per gram"),
    "stones": gettext_lazy("Stones (ct)"),
    # Diamond pieces: the stones' grading (Gweb's diamond labels printed carats only).
    "main_stone": gettext_lazy("Main stone (ct, colour, clarity)"),
    "grade": gettext_lazy("Colour / clarity"),
    "cut": gettext_lazy("Cut"),
    "certificate": gettext_lazy("Certificate"),
    "all_stones": gettext_lazy("All stones"),
    "origin": gettext_lazy("Natural or lab-grown"),
    "model": gettext_lazy("Model"),
    "fineness": gettext_lazy("Fineness (bars)"),
    "serial": gettext_lazy("Serial no. (bars)"),
    "price": gettext_lazy("Label price"),
    "branch": gettext_lazy("Branch"),
}
MM_PER_PT = Decimal("0.3528")
DOTS_PER_MM = 8  # 203 dpi, the usual Zebra thermal head


def _plain(value: Decimal, places: int) -> str:
    return str(Decimal(value).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))


def _stones(item) -> list:
    """The piece's stones, the biggest first (a preview sample carries its own)."""
    if not getattr(item, "pk", None):
        return list(getattr(item, "sample_stones", []))
    return sorted(item.stones.all(), key=lambda s: s.carat / (s.count or 1), reverse=True)


def _ct(value) -> str:
    return f"{Decimal(value).normalize():f}"


def _stone_text(key: str, item) -> str:
    stones = _stones(item)
    if not stones:
        return ""
    main = stones[0]
    if key == "main_stone":
        each = main.carat / (main.count or 1)
        parts = [f"{main.count}×{_ct(each)} ct" if main.count > 1 else f"{_ct(each)} ct",
                 main.color, main.clarity]
        return " ".join(part for part in parts if part)
    if key == "grade":
        return " / ".join(part for part in (main.color, main.clarity) if part)
    if key == "cut":
        return main.cut
    if key == "certificate":
        return " ".join(part for part in (main.lab, main.certificate_no) if part) \
            if main.certificate_no else ""
    if key == "origin":
        from apps.diamonds.models import StoneOrigin

        origin = getattr(main, "origin", "")
        return str(StoneOrigin(origin).label) if origin in StoneOrigin.values else ""
    if key == "all_stones":
        return " + ".join(
            " ".join(part for part in (f"{s.count}×{_ct(s.carat / (s.count or 1))}",
                                       s.color, s.clarity) if part) for s in stones)
    return ""


def field_text(key: str, item, shop: str = "") -> str:
    """What a text field prints for a piece, in the active language. Empty = nothing."""
    if key == "code":
        return item.barcode
    if key == "shop":
        return shop
    if key == "category":
        return item.category.name
    if key == "karat":
        return item.karat.label if item.karat_id else ""
    if key == "weight":
        return _("%(weight)s g") % {"weight": num(item.gross_weight_g, 3)}
    if key == "making":
        return _("Making %(rate)s") % {"rate": num(item.list_making_rate, 0)} \
            if item.list_making_rate else ""
    if key == "stones":
        return _("%(ct)s ct") % {"ct": num(item.stone_weight_ct, 2)} \
            if item.stone_weight_ct else ""
    if key == "price":
        return num(item.label_price, 0) if item.label_price else ""
    if key == "branch":
        return item.branch.name
    if key in ("main_stone", "grade", "cut", "certificate", "all_stones", "origin"):
        return _stone_text(key, item)
    if key == "model":
        return getattr(item, "model_code", "")
    if key == "fineness":
        return str((getattr(item, "attributes", None) or {}).get("fineness", ""))
    if key == "serial":
        return getattr(item, "external_code", "")
    return ""


@dataclass
class LabelPart:
    """What one area prints, in order: ("barcode", svg) or ("text", value)."""

    elements: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class Label:
    item: object
    parts: list[LabelPart]


def build_label(template: LabelTemplate, item, shop: str = "") -> Label:
    areas = [template.fields]
    if template.layout == Layout.SPLIT:
        areas.append(template.fields_b)
    parts = []
    for keys in areas:
        part = LabelPart()
        for key in keys:
            if key == "barcode":
                part.elements.append(("barcode", code128(item.barcode).svg()))
            elif text := field_text(key, item, shop):
                part.elements.append(("text", text))
        parts.append(part)
    return Label(item=item, parts=parts)


@dataclass
class Page:
    cells: list[Label | None]


def paginate(template: LabelTemplate, labels: list[Label], *, skip: int = 0) -> list[Page]:
    """Roll: one label per page. Sheet: fill the grid, leaving `skip` used positions empty."""
    if template.media == Media.ROLL:
        return [Page([label]) for label in labels]
    per_page = template.per_page
    cells: list[Label | None] = [None] * min(max(skip, 0), per_page - 1) + labels
    return [Page(cells[i:i + per_page]) for i in range(0, len(cells), per_page)] or [Page([])]


# --- ZPL ----------------------------------------------------------------------------------------

def _dots(mm) -> int:
    return int((Decimal(mm) * DOTS_PER_MM).to_integral_value(rounding=ROUND_HALF_UP))


def _zpl_safe(text: str) -> str:
    return text.replace("^", " ").replace("~", " ")


def _zpl_text(key: str, item, shop: str) -> str:
    """Latin text for ZPL: printer fonts do not shape Arabic, so Arabic values are left out."""
    if key == "karat":
        return f"{item.karat.code}K" if item.karat_id else ""
    if key == "weight":
        return f"{_plain(item.gross_weight_g, 3)} g"
    if key == "making":
        return _plain(item.list_making_rate, 0) if item.list_making_rate else ""
    if key == "stones":
        return f"{_plain(item.stone_weight_ct, 2)} ct" if item.stone_weight_ct else ""
    if key == "price":
        return _plain(item.label_price, 0) if item.label_price else ""
    if key in ("main_stone", "grade", "cut", "certificate", "all_stones"):
        text = _stone_text(key, item).replace("×", "x")
        return text if text.isascii() else ""
    with translation.override("en"):
        text = field_text(key, item, shop)
    return text if text.isascii() else ""


def zpl(template: LabelTemplate, items, *, copies: int = 1, shop: str = "") -> str:
    area = _dots(template.area_width_mm)
    offsets = [0]
    if template.layout == Layout.SPLIT:
        offsets.append(area + _dots(template.tail_mm))
    text_height = max(_dots(template.font_size_pt * MM_PER_PT), 12)
    margin = DOTS_PER_MM  # 1 mm
    out = []
    for item in items:
        label = [f"^XA^CI28^PW{_dots(template.width_mm)}^LL{_dots(template.height_mm)}^LH0,0"]
        for x0, keys in zip(offsets, [template.fields, template.fields_b], strict=False):
            y = margin
            for key in keys:
                if key == "barcode":
                    # The label edge and the fold give the quiet zone; size on the bars alone.
                    bars = sum(code128(item.barcode).widths)
                    module = max(1, min(3, (area - 2 * margin) // bars))
                    height = _dots(template.barcode_height_mm)
                    label.append(f"^FO{x0 + margin},{y}^BY{module}^BCN,{height},N,N,N,A"
                                 f"^FD{_zpl_safe(item.barcode)}^FS")
                    y += height + 4
                    continue
                text = _zpl_text(key, item, shop)
                if text:
                    label.append(f"^FO{x0 + margin},{y}^A0N,{text_height},{text_height}"
                                 f"^FD{_zpl_safe(text)}^FS")
                    y += text_height + 2
        label.append(f"^PQ{copies}^XZ")
        out.append("".join(label))
    return "\n".join(out) + "\n"


# --- defaults -------------------------------------------------------------------------------

def ensure_default_templates(locale: str = "ar") -> None:
    """A tenant starts with a jewellery tail tag and an A4 sheet; both can be edited."""
    if LabelTemplate.objects.exists():
        return
    with translation.override(locale):
        LabelTemplate.objects.create(
            name=_("Tail tag 70 × 12 mm"), media=Media.ROLL, layout=Layout.SPLIT,
            width_mm=70, height_mm=12, tail_mm=20, fields=["barcode", "code"],
            fields_b=["karat", "weight", "making"], font_size_pt=Decimal("6"),
            barcode_height_mm=Decimal("5.5"), is_default=True)
        LabelTemplate.objects.create(
            name=_("A4 sheet, 3 × 8 labels"), media=Media.SHEET, layout=Layout.SINGLE,
            width_mm=70, height_mm=37, fields=["shop", "barcode", "code", "category", "karat",
                                                "weight", "making"],
            font_size_pt=Decimal("8"), barcode_height_mm=Decimal("9"), columns=3, rows=8,
            margin_top_mm=Decimal("0.5"))
