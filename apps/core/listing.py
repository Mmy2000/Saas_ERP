"""One way to search and filter a list of documents (Gweb's many "enquiry" screens, استعلام):
a search box, a date range, the branch, the list's own choices (status, kind…) and, from a link,
one party (`?party=<id>`, shown as a removable chip). The template side is
components/list_filters.html, components/list_totals.html and components/pager.html.

    found = ListQuery(request, permission="sales.invoice.view",
                      search=("number", "customer__name", "customer_phone"),
                      party="customer_id",
                      choices=[Choice("status", _("Status"), DocStatus.choices, "status")])
    invoices = found.apply(queryset)
    render(..., {"filters": found, "page": found.page(invoices.order_by(...))})
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from functools import reduce
from operator import or_
from urllib.parse import urlencode

from django.core.paginator import Paginator
from django.db.models import Q


@dataclass
class Choice:
    name: str  # the query parameter
    label: object
    options: list  # [(value, label)]
    lookup: str = ""  # the field to filter on; or `apply` for anything else
    apply: object = None  # callable(queryset, value) -> queryset
    value: str = ""  # the default when the address has none
    tabs: bool = False  # shown as tabs above the list (the page renders filters.tabs)


@dataclass
class Total:
    label: object
    value: object
    places: int = 2
    unit: str = ""


def _date(text: str) -> date | None:
    try:
        return date.fromisoformat(text) if text else None
    except ValueError:
        return None


@dataclass
class ListQuery:
    request: object
    permission: str
    search: tuple = ()  # fields matched with icontains
    search_label: object = ""
    date_field: str = "business_date"  # "" for no date range
    branch_field: str = "branch_id"  # "" for no branch filter; a callable(qs, ids) for two
    party: object = ""  # the field `?party=<id>` filters on, or callable(queryset, id)
    party_param: str = "party"  # the address's name for it (older links say ?customer=)
    choices: list = field(default_factory=list)
    per_page: int = 25

    def __post_init__(self):
        from apps.org.models import Branch

        get = self.request.GET
        self.q = get.get("q", "").strip()[:100]
        self.date_from, self.date_to = _date(get.get("from", "")), _date(get.get("to", ""))
        allowed = self.request.actor.branch_ids(self.permission)
        branches = Branch.objects.filter(is_active=True).order_by("code")
        self.branches = list(branches if allowed is None else branches.filter(pk__in=allowed))
        branch = get.get("branch", "")
        self.branch = int(branch) if branch.isdigit() and int(branch) in {
            b.pk for b in self.branches} else None
        party = get.get(self.party_param, "")
        self.party_id = int(party) if self.party and party.isdigit() else None
        self.party_name = ""
        if self.party_id:
            from apps.parties.models import Party

            self.party_name = Party.objects.filter(pk=self.party_id).values_list(
                "name", flat=True).first() or ""
        for choice in self.choices:
            value = get.get(choice.name, choice.value)
            choice.value = value if value in {str(v) for v, _label in choice.options} else ""

    @property
    def show_branches(self) -> bool:
        return bool(self.branch_field) and len(self.branches) > 1

    @property
    def active(self) -> bool:
        return bool(self.q or self.date_from or self.date_to or self.branch or self.party_id
                    or any(c.value for c in self.choices if not c.tabs))

    def apply(self, queryset, *, search: bool = True):
        if search and self.q and self.search:
            queryset = queryset.filter(reduce(or_, (Q(**{f"{name}__icontains": self.q})
                                                    for name in self.search))).distinct()
        if self.date_field and self.date_from:
            queryset = queryset.filter(**{f"{self.date_field}__gte": self.date_from})
        if self.date_field and self.date_to:
            queryset = queryset.filter(**{f"{self.date_field}__lte": self.date_to})
        if self.branch is not None:
            if callable(self.branch_field):
                queryset = self.branch_field(queryset, {self.branch})
            elif self.branch_field:
                queryset = queryset.filter(**{self.branch_field: self.branch})
        if self.party_id and self.party:
            queryset = (self.party(queryset, self.party_id) if callable(self.party)
                        else queryset.filter(**{self.party: self.party_id}))
        for choice in self.choices:
            if not choice.value:
                continue
            if choice.apply is not None:
                queryset = choice.apply(queryset, choice.value)
            elif choice.lookup:
                queryset = queryset.filter(**{choice.lookup: choice.value})
        return queryset

    def page(self, queryset):
        return Paginator(queryset, self.per_page).get_page(self.request.GET.get("page"))

    @property
    def query(self) -> str:
        """The filters as a query string (without the page), for the pager and print links."""
        return self._encode(party=True)

    def _encode(self, *, party: bool, **override) -> str:
        values = {"q": self.q, "from": self.date_from or "", "to": self.date_to or "",
                  "branch": self.branch or "",
                  self.party_param: (self.party_id or "") if party else "",
                  **{c.name: c.value for c in self.choices}, **override}
        return urlencode({k: v for k, v in values.items() if v not in ("", None)})

    @property
    def tabs(self) -> list[dict]:
        """The choices shown as tabs: each option with its address (the other filters kept)."""
        return [{"label": label, "active": choice.value == str(value),
                 "query": self._encode(party=True, **{choice.name: value})}
                for choice in self.choices if choice.tabs for value, label in choice.options]

    def without_party(self) -> str:
        return self._encode(party=False)
