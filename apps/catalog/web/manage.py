"""Add and edit item categories and karats."""

from __future__ import annotations

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _

from apps.catalog.models import ItemCategory, Karat
from apps.catalog.services import (
    CreateItemCategoryCommand,
    KaratCommand,
    MakingChargeInput,
    UpdateItemCategoryCommand,
    create_item_category,
    create_karat,
    update_item_category,
    update_karat,
)
from apps.core.errors import ValidationError
from apps.iam.authz import permission_required

from .forms import CategoryForm, KaratForm

# Service error keys → form fields.
FIELD_NAMES = {"parent_id": "parent", "default_karat_id": "default_karat",
               "commission_rate": "commission_percent"}


def _show(form, exc: ValidationError) -> None:
    placed = False
    for key, problems in (exc.fields or {}).items():
        name = FIELD_NAMES.get(key, key)
        for problem in problems if isinstance(problems, list) else [problems]:
            if name in form.fields:
                form.add_error(name, str(problem))
                placed = True
            else:
                form.add_error(None, str(problem))
    if not placed:
        form.add_error(None, exc.message)


def _category_command(form, cls, **extra):
    d = form.cleaned_data
    return cls(
        code=d["code"], name=d["name"], short_name=d["short_name"], parent_id=d["parent"],
        product_family=d["product_family"], tracking=d["tracking"],
        default_karat_id=d["default_karat"], barcode_prefix=d["barcode_prefix"],
        commission_rate=form.commission_rate(),
        making_charges=tuple(
            MakingChargeInput(currency_code=code, list_rate_per_g=d[f"list_{code}"] or 0,
                              cost_rate_per_g=d[f"cost_{code}"] or 0)
            for code, _list, _cost in form.charge_rows()),
        **extra)


@permission_required("catalog.category.manage")
def category_new(request):
    form = CategoryForm(request.POST or None, initial={"parent": request.GET.get("parent")})
    if request.method == "POST" and form.is_valid():
        try:
            category = create_item_category(_category_command(form, CreateItemCategoryCommand),
                                            actor=request.actor)
        except ValidationError as exc:
            _show(form, exc)
        else:
            messages.success(request, _("%(name)s added.") % {"name": category.name})
            return redirect("categories")
    return render(request, "catalog/category_form.html", {"form": form, "category": None})


@permission_required("catalog.category.manage")
def category_edit(request, pk):
    category = ItemCategory.objects.prefetch_related("making_charges__currency").filter(
        pk=pk).first()
    if category is None:
        raise Http404
    initial = {
        "code": category.code, "name": category.name, "short_name": category.short_name,
        "parent": category.parent_id, "product_family": category.product_family,
        "tracking": category.tracking, "default_karat": category.default_karat_id,
        "barcode_prefix": category.barcode_prefix,
        "commission_percent": (category.commission_rate * 100).normalize(),
        "is_active": category.is_active,
    }
    for charge in category.making_charges.all():
        initial[f"list_{charge.currency.code}"] = charge.list_rate_per_g.normalize()
        initial[f"cost_{charge.currency.code}"] = charge.cost_rate_per_g.normalize()
    form = CategoryForm(request.POST or None, initial=initial, category=category)
    if request.method == "POST" and form.is_valid():
        try:
            update_item_category(category.pk, _category_command(
                form, UpdateItemCategoryCommand, is_active=form.cleaned_data["is_active"]),
                actor=request.actor)
        except ValidationError as exc:
            _show(form, exc)
        else:
            messages.success(request, _("Saved."))
            return redirect("categories")
    return render(request, "catalog/category_form.html", {"form": form, "category": category})


@permission_required("catalog.karat.manage")
def karat_new(request):
    form = KaratForm(request.POST or None, initial={"metal": "gold"})
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            karat = create_karat(d["metal"], d["code"], KaratCommand(
                fineness=d["fineness"], display_name=d["display_name"],
                is_active=d["is_active"]), actor=request.actor)
        except ValidationError as exc:
            _show(form, exc)
        else:
            messages.success(request, _("%(name)s added.") % {"name": karat.label})
            return redirect("karats")
    return render(request, "catalog/karat_form.html", {"form": form, "karat": None})


@permission_required("catalog.karat.manage")
def karat_edit(request, pk):
    karat = Karat.objects.select_related("metal").filter(pk=pk).first()
    if karat is None:
        raise Http404
    form = KaratForm(request.POST or None, karat=karat, initial={
        "fineness": karat.fineness.normalize(), "display_name": karat.display_name,
        "is_active": karat.is_active})
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            update_karat(karat.pk, KaratCommand(
                fineness=d["fineness"], display_name=d["display_name"],
                is_active=True if karat.is_reference else d["is_active"]), actor=request.actor)
        except ValidationError as exc:
            _show(form, exc)
        else:
            messages.success(request, _("Saved."))
            return redirect("karats")
    return render(request, "catalog/karat_form.html", {"form": form, "karat": karat})
