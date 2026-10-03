"""Forms for item categories and karats (server-rendered; the rules live in catalog.services)."""

from __future__ import annotations

from decimal import Decimal

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.catalog.models import (
    MAX_CATEGORY_DEPTH,
    Currency,
    ItemCategory,
    Karat,
    MetalCode,
    ProductFamily,
    Tracking,
)

CHECKBOX = "size-4 rounded border-slate-300 accent-brand-600"


def _style(form: forms.Form) -> None:
    for field in form.fields.values():
        if isinstance(field.widget, forms.CheckboxInput):
            field.widget.attrs.setdefault("class", CHECKBOX)
            continue
        css = "form-input"
        if isinstance(field, (forms.IntegerField, forms.DecimalField)):
            css += " num w-full text-start"
        field.widget.attrs.setdefault("class", css)


class CategoryForm(forms.Form):
    code = forms.CharField(label=_("Code"), max_length=32)
    name = forms.CharField(label=_("Name"), max_length=200)
    short_name = forms.CharField(label=_("Short name"), max_length=60, required=False,
                                 help_text=_("Printed on labels when the name is long."))
    parent = forms.TypedChoiceField(label=_("Under"), required=False, coerce=int,
                                    empty_value=None)
    product_family = forms.ChoiceField(label=_("Family"), choices=ProductFamily.choices)
    tracking = forms.ChoiceField(label=_("Tracking"), choices=Tracking.choices,
                                 help_text=_("Per piece: each piece has its own barcode. "
                                             "By weight: counted in grams (chains, bullion)."))
    default_karat = forms.TypedChoiceField(label=_("Default karat"), required=False,
                                           coerce=int, empty_value=None)
    barcode_prefix = forms.IntegerField(label=_("Barcode prefix"), min_value=1,
                                        max_value=9999, required=False,
                                        help_text=_("Pieces get barcodes starting with it."))
    commission_percent = forms.DecimalField(label=_("Sales commission (%)"), min_value=0,
                                            max_value=100, decimal_places=4, required=False)
    is_active = forms.BooleanField(label=_("Active"), required=False, initial=True)

    def __init__(self, *args, category: ItemCategory | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.category = category
        excluded = set()
        if category is not None:
            from apps.catalog.services import _subtree

            excluded = {category.pk, *(c.pk for c in _subtree(category))}
        parents = [c for c in ItemCategory.objects.order_by("code")
                   if c.depth < MAX_CATEGORY_DEPTH and c.pk not in excluded]
        self.fields["parent"].choices = [("", _("— Top level —"))] + [
            (c.pk, f"{'· ' * (c.depth - 1)}{c.code} {c.name}") for c in parents]
        karats = Karat.objects.select_related("metal").filter(is_active=True)
        self.fields["default_karat"].choices = [("", "—")] + [(k.pk, k.label) for k in karats]
        self.currencies = list(Currency.objects.filter(is_active=True).order_by("code"))
        for currency in self.currencies:
            for kind, label in (("list", _("Selling making charge / g")),
                                ("cost", _("Cost making charge / g"))):
                self.fields[f"{kind}_{currency.code}"] = forms.DecimalField(
                    label=f"{label} ({currency.code})", min_value=0, decimal_places=4,
                    required=False)
        if category is None:
            del self.fields["is_active"]
        _style(self)

    def charge_rows(self):
        return [(c.code, self[f"list_{c.code}"], self[f"cost_{c.code}"]) for c in self.currencies]

    def commission_rate(self) -> Decimal:
        return (self.cleaned_data.get("commission_percent") or Decimal(0)) / 100


class KaratForm(forms.Form):
    metal = forms.ChoiceField(label=_("Metal"), choices=MetalCode.choices)
    code = forms.IntegerField(label=_("Karat"), min_value=1, max_value=9999,
                              help_text=_("e.g. 18, 21 or 24 for gold, 925 for silver."))
    fineness = forms.DecimalField(label=_("Fineness (‰)"), min_value=Decimal("0.001"),
                                  max_value=1000, decimal_places=3,
                                  help_text=_("Parts of pure metal per thousand, "
                                              "e.g. 875 for 21K."))
    display_name = forms.CharField(label=_("Own name"), max_length=40, required=False,
                                   help_text=_("Empty: shown as “21K” in each user's language."))
    is_active = forms.BooleanField(label=_("Active"), required=False, initial=True)

    def __init__(self, *args, karat: Karat | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        if karat is not None:  # metal and karat number identify it: fixed once created
            del self.fields["metal"]
            del self.fields["code"]
            if karat.is_reference:
                self.fields["is_active"].disabled = True
        _style(self)
