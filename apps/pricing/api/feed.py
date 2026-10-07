"""Automatic gold prices: the shop's settings and its buttons (update now, approve, pause)."""

from decimal import Decimal

from django.db import transaction
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.errors import ValidationError

from .. import feed as feeds

MANAGE = "pricing.feed.manage"


class FeedSerializer(serializers.Serializer):
    is_enabled = serializers.BooleanField()
    source = serializers.CharField(max_length=30, required=False, allow_blank=True, default="")
    sell_adjust = serializers.DecimalField(max_digits=10, decimal_places=2)
    buy_adjust = serializers.DecimalField(max_digits=10, decimal_places=2)
    scrap_adjust = serializers.DecimalField(max_digits=10, decimal_places=2)
    rounding = serializers.DecimalField(max_digits=6, decimal_places=2, min_value=0)
    min_change = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0)
    max_jump_pct = serializers.DecimalField(max_digits=5, decimal_places=2,
                                            min_value=Decimal("0.1"), max_value=Decimal(50))
    follow_silver = serializers.BooleanField(required=False, default=True)
    active_from = serializers.TimeField(required=False, allow_null=True, default=None)
    active_to = serializers.TimeField(required=False, allow_null=True, default=None)

    def validate_source(self, value):
        from apps.platform.market.sources import SOURCES

        if value and value not in SOURCES:
            raise serializers.ValidationError("Unknown source.")
        return value

    def validate(self, data):
        if (data["active_from"] is None) != (data["active_to"] is None):
            raise serializers.ValidationError({"active_to": ["Give both times, or neither."]})
        return data


class FeedView(APIView):
    required_permissions = {"PUT": MANAGE}

    def put(self, request):
        serializer = FeedSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            feed = feeds.feed_settings()
            for name, value in serializer.validated_data.items():
                setattr(feed, name, value)
            feed.updated_by = request.user
            feed.save()
        outcome = feeds.follow_market() if feed.is_enabled else feeds.Outcome.OFF
        return Response({"is_enabled": feed.is_enabled, "outcome": outcome})


class FeedActionView(APIView):
    """POST {"action": update | approve | dismiss | pause | resume}."""

    required_permissions = {"POST": MANAGE}

    def post(self, request):
        action = request.data.get("action", "")
        if action == "update":
            outcome = feeds.follow_market(force=True, actor=request.actor)
        elif action == "approve":
            outcome = feeds.follow_market(force=True, approve=True, actor=request.actor)
        elif action == "dismiss":
            feeds.dismiss_held()
            outcome = "dismissed"
        elif action == "pause":
            feeds.pause_until_tomorrow()
            outcome = feeds.Outcome.PAUSED
        elif action == "resume":
            feeds.resume()
            outcome = "resumed"
        else:
            raise ValidationError("Unknown action.", fields={"action": ["Unknown action."]})
        return Response({"outcome": outcome})
