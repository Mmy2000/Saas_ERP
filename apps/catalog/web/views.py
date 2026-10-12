from django.shortcuts import render

from apps.catalog.domain.metal import DEFAULT_REFERENCE_FINENESS
from apps.catalog.models import Currency, ItemCategory, Karat
from apps.iam.authz import permission_required


@permission_required("catalog.view")
def karats(request):
    rows = []
    for karat in Karat.objects.select_related("metal").order_by("metal__code", "-code"):
        ratio = karat.fineness / DEFAULT_REFERENCE_FINENESS if karat.metal.code == "gold" else None
        rows.append({"karat": karat, "ratio": ratio})
    return render(request, "catalog/karats.html", {"rows": rows})


@permission_required("catalog.view")
def currencies(request):
    """The currencies the business trades in, with the exchange rate in force."""
    from django.utils import timezone

    from apps.pricing.models import FxRate
    from apps.pricing.selectors import functional_currency

    home, now, rows = functional_currency(), timezone.now(), []
    for currency in Currency.objects.order_by("-is_active", "code"):
        rate = None if currency.code == home else (
            FxRate.objects.filter(currency=currency, effective_at__lte=now)
            .order_by("-effective_at", "-id").first())
        rows.append({"currency": currency, "home": currency.code == home, "rate": rate})
    return render(request, "catalog/currencies.html", {"rows": rows, "home_currency": home})


def _tree(categories):
    """Depth-first order so children follow their parent in a flat table."""
    children: dict[int | None, list] = {}
    for category in categories:
        children.setdefault(category.parent_id, []).append(category)
    ordered = []

    def walk(parent_id):
        for category in sorted(children.get(parent_id, []), key=lambda c: c.code):
            ordered.append(category)
            walk(category.pk)

    walk(None)
    return ordered


@permission_required("catalog.view")
def categories(request):
    queryset = (ItemCategory.objects.select_related("default_karat__metal")
                .prefetch_related("making_charges__currency"))
    return render(request, "catalog/categories.html", {"categories": _tree(list(queryset))})
