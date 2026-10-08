from django.core.paginator import Paginator
from django.shortcuts import render

from apps.iam.authz import permission_required
from apps.iam.catalog import AUTHENTICATED

from . import services


@permission_required(AUTHENTICATED)
def notifications(request):
    actor = request.actor
    shown = request.GET.get("show", "")
    items = services.visible(actor).select_related("branch").order_by("-created_at", "-id")
    if shown == "unread":
        items = items.filter(read_at__isnull=True)
    page = Paginator(items, 30).get_page(request.GET.get("page"))
    return render(request, "notifications/notifications.html", {
        "page": page, "show": shown, "unread": services.unread_count(actor),
        "preferences": services.preferences(actor),
    })
