from django.db import transaction
from django.urls import path
from django.utils.translation import gettext as _
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.api.idempotency import idempotent
from apps.core.errors import NotFound, ValidationError

from . import campaigns, services
from .models import (
    Campaign,
    Channel,
    DigestRecipient,
    LinkStatus,
    OptOut,
    OutboundMessage,
    Via,
)


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
            "whatsapp_routes": services.whatsapp_routes(request.actor),
        })


class SendSerializer(TargetSerializer):
    channel = serializers.ChoiceField(choices=Channel.choices)
    to = serializers.CharField(max_length=254, allow_blank=True, default="")
    message = serializers.CharField(max_length=4000, allow_blank=True, default="")
    via = serializers.ChoiceField(choices=Via.choices, required=False, default=Via.LINK)


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
        if data["via"] != Via.LINK:
            sent = services.whatsapp_send(target, actor=request.actor, phone=data["to"],
                                          message=data["message"], via=data["via"])
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
    daily = serializers.ListField(child=serializers.IntegerField(min_value=1),
                                  allow_empty=True, max_length=500)
    monthly = serializers.ListField(child=serializers.IntegerField(min_value=1),
                                    allow_empty=True, max_length=500, required=False,
                                    default=list)


class DigestView(APIView):
    """Who gets the daily reminders and the monthly report e-mails."""

    required_permissions = {"PUT": "admin.users.manage"}

    def put(self, request):
        from apps.iam.models import Membership

        serializer = DigestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        members = set(Membership.objects.filter(pk__in={*data["daily"], *data["monthly"]})
                      .values_list("pk", flat=True))
        daily, monthly = members & set(data["daily"]), members & set(data["monthly"])
        with transaction.atomic():
            DigestRecipient.objects.exclude(membership_id__in=members).delete()
            rows = {r.membership_id: r for r in DigestRecipient.objects.all()}
            for pk in sorted(members):
                row = rows.get(pk) or DigestRecipient(membership_id=pk)
                row.daily, row.monthly = pk in daily, pk in monthly
                row.save()
        return Response({"daily": sorted(daily), "monthly": sorted(monthly)})


class MonthlyNowView(APIView):
    """E-mail me last month's report now (to see what it looks like, or if one went missing)."""

    required_permissions = {"POST": "messaging.send"}

    @idempotent
    def post(self, request):
        from django.utils.translation import gettext as _

        from apps.core.errors import DomainError

        from .jobs import queue_monthly
        from .monthly import last_month

        if not request.user.email:
            raise DomainError(_("Add your e-mail address to your user first."),
                              code="MESSAGING_NO_EMAIL")
        message = queue_monthly(request.membership, *last_month(), user=request.user)
        if message is None:
            raise DomainError(_("Your roles do not include any of the monthly figures."),
                              code="MESSAGING_NOTHING_TO_SEND")
        return Response({"id": message.pk, "status": message.status}, status=201)


def _link_state(link, qr="") -> dict:
    if link is None:
        return {"status": LinkStatus.NOT_STARTED, "label": str(LinkStatus.NOT_STARTED.label),
                "phone": "", "qr": "", "error": ""}
    return {"status": link.status, "label": link.get_status_display(), "phone": link.phone,
            "qr": qr, "error": link.error,
            "linked_at": link.linked_at.isoformat() if link.linked_at else None}


class LinkView(APIView):
    """The shop's own WhatsApp number: GET how it is (with the QR while waiting for the scan),
    POST to start linking, DELETE to forget it."""

    required_permissions = {"GET": "messaging.whatsapp.link", "POST": "messaging.whatsapp.link",
                            "DELETE": "messaging.whatsapp.link"}

    def get(self, request):
        return Response(_link_state(*services.check_link()))

    def post(self, request):
        services.link_number(request.tenant.slug)
        return Response(_link_state(*services.check_link()))

    def delete(self, request):
        services.unlink_number()
        return Response(_link_state(None))


class LinkTestSerializer(serializers.Serializer):
    to = serializers.CharField(max_length=30)


class LinkTestView(APIView):
    required_permissions = {"POST": "messaging.whatsapp.link"}

    def post(self, request):
        serializer = LinkTestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        sent = services.send_test(actor=request.actor, phone=serializer.validated_data["to"])
        return Response({"id": sent.pk, "status": sent.status, "error": sent.error})


class AudienceSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=[(k, k) for k in campaigns.AUDIENCES])
    branch = serializers.IntegerField(required=False, allow_null=True, default=None)
    since = serializers.DateField(required=False, allow_null=True, default=None)
    numbers = serializers.CharField(required=False, allow_blank=True, default="",
                                    max_length=200000)


def _audience(data) -> campaigns.Audience:
    return campaigns.Audience(kind=data["kind"], branch_id=data["branch"], since=data["since"],
                              numbers=data["numbers"])


class CampaignSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=120, allow_blank=True)
    message = serializers.CharField(max_length=4000, allow_blank=True)
    audience = AudienceSerializer()
    daily_cap = serializers.IntegerField(required=False, default=150)
    min_gap = serializers.IntegerField(required=False, default=25)
    max_gap = serializers.IntegerField(required=False, default=70)


def _campaign_state(campaign) -> dict:
    return {"id": campaign.pk, "status": campaign.status, "label": campaign.get_status_display(),
            "total": campaign.total, "sent": campaign.sent, "failed": campaign.failed,
            "skipped": campaign.skipped, "left": campaign.left, "percent": campaign.percent,
            "note": campaign.note}


class CampaignPreviewView(APIView):
    """How many people a choice reaches, and who is skipped and why (nothing is saved)."""

    required_permissions = {"POST": "messaging.campaign.manage"}

    def post(self, request):
        serializer = AudienceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(campaigns.preview(_audience(serializer.validated_data)))


class CampaignsView(APIView):
    required_permissions = {"POST": "messaging.campaign.manage"}

    @idempotent
    def post(self, request):
        serializer = CampaignSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data
        campaign = campaigns.start(campaigns.CampaignInput(
            name=d["name"], message=d["message"], audience=_audience(d["audience"]),
            daily_cap=d["daily_cap"], min_gap=d["min_gap"], max_gap=d["max_gap"]),
            actor=request.actor)
        return Response(_campaign_state(campaign), status=201)


class CampaignView(APIView):
    required_permissions = {"GET": "messaging.campaign.manage",
                            "POST": "messaging.campaign.manage"}
    ACTIONS = {"pause": campaigns.pause, "resume": campaigns.resume, "cancel": campaigns.cancel}

    def get(self, request, pk):
        campaign = Campaign.objects.filter(pk=pk).first()
        if campaign is None:
            raise NotFound
        return Response(_campaign_state(campaign))

    def post(self, request, pk, action):
        if action not in self.ACTIONS:
            raise NotFound
        return Response(_campaign_state(self.ACTIONS[action](pk)))


class OptOutSerializer(serializers.Serializer):
    phone = serializers.CharField(max_length=30)
    note = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")


class OptOutsView(APIView):
    """Numbers that do not want campaigns: add one (POST) or take one off (DELETE ?id=)."""

    required_permissions = {"POST": "messaging.campaign.manage",
                            "DELETE": "messaging.campaign.manage"}

    def post(self, request):
        from . import gateways

        serializer = OptOutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        phone = gateways.phone_digits(serializer.validated_data["phone"])
        if not phone:
            raise ValidationError(_("Enter the WhatsApp number with the country code."),
                                  fields={"phone": [_("Enter the WhatsApp number with the "
                                                      "country code.")]})
        row, _created = OptOut.objects.get_or_create(
            phone=phone, defaults={"note": serializer.validated_data["note"][:200],
                                   "created_by": request.user})
        return Response({"id": row.pk, "phone": row.phone}, status=201)

    def delete(self, request):
        OptOut.objects.filter(pk=request.query_params.get("id") or 0).delete()
        return Response(status=204)


urlpatterns = [
    path("campaigns/", CampaignsView.as_view(), name="campaigns-api"),
    path("campaigns/preview/", CampaignPreviewView.as_view(), name="campaign-preview"),
    path("campaigns/<int:pk>/", CampaignView.as_view(), name="campaign-api"),
    path("campaigns/<int:pk>/<str:action>/", CampaignView.as_view(), name="campaign-action"),
    path("opt-outs/", OptOutsView.as_view(), name="opt-outs"),
    path("whatsapp/link/", LinkView.as_view(), name="whatsapp-link"),
    path("whatsapp/link/test/", LinkTestView.as_view(), name="whatsapp-link-test"),
    path("draft/", DraftView.as_view(), name="message-draft"),
    path("send/", SendView.as_view(), name="message-send"),
    path("<int:pk>/again/", AgainView.as_view(), name="message-again"),
    path("<int:pk>/stop/", StopView.as_view(), name="message-stop"),
    path("digest/", DigestView.as_view(), name="message-digest"),
    path("monthly/", MonthlyNowView.as_view(), name="message-monthly"),
]
