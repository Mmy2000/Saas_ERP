from django.shortcuts import render

from apps.catalog.domain.metal import DEFAULT_REFERENCE_FINENESS
from apps.catalog.models import ItemCategory, Karat
from apps.iam.authz import permission_required


@permission_required("catalog.view")
def karats(request):
    rows = []
    for karat in Karat.objects.select_related("metal").order_by("metal__code", "-code"):
        ratio = karat.fineness / DEFAULT_REFERENCE_FINENESS if karat.metal.code == "gold" else None
        rows.append({"karat": karat, "ratio": ratio})
    return render(request, "catalog/karats.html", {"rows": rows})


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
