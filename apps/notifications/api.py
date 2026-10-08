"""The bell's endpoints: the unread count (polled), the latest notifications with what needs
attention (when the bell opens), marking read, and the member's own preferences."""

from __future__ import annotations

from django.urls import path
from django.utils import timezone
from django.utils.timesince import timesince
from django.utils.translation import gettext as _
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.errors import ValidationError
from apps.iam.catalog import AUTHENTICATED

from . import services
from .kinds import KINDS


def _ago(when) -> str:
    seconds = (timezone.now() - when).total_seconds()
    if seconds < 60:
        return _("just now")
    return _("%(time)s ago") % {"time": timesince(when, depth=1)}


def serialize(notification) -> dict:
    title, body = notification.text
    return {"id": notification.pk, "kind": notification.kind, "icon": notification.icon,
            "title": title, "body": body, "url": notification.url,
            "read": notification.read_at is not None, "at": notification.created_at.isoformat(),
            "ago": _ago(notification.created_at)}


class BellView(APIView):
    required_permissions = {"GET": AUTHENTICATED}

    def get(self, request):
        actor = request.actor
        unread = services.unread_count(actor)
        if request.query_params.get("count"):
            return Response({"unread": unread})
        from apps.org.dashboard import needs_attention

        latest = services.visible(actor).order_by("-created_at", "-id")[:services.LATEST]
        return Response({
            "unread": unread, "results": [serialize(n) for n in latest],
            "attention": [{"label": str(item.label), "count": item.count, "url": item.url,
                           "icon": item.icon} for item in needs_attention(actor)],
        })


class ReadView(APIView):
    required_permissions = {"POST": AUTHENTICATED}

    def post(self, request, pk=None):
        if pk is None:
            services.mark_all_read(request.actor)
        else:
            services.mark_read(request.actor, pk)
        return Response({"unread": services.unread_count(request.actor)})


class PreferencesSerializer(serializers.Serializer):
    muted = serializers.ListField(child=serializers.CharField(max_length=32), max_length=50)


class PreferencesView(APIView):
    required_permissions = {"GET": AUTHENTICATED, "PUT": AUTHENTICATED}

    def get(self, request):
        return Response({"results": services.preferences(request.actor)})

    def put(self, request):
        serializer = PreferencesSerializer(data=request.data)
        if not serializer.is_valid():
            raise ValidationError(_("Please correct the highlighted fields."),
                                  fields=serializer.errors)
        unknown = set(serializer.validated_data["muted"]) - set(KINDS)
        if unknown:
            raise ValidationError(_("Unknown notification kind."),
                                  fields={"muted": sorted(unknown)})
        services.set_muted(request.actor, set(serializer.validated_data["muted"]))
        return Response({"results": services.preferences(request.actor)})


urlpatterns = [
    path("", BellView.as_view(), name="notifications-api"),
    path("<int:pk>/read/", ReadView.as_view(), name="notifications-read"),
    path("read-all/", ReadView.as_view(), name="notifications-read-all"),
    path("preferences/", PreferencesView.as_view(), name="notifications-preferences"),
]
