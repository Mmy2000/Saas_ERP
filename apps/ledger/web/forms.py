"""Forms for the chart of accounts (server-rendered; the rules live in ledger.accounts)."""

from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.catalog.models import Currency
from apps.catalog.web.forms import _style
from apps.ledger.accounts import parent_choices
from apps.ledger.models import CommodityScope, Nature

ONE_CURRENCY = "currency"


class NewAccountForm(forms.Form):
    parent = forms.TypedChoiceField(label=_("Under the group"), coerce=int)
    is_group = forms.TypedChoiceField(label=_("Kind"), coerce=lambda v: v == "1", choices=(
        ("0", _("An account that takes entries")), ("1", _("A group of accounts"))))
    code = forms.CharField(label=_("Code"), max_length=20, required=False,
                           help_text=_("Starts with the group's code. Empty: the next free one."))
    name = forms.CharField(label=_("Name"), max_length=200)
    nature = forms.ChoiceField(label=_("Normal balance"), choices=Nature.choices)
    commodity_scope = forms.ChoiceField(label=_("Takes"), choices=(
        (CommodityScope.MONEY, _("Money, any currency")),
        (ONE_CURRENCY, _("One currency only")),
        (CommodityScope.ANY, _("Money and gold")),
        (CommodityScope.METAL, _("Gold only (fine grams)"))))
    currency = forms.ChoiceField(label=_("Currency"), required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["parent"].choices = [
            (a.pk, f"{a.code} · {a.label}") for a in parent_choices()]
        self.fields["currency"].choices = [("", "—")] + [
            (c.code, f"{c.code} · {c.label}") for c in Currency.objects.filter(is_active=True)]
        for name in ("is_group", "nature", "commodity_scope"):
            self.fields[name].widget.attrs["data-native"] = ""
        _style(self)


class EditAccountForm(forms.Form):
    name = forms.CharField(label=_("Name"), max_length=200, required=False)
    is_active = forms.BooleanField(label=_("Active"), required=False)

    def __init__(self, *args, standard: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        if standard:
            self.fields["name"].help_text = _("Empty: the standard name, in each user's "
                                              "language.")
        _style(self)
