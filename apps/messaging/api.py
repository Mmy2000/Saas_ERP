from django.db import transaction
from django.urls import path
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.api.idempotency import idempotent
from apps.core.errors import NotFound, ValidationError

from . import services
from .models import Channel, DigestRecipient, OutboundMessage


class TargetSerializer(serializers.Serializer):
    """A printed document ({doc_type, pk}) or a party statement ({doc_type: "statement",
    role, pk, from, to})."""

    doc_type = serializers.CharField(max_length=40)
    pk = serializers.IntegerField(min_value=1)
    role = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")
    date_from = serializers.DateField(required=False, allow_null=True, default=None)
    date_to = serializers.DateField(required=False, allow_null=True, default=None)


def _target(request, data) -> services.Target:
    if data["doc_type"] == "statement":
        return services.statement_target(request, data["role"], data["pk"], data["date_from"],
                                         data["date_to"])
    return services.document_target(request, data["doc_type"], data["pk"])


def _query_target(request) -> services.Target:
    values = {key: request.query_params.get(key) or None
              for key in ("doc_type", "pk", "role", "date_from", "date_to")}
    serializer = TargetSerializer(data={k: v for k, v in values.items() if v is not None})
    if not serializer.is_valid():
        raise ValidationError(str(serializer.errors))
    return _target(request, serializer.validated_data)


class DraftView(APIView):
    """What the Send dialog starts with: the party's contact and the default texts."""

    required_permissions = {"GET": "messaging.send"}

    def get(self, request):
        target = _query_target(request)
        return Response({
            "label": target.label, **services.contact(target.party),
            "email_text": services.default_text(target, Channel.EMAIL),
            "whatsapp_text": services.default_text(target, Channel.WHATSAPP),
        })


class SendSerializer(TargetSerializer):
    channel = serializers.ChoiceField(choices=Channel.choices)
    to = serializers.CharField(max_length=254, allow_blank=True, default="")
    message = serializers.CharField(max_length=4000, allow_blank=True, default="")


class SendView(APIView):
    required_permissions = {"POST": "messaging.send"}

    @idempotent
    def post(self, request):
        serializer = SendSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        target = _target(request, data)
        if data["channel"] == Channel.EMAIL:
            sent = services.email_target(target, user=request.user, to=data["to"],
                                         message=data["message"])
            return Response({"id": sent.pk, "status": sent.status}, status=201)
        sent, wa_url = services.whatsapp_target(
            target, user=request.user, phone=data["to"], message=data["message"],
            base_url=request.build_absolute_uri("/"))
        return Response({"id": sent.pk, "status": sent.status, "wa_url": wa_url}, status=201)


def _message(pk) -> OutboundMessage:
    message = OutboundMessage.objects.filter(pk=pk).first()
    if message is None:
        raise NotFound
    return message


class AgainView(APIView):
    required_permissions = {"POST": "messaging.send"}

    def post(self, request, pk):
        message = _message(pk)
        services.send_again(message)
        return Response({"id": message.pk, "status": message.status})


class StopView(APIView):
    required_permissions = {"POST": "messaging.send"}

    def post(self, request, pk):
        message = _message(pk)
        services.stop_link(message)
        return Response({"id": message.pk, "status": message.status})


class DigestSerializer(serializers.Serializer):
    members = serializers.ListField(child=serializers.IntegerField(min_value=1),
                                    allow_empty=True, max_length=500)


class DigestView(APIView):
    """Who gets the daily reminders e-mail."""

    required_permissions = {"PUT": "admin.users.manage"}

    def put(self, request):
        from apps.iam.models import Membership

        serializer = DigestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        wanted = set(Membership.objects.filter(pk__in=serializer.validated_data["members"])
                     .values_list("pk", flat=True))
        with transaction.atomic():
            DigestRecipient.objects.exclude(membership_id__in=wanted).delete()
            have = set(DigestRecipient.objects.values_list("membership_id", flat=True))
            DigestRecipient.objects.bulk_create(
                [DigestRecipient(membership_id=pk) for pk in sorted(wanted - have)])
        return Response({"members": sorted(wanted)})


urlpatterns = [
    path("draft/", DraftView.as_view(), name="message-draft"),
    path("send/", SendView.as_view(), name="message-send"),
    path("<int:pk>/again/", AgainView.as_view(), name="message-again"),
    path("<int:pk>/stop/", StopView.as_view(), name="message-stop"),
    path("digest/", DigestView.as_view(), name="message-digest"),
]
