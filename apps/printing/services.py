"""Label template setup. `template_from` validates input into an (unsaved) template, so the same
rules serve saving and the live preview of the template form."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.utils.translation import gettext as _

from apps.core.errors import NotFound, ValidationError

from .labels import FIELDS
from .models import LabelTemplate, Layout, Media

MANAGE = "printing.templates.manage"
LIMITS = {  # field → (min, max) in mm / pt / count
    "width_mm": (10, 300), "height_mm": (5, 300), "tail_mm": (0, 200),
    "font_size_pt": (3, 36), "barcode_height_mm": (2, 60),
    "page_width_mm": (50, 500), "page_height_mm": (50, 500),
    "margin_top_mm": (0, 50), "margin_start_mm": (0, 50), "gap_x_mm": (0, 50),
    "gap_y_mm": (0, 50), "columns": (1, 12), "rows": (1, 40),
}


def template_from(data: dict, template: LabelTemplate | None = None) -> LabelTemplate:
    """Apply `data` to a template (a new one by default) and check it. Nothing is saved."""
    template = template or LabelTemplate()
    errors: dict[str, list[str]] = {}

    name = str(data.get("name", "")).strip()
    if not name:
        errors["name"] = [_("Required.")]
    template.name = name[:100]
    template.media = data.get("media") if data.get("media") in Media.values else Media.ROLL
    template.layout = data.get("layout") if data.get("layout") in Layout.values else Layout.SINGLE

    for key, (low, high) in LIMITS.items():
        raw = data.get(key)
        if raw in (None, ""):
            continue
        try:
            value = Decimal(str(raw))
        except InvalidOperation:
            errors[key] = [_("Enter a number.")]
            continue
        if not low <= value <= high:
            errors[key] = [_("Between %(low)s and %(high)s.") % {"low": low, "high": high}]
            continue
        setattr(template, key, int(value) if key in ("columns", "rows") else value)

    for key in ("fields", "fields_b"):
        chosen = data.get(key) or []
        if isinstance(chosen, str):
            chosen = [part for part in chosen.split(",") if part]
        unknown = [c for c in chosen if c not in FIELDS]
        if unknown:
            errors[key] = [_("Unknown field.")]
        setattr(template, key, [c for c in dict.fromkeys(chosen) if c in FIELDS])
    if template.layout == Layout.SINGLE:
        template.fields_b = []
        template.tail_mm = template.tail_mm or Decimal(0)
    if not template.fields and not template.fields_b:
        errors["fields"] = [_("Choose at least one thing to print.")]
    if template.layout == Layout.SPLIT and template.area_width_mm <= 0:
        errors["tail_mm"] = [_("The fold is wider than the label.")]
    if template.media == Media.SHEET:
        used_x = template.columns * template.width_mm + (template.columns - 1) * template.gap_x_mm
        used_y = template.rows * template.height_mm + (template.rows - 1) * template.gap_y_mm
        if template.margin_start_mm + used_x > template.page_width_mm \
                or template.margin_top_mm + used_y > template.page_height_mm:
            errors["columns"] = [_("The labels do not fit on the page.")]
    if errors:
        raise ValidationError(_("Please correct the highlighted fields."), fields=errors)
    return template


def _save(template: LabelTemplate, make_default: bool) -> LabelTemplate:
    try:
        with transaction.atomic():
            if make_default or not LabelTemplate.objects.exclude(pk=template.pk).filter(
                    is_default=True).exists():
                LabelTemplate.objects.exclude(pk=template.pk).filter(is_default=True).update(
                    is_default=False)
                template.is_default = True
            template.save()
    except IntegrityError:
        raise ValidationError(_("A template with this name already exists."),
                              fields={"name": [_("A template with this name already exists.")]}
                              ) from None
    return template


def create_template(data: dict, *, actor=None) -> LabelTemplate:
    if actor is not None:
        actor.require(MANAGE)
    return _save(template_from(data), bool(data.get("is_default")))


def update_template(template_id: int, data: dict, *, actor=None) -> LabelTemplate:
    if actor is not None:
        actor.require(MANAGE)
    template = LabelTemplate.objects.filter(pk=template_id).first()
    if template is None:
        raise NotFound(_("Not found."))
    return _save(template_from(data, template), bool(data.get("is_default")))


def delete_template(template_id: int, *, actor=None) -> None:
    if actor is not None:
        actor.require(MANAGE)
    template = LabelTemplate.objects.filter(pk=template_id).first()
    if template is None:
        raise NotFound(_("Not found."))
    if LabelTemplate.objects.count() == 1:
        raise ValidationError(_("Keep at least one label template."), code="PRINTING_LAST_TEMPLATE")
    was_default = template.is_default
    template.delete()
    if was_default:
        LabelTemplate.objects.filter(pk=LabelTemplate.objects.order_by("name").values("pk")[:1]
                                     ).update(is_default=True)
