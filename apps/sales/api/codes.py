"""One-time codes for confirming sales (apps.sales.codes)."""

from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.sales import codes


class RequestSerializer(serializers.Serializer):
    branch = serializers.IntegerField()
    amount = serializers.CharField(max_length=30, required=False, allow_blank=True, default="0")
    customer = serializers.IntegerField(required=False, allow_null=True, default=None)
    phone = serializers.CharField(max_length=30, required=False, allow_blank=True, default="")
    reservation = serializers.IntegerField(required=False, allow_null=True, default=None)


class VerifySerializer(serializers.Serializer):
    code = serializers.CharField(max_length=12)


class RulesSerializer(serializers.Serializer):
    on_account = serializers.BooleanField(default=False)
    on_reservations = serializers.BooleanField(default=False)
    above_amount = serializers.CharField(max_length=30, required=False, allow_blank=True,
                                         allow_null=True, default=None)


def _shape(request, sale_code):
    return {"id": sale_code.pk, "phone": codes.masked(sale_code.phone),
            "amount": sale_code.amount, "channel": sale_code.channel,
            "expires_at": sale_code.expires_at, "verified": sale_code.verified_at is not None,
            "sends_left": codes.MAX_SENDS - sale_code.sends,
            "can_skip": request.actor.can("sales.code.skip", branch=sale_code.branch)}


class CodeRequestView(APIView):
    """Send a code for this sale (or reservation pick-up) to the customer's phone."""

    required_permissions = {"POST": "sales.invoice.create"}

    def post(self, request):
        payload = RequestSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        d = payload.validated_data
        sale_code = codes.request_code(branch_id=d["branch"], amount=d["amount"] or "0",
                                       customer_id=d["customer"], phone=d["phone"],
                                       reservation_id=d["reservation"], actor=request.actor)
        return Response(_shape(request, sale_code), status=201)


class CodeResendView(APIView):
    required_permissions = {"POST": "sales.invoice.create"}

    def post(self, request, pk):
        return Response(_shape(request, codes.resend(pk, actor=request.actor)))


class CodeVerifyView(APIView):
    required_permissions = {"POST": "sales.invoice.create"}

    def post(self, request, pk):
        payload = VerifySerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        sale_code = codes.verify(pk, payload.validated_data["code"], actor=request.actor)
        return Response(_shape(request, sale_code))


class CodeRulesView(APIView):
    required_permissions = {"PUT": "sales.code.manage"}

    def put(self, request):
        payload = RulesSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        d = payload.validated_data
        saved = codes.save_rules(on_account=d["on_account"], on_reservations=d["on_reservations"],
                                 above_amount=d["above_amount"], actor=request.actor)
        return Response({"on_account": saved.on_account,
                         "on_reservations": saved.on_reservations,
                         "above_amount": saved.above_amount})
