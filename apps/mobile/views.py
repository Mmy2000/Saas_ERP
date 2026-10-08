from django.contrib import messages
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.iam.authz import permission_required
from apps.iam.signins import device as describe

from . import tokens
from .models import MobileDevice


@permission_required("mobile.devices.manage")
def devices(request):
    """Settings → Mobile devices: the phones signed in to the app, and signing one out."""
    rows = list(MobileDevice.objects.filter(revoked_at__isnull=True)
                .select_related("membership__user").order_by("-last_seen_at"))
    rows = [row for row in rows if row.active]
    for row in rows:
        row.described = describe(row.user_agent)
    return render(request, "mobile/devices.html", {"devices": rows})


@require_POST
@permission_required("mobile.devices.manage")
def sign_out_device(request, pk):
    device = MobileDevice.objects.filter(pk=pk).first()
    if device is not None:
        tokens.revoke(device)
        messages.success(request, _("The phone was signed out."))
    return redirect("mobile-devices")
