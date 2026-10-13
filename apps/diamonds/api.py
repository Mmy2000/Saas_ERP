from django.db.models import Q
from django.urls import path, reverse
from django.utils.translation import gettext as _
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.api.idempotency import idempotent
from apps.core.errors import NotFound
from apps.inventory.models import Item, ItemStatus

from . import buyback, certificates, handouts, jobs, parcels, services
from .models import HandoutKind, StoneKind, StoneParcel, StoneShape


class StoneSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=StoneKind.choices, required=False,
                                   default=StoneKind.DIAMOND)
    shape = serializers.ChoiceField(choices=StoneShape.choices, required=False, allow_blank=True,
                                    default="")
    count = serializers.IntegerField(required=False, min_value=1, default=1)
    carat = serializers.CharField(max_length=20, allow_blank=True)
    color = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")
    clarity = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")
    cut = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")
    lab = serializers.CharField(max_length=40, required=False, allow_blank=True, default="")
    certificate_no = serializers.CharField(max_length=60, required=False, allow_blank=True,
                                           default="")
    note = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")
    origin = serializers.CharField(max_length=10, required=False, allow_blank=True, default="")
    setting_type = serializers.CharField(max_length=20, required=False, allow_blank=True,
                                         default="")
    code = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")
    price_per_ct = serializers.CharField(max_length=20, required=False, allow_blank=True,
                                         default="")
    price_currency = serializers.CharField(max_length=3, required=False, allow_blank=True,
                                           default="")


class PieceSerializer(serializers.Serializer):
    category = serializers.IntegerField()
    karat = serializers.IntegerField(required=False, allow_null=True, default=None)
    gross_weight_g = serializers.CharField(max_length=20, required=False, allow_blank=True,
                                           allow_null=True, default=None)
    making_cost_rate = serializers.CharField(max_length=20, required=False, allow_blank=True,
                                             default="0")
    stone_cost = serializers.CharField(max_length=20, required=False, allow_blank=True,
                                       default="0")
    label_price = serializers.CharField(max_length=20, required=False, allow_blank=True,
                                        allow_null=True, default=None)
    stones = StoneSerializer(many=True, required=False, default=list)
    discount_class = serializers.IntegerField(required=False, allow_null=True, default=None)
    model_code = serializers.CharField(max_length=40, required=False, allow_blank=True,
                                       default="")
    model_classes = serializers.ListField(child=serializers.CharField(max_length=20),
                                          required=False, default=list)


class ReceiveSerializer(serializers.Serializer):
    supplier = serializers.IntegerField(allow_null=True)
    branch = serializers.IntegerField()
    currency = serializers.CharField(max_length=3)
    supplier_reference = serializers.CharField(max_length=60, required=False, allow_blank=True,
                                               default="")
    note = serializers.CharField(required=False, allow_blank=True, default="")
    pieces = PieceSerializer(many=True)


class ReceiveView(APIView):
    required_permissions = {"POST": "diamonds.receive"}

    @idempotent
    def post(self, request):
        payload = ReceiveSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        d = payload.validated_data
        invoice = services.receive(services.ReceiveInput(
            supplier_id=d["supplier"] or 0, branch_id=d["branch"], currency_code=d["currency"],
            supplier_reference=d["supplier_reference"], note=d["note"],
            pieces=tuple(services.PieceInput(
                category_id=p["category"], karat_id=p["karat"],
                gross_weight_g=p["gross_weight_g"], making_cost_rate=p["making_cost_rate"] or "0",
                stone_cost=p["stone_cost"] or "0", label_price=p["label_price"] or None,
                stones=tuple(p["stones"]), discount_class_id=p["discount_class"],
                model_code=p["model_code"], model_classes=tuple(p["model_classes"]))
                for p in d["pieces"])), actor=request.actor)
        return Response({"id": invoice.pk, "number": invoice.number},
                        status=status.HTTP_201_CREATED)


class ItemStonesSerializer(serializers.Serializer):
    stones = StoneSerializer(many=True)
    label_price = serializers.CharField(max_length=20, required=False, allow_blank=True,
                                        allow_null=True, default=None)
    discount_class = serializers.IntegerField(required=False, allow_null=True)
    model_code = serializers.CharField(max_length=40, required=False, allow_blank=True)
    model_classes = serializers.ListField(child=serializers.CharField(max_length=20),
                                          required=False)


class ItemStonesView(APIView):
    required_permissions = {"PUT": "diamonds.manage"}

    def put(self, request, pk):
        payload = ItemStonesSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        d = payload.validated_data
        item = services.update_item(pk, stones=d["stones"], label_price=d["label_price"],
                                    discount_class_id=d.get("discount_class",
                                                            services.UNCHANGED),
                                    model_code=d.get("model_code", services.UNCHANGED),
                                    model_classes=d.get("model_classes", services.UNCHANGED),
                                    actor=request.actor)
        return Response({"id": item.pk, "label_price": item.label_price,
                         "stone_weight_ct": item.stone_weight_ct})


class LookupView(APIView):
    """Diamond pieces or loose stones in stock, by barcode or certificate (setting screen)."""

    required_permissions = {"GET": "diamonds.view"}

    def get(self, request):
        services.require_enabled()
        family = request.query_params.get("family", "stone")
        term = request.query_params.get("q", "").strip()
        # "job": anything a setter can be handed (gold or diamond mountings, loose stones).
        families = (("gold", "diamond", "stone") if family == "job" else (family,))
        items = (Item.objects.select_related("category", "karat", "branch")
                 .filter(status=ItemStatus.IN_STOCK, category__product_family__in=families,
                         category__tracking="serialized")
                 .prefetch_related("stones"))
        branch = request.query_params.get("branch", "")
        if branch.isdigit():
            items = items.filter(branch_id=int(branch))
        if term:
            items = items.filter(Q(barcode=term) | Q(stones__certificate_no=term)).distinct()
        found = list(items.order_by("barcode")[:20])
        if term and not found:
            raise NotFound(_("No piece with barcode %(barcode)s.") % {"barcode": term})
        return Response({"results": [{
            "id": item.pk, "barcode": item.barcode, "family": item.category.product_family,
            "name": f"{item.barcode} · {item.category.name}",
            "category": item.category.name, "karat": item.karat.label if item.karat else "",
            "gross_weight_g": item.gross_weight_g, "stone_weight_ct": item.stone_weight_ct,
            "stones": services.stones_summary(item.stones.all()),
            "label_price": item.label_price} for item in found]})


class ParcelUseSerializer(serializers.Serializer):
    parcel = serializers.IntegerField()
    carats = serializers.CharField(max_length=20)
    count = serializers.IntegerField(required=False, min_value=0, default=0)
    held = serializers.BooleanField(required=False, default=False)  # the setter's own stones


def _uses(rows):
    return tuple(parcels.ParcelUse(parcel_id=row["parcel"], carats=row["carats"],
                                   count=row["count"], held=row["held"]) for row in rows)


class SettingSerializer(serializers.Serializer):
    parcels = ParcelUseSerializer(many=True, required=False, default=list)
    piece = serializers.IntegerField()
    stones = serializers.ListField(child=serializers.IntegerField(), allow_empty=True)
    gross_after_g = serializers.CharField(max_length=20, allow_blank=True)
    setter = serializers.IntegerField(required=False, allow_null=True, default=None)
    labour_amount = serializers.CharField(max_length=20, required=False, allow_blank=True,
                                          default="0")
    note = serializers.CharField(required=False, allow_blank=True, default="")


class SettingView(APIView):
    required_permissions = {"POST": "diamonds.setting"}

    @idempotent
    def post(self, request):
        payload = SettingSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        d = payload.validated_data
        setting = services.set_stones(services.SettingInput(
            piece_id=d["piece"], stone_ids=tuple(d["stones"]), gross_after_g=d["gross_after_g"],
            setter_id=d["setter"], labour_amount=d["labour_amount"] or "0", note=d["note"],
            parcel_uses=_uses(d["parcels"])), actor=request.actor)
        return Response({"id": setting.pk, "number": setting.number},
                        status=status.HTTP_201_CREATED)


class SettingVoidView(APIView):
    required_permissions = {"POST": "diamonds.setting"}

    @idempotent
    def post(self, request, pk):
        reason = str(request.data.get("reason") or "")
        setting = services.cancel_setting(pk, reason=reason, actor=request.actor)
        return Response({"id": setting.pk, "status": setting.status})


class JobSerializer(serializers.Serializer):
    branch = serializers.IntegerField()
    setter = serializers.IntegerField(required=False, allow_null=True, default=None)
    items = serializers.ListField(child=serializers.IntegerField(), allow_empty=True)
    note = serializers.CharField(required=False, allow_blank=True, default="")


class JobView(APIView):
    """Hand pieces and loose stones to a setter."""

    required_permissions = {"POST": "diamonds.setting"}

    @idempotent
    def post(self, request):
        payload = JobSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        d = payload.validated_data
        job = jobs.hand_over(jobs.JobInput(branch_id=d["branch"], setter_id=d["setter"] or 0,
                                           item_ids=tuple(d["items"]), note=d["note"]),
                             actor=request.actor)
        return Response({"id": job.pk, "number": job.number}, status=status.HTTP_201_CREATED)


class JobReturnView(APIView):
    required_permissions = {"POST": "diamonds.setting"}

    @idempotent
    def post(self, request, pk):
        lines = [int(x) for x in request.data.get("lines") or [] if str(x).isdigit()]
        job = jobs.return_items(pk, lines, actor=request.actor)
        return Response({"id": job.pk, "closed": job.closed_at is not None})


class JobSetSerializer(serializers.Serializer):
    parcels = ParcelUseSerializer(many=True, required=False, default=list)
    mounting = serializers.IntegerField()
    stones = serializers.ListField(child=serializers.IntegerField(), allow_empty=True)
    gross_after_g = serializers.CharField(max_length=20, allow_blank=True)
    labour_amount = serializers.CharField(max_length=20, required=False, allow_blank=True,
                                          default="0")
    category = serializers.IntegerField(required=False, allow_null=True, default=None)
    label_price = serializers.CharField(max_length=20, required=False, allow_blank=True,
                                        default="")
    note = serializers.CharField(required=False, allow_blank=True, default="")


class JobSetView(APIView):
    """A mounting and stones of a job come back set as one piece."""

    required_permissions = {"POST": "diamonds.setting"}

    @idempotent
    def post(self, request, pk):
        payload = JobSetSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        d = payload.validated_data
        setting = jobs.set_from_job(pk, jobs.SetInput(
            mounting_line_id=d["mounting"], stone_line_ids=tuple(d["stones"]),
            gross_after_g=d["gross_after_g"], labour_amount=d["labour_amount"] or "0",
            category_id=d["category"], label_price=d["label_price"] or None, note=d["note"],
            parcel_uses=_uses(d["parcels"])), actor=request.actor)
        return Response({"id": setting.pk, "number": setting.number},
                        status=status.HTTP_201_CREATED)


class RemovalSerializer(serializers.Serializer):
    stones = serializers.ListField(child=serializers.IntegerField(), allow_empty=True)
    gross_after_g = serializers.CharField(max_length=20, allow_blank=True)
    fee_amount = serializers.CharField(max_length=20, required=False, allow_blank=True,
                                       default="0")
    setter = serializers.IntegerField(required=False, allow_null=True, default=None)
    note = serializers.CharField(required=False, allow_blank=True, default="")


class RemovalView(APIView):
    """Take stones set here out of the piece again."""

    required_permissions = {"POST": "diamonds.setting"}

    @idempotent
    def post(self, request, pk):
        payload = RemovalSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        d = payload.validated_data
        removal = jobs.remove_stones(jobs.RemovalInput(
            setting_id=pk, stone_ids=tuple(d["stones"]), gross_after_g=d["gross_after_g"],
            fee_amount=d["fee_amount"] or "0", setter_id=d["setter"], note=d["note"]),
            actor=request.actor)
        return Response({"id": removal.pk, "number": removal.number},
                        status=status.HTTP_201_CREATED)


class DiscountClassSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=4)
    name = serializers.CharField(max_length=100, allow_blank=True)
    usual_percent = serializers.CharField(max_length=10, required=False, allow_blank=True,
                                          default="0")
    max_percent = serializers.CharField(max_length=10, required=False, allow_blank=True,
                                        default="0")
    is_active = serializers.BooleanField(required=False, default=True)


def _class_input(request):
    payload = DiscountClassSerializer(data=request.data)
    payload.is_valid(raise_exception=True)
    d = payload.validated_data
    return services.DiscountClassInput(code=d["code"], name=d["name"],
                                       usual_percent=d["usual_percent"],
                                       max_percent=d["max_percent"], is_active=d["is_active"])


class DiscountClassesView(APIView):
    required_permissions = {"POST": "diamonds.manage"}

    def post(self, request):
        found = services.save_discount_class(_class_input(request), actor=request.actor)
        return Response({"id": found.pk}, status=status.HTTP_201_CREATED)


class DiscountClassView(APIView):
    required_permissions = {"PUT": "diamonds.manage"}

    def put(self, request, pk):
        found = services.save_discount_class(_class_input(request), class_id=pk,
                                             actor=request.actor)
        return Response({"id": found.pk})


class AssignClassSerializer(serializers.Serializer):
    discount_class = serializers.IntegerField(allow_null=True)
    family = serializers.CharField(max_length=16, required=False, allow_blank=True, default="")
    q = serializers.CharField(max_length=60, required=False, allow_blank=True, default="")
    current = serializers.CharField(max_length=10, required=False, allow_blank=True, default="")


class AssignClassView(APIView):
    """Put every matching diamond piece in stock into a class (legacy GHD42)."""

    required_permissions = {"POST": "diamonds.manage"}

    @idempotent
    def post(self, request):
        payload = AssignClassSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        d = payload.validated_data
        count = services.assign_discount_class(
            d["discount_class"], family=d["family"], term=d["q"].strip(), current=d["current"],
            scope=request.actor.branch_ids("diamonds.manage"), actor=request.actor)
        return Response({"count": count})


class CheckSerializer(serializers.Serializer):
    report_no = serializers.CharField(max_length=80)
    lab = serializers.CharField(max_length=10, required=False, default="IGI")
    refresh = serializers.BooleanField(required=False, default=False)


class CertificateCheckView(APIView):
    """Ask the lab about a report (legacy GHD52); the answer and its PDF are kept."""

    required_permissions = {"POST": "diamonds.view"}

    def post(self, request):
        payload = CheckSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        d = payload.validated_data
        found = certificates.check(d["report_no"], lab=d["lab"], refresh=d["refresh"],
                                   actor=request.actor)
        return Response({
            "id": found.pk, "lab": found.lab, "report_no": found.report_no,
            "status": found.status, "status_label": found.get_status_display(),
            "message": found.message, "checked_at": found.checked_at,
            "pdf_url": reverse("certificate-pdf", args=[found.pk]) if found.pdf else ""})


class BuyBackPieceSerializer(serializers.Serializer):
    category = serializers.IntegerField()
    karat = serializers.IntegerField(required=False, allow_null=True, default=None)
    gross_weight_g = serializers.CharField(max_length=20, required=False, allow_blank=True,
                                           allow_null=True, default=None)
    stones_carat = serializers.CharField(max_length=20, required=False, allow_blank=True,
                                         default="0")
    stones_count = serializers.IntegerField(required=False, min_value=1, default=1)
    stones_note = serializers.CharField(max_length=200, required=False, allow_blank=True,
                                        default="")
    price = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")
    label_price = serializers.CharField(max_length=20, required=False, allow_blank=True,
                                        allow_null=True, default=None)


def _buy_back_pieces(rows):
    return tuple(buyback.PieceInput(
        category_id=row["category"], karat_id=row["karat"], gross_weight_g=row["gross_weight_g"],
        stones_carat=row["stones_carat"] or "0", stones_count=row["stones_count"],
        stones_note=row["stones_note"], price=row["price"], label_price=row["label_price"] or None)
        for row in rows)


class BuyBackSerializer(serializers.Serializer):
    branch = serializers.IntegerField()
    pieces = BuyBackPieceSerializer(many=True)
    payment = serializers.CharField(max_length=16)
    seller = serializers.IntegerField(required=False, allow_null=True, default=None)
    seller_name = serializers.CharField(max_length=200, required=False, allow_blank=True,
                                        default="")
    seller_phone = serializers.CharField(max_length=30, required=False, allow_blank=True,
                                         default="")
    seller_id_number = serializers.CharField(max_length=40, required=False, allow_blank=True,
                                             default="")
    cash_box = serializers.IntegerField(required=False, allow_null=True, default=None)
    bank_account = serializers.IntegerField(required=False, allow_null=True, default=None)
    note = serializers.CharField(required=False, allow_blank=True, default="")


class BuyBackView(APIView):
    """Buy old diamond jewellery or stones from a customer (legacy GHD40)."""

    required_permissions = {"POST": "diamonds.buy_back"}

    @idempotent
    def post(self, request):
        payload = BuyBackSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        d = payload.validated_data
        bought = buyback.buy_back(buyback.BuyBackInput(
            branch_id=d["branch"], pieces=_buy_back_pieces(d["pieces"]), payment=d["payment"],
            seller_id=d["seller"], seller_name=d["seller_name"], seller_phone=d["seller_phone"],
            seller_id_number=d["seller_id_number"], cash_box_id=d["cash_box"],
            bank_account_id=d["bank_account"], note=d["note"]), actor=request.actor)
        return Response({"id": bought.pk, "number": bought.number},
                        status=status.HTTP_201_CREATED)


class BuyBackQuoteView(APIView):
    required_permissions = {"POST": "diamonds.buy_back"}

    def post(self, request):
        payload = BuyBackPieceSerializer(data=request.data.get("pieces") or [], many=True)
        payload.is_valid(raise_exception=True)
        return Response({"pieces": buyback.quote(_buy_back_pieces(payload.validated_data))})


class BuyBackVoidView(APIView):
    required_permissions = {"POST": "diamonds.buy_back"}

    @idempotent
    def post(self, request, pk):
        bought = buyback.void_buy_back(pk, reason=str(request.data.get("reason") or ""),
                                       actor=request.actor)
        return Response({"id": bought.pk, "status": bought.status})


class ParcelSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=20, allow_blank=True)
    branch = serializers.IntegerField()
    kind = serializers.CharField(max_length=12, required=False, default="diamond")
    shape = serializers.CharField(max_length=12, required=False, allow_blank=True, default="")
    size = serializers.CharField(max_length=40, required=False, allow_blank=True, default="")
    color = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")
    clarity = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")
    note = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")
    sale_price_per_ct = serializers.CharField(max_length=20, required=False, allow_blank=True,
                                              allow_null=True, default=None)
    is_active = serializers.BooleanField(required=False, default=True)


def _parcel_input(request):
    payload = ParcelSerializer(data=request.data)
    payload.is_valid(raise_exception=True)
    d = payload.validated_data
    return parcels.ParcelInput(code=d["code"], branch_id=d["branch"], kind=d["kind"],
                               shape=d["shape"], size=d["size"], color=d["color"],
                               clarity=d["clarity"], note=d["note"],
                               sale_price_per_ct=d["sale_price_per_ct"] or None,
                               is_active=d["is_active"])


class ParcelsView(APIView):
    """A branch's parcels with their balance (the setting forms pick from them), or a new one."""

    required_permissions = {"GET": "diamonds.view", "POST": "diamonds.manage"}

    def get(self, request):
        services.require_enabled()
        found = StoneParcel.objects.filter(is_active=True)
        branch = request.query_params.get("branch", "")
        if branch.isdigit():
            found = found.filter(branch_id=int(branch))
        return Response({"results": [{
            "id": parcel.pk, "code": parcel.code, "carats": parcel.carats,
            "count": parcel.count, "cost_per_ct": parcel.cost_per_ct,
            "name": f"{parcel.code} · {parcel.get_kind_display()} {parcel.size}".strip()}
            for parcel in found.order_by("code")]})

    def post(self, request):
        parcel = parcels.save_parcel(_parcel_input(request), actor=request.actor)
        return Response({"id": parcel.pk}, status=status.HTTP_201_CREATED)


class ParcelView(APIView):
    required_permissions = {"PUT": "diamonds.manage"}

    def put(self, request, pk):
        parcel = parcels.save_parcel(_parcel_input(request), parcel_id=pk, actor=request.actor)
        return Response({"id": parcel.pk})


class DealSerializer(serializers.Serializer):
    parcel = serializers.IntegerField()
    kind = serializers.ChoiceField(choices=[("buy", "buy"), ("sell", "sell"),
                                            ("return", "return")])
    carats = serializers.CharField(max_length=20, allow_blank=True)
    count = serializers.IntegerField(required=False, min_value=0, default=0)
    price_per_ct = serializers.CharField(max_length=20, allow_blank=True)
    payment = serializers.CharField(max_length=16)
    party = serializers.IntegerField(required=False, allow_null=True, default=None)
    party_name = serializers.CharField(max_length=200, required=False, allow_blank=True,
                                       default="")
    cash_box = serializers.IntegerField(required=False, allow_null=True, default=None)
    bank_account = serializers.IntegerField(required=False, allow_null=True, default=None)
    note = serializers.CharField(required=False, allow_blank=True, default="")


class DealView(APIView):
    """Stones bought into a parcel, or sold from it, by the carat."""

    required_permissions = {"POST": "diamonds.view"}  # the service checks buy / sell

    @idempotent
    def post(self, request):
        payload = DealSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        d = payload.validated_data
        made = parcels.deal(parcels.DealInput(
            parcel_id=d["parcel"], kind=d["kind"], carats=d["carats"], count=d["count"],
            price_per_ct=d["price_per_ct"], payment=d["payment"], party_id=d["party"],
            party_name=d["party_name"], cash_box_id=d["cash_box"],
            bank_account_id=d["bank_account"], note=d["note"]), actor=request.actor)
        return Response({"id": made.pk, "number": made.number}, status=status.HTTP_201_CREATED)


class DealVoidView(APIView):
    required_permissions = {"POST": "diamonds.view"}

    @idempotent
    def post(self, request, pk):
        made = parcels.void_deal(pk, reason=str(request.data.get("reason") or ""),
                                 actor=request.actor)
        return Response({"id": made.pk, "status": made.status})


class TransferSerializer(serializers.Serializer):
    parcel = serializers.IntegerField()
    to_branch = serializers.IntegerField()
    to_parcel = serializers.IntegerField(required=False, allow_null=True, default=None)
    carats = serializers.CharField(max_length=20, allow_blank=True)
    count = serializers.IntegerField(required=False, min_value=0, default=0)
    note = serializers.CharField(required=False, allow_blank=True, default="")


class TransferView(APIView):
    """Stones moved from a parcel to another branch."""

    required_permissions = {"POST": "diamonds.manage"}

    @idempotent
    def post(self, request):
        payload = TransferSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        d = payload.validated_data
        made = parcels.transfer_parcel(
            d["parcel"], to_branch_id=d["to_branch"], to_parcel_id=d["to_parcel"],
            carats=d["carats"], count=d["count"], note=d["note"], actor=request.actor)
        return Response({"id": made.pk, "number": made.number}, status=status.HTTP_201_CREATED)


class TransferVoidView(APIView):
    required_permissions = {"POST": "diamonds.manage"}

    @idempotent
    def post(self, request, pk):
        made = parcels.void_transfer(pk, reason=str(request.data.get("reason") or ""),
                                     actor=request.actor)
        return Response({"id": made.pk, "status": made.status})


class CountedSerializer(serializers.Serializer):
    parcel = serializers.IntegerField()
    carats = serializers.CharField(max_length=20, allow_blank=True)
    count = serializers.IntegerField(required=False, min_value=0, default=0)


class CountSerializer(serializers.Serializer):
    branch = serializers.IntegerField()
    rows = CountedSerializer(many=True)
    note = serializers.CharField(required=False, allow_blank=True, default="")


class CountView(APIView):
    """A branch's parcels weighed and counted."""

    required_permissions = {"POST": "diamonds.manage"}

    @idempotent
    def post(self, request):
        payload = CountSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        d = payload.validated_data
        made = parcels.count_parcels(d["branch"], [
            parcels.CountedParcel(parcel_id=row["parcel"], carats=row["carats"],
                                  count=row["count"]) for row in d["rows"]],
            note=d["note"], actor=request.actor)
        return Response({"id": made.pk, "number": made.number}, status=status.HTTP_201_CREATED)


class CountVoidView(APIView):
    required_permissions = {"POST": "diamonds.manage"}

    @idempotent
    def post(self, request, pk):
        made = parcels.void_count(pk, reason=str(request.data.get("reason") or ""),
                                  actor=request.actor)
        return Response({"id": made.pk, "status": made.status})


class HandoutSerializer(serializers.Serializer):
    workshop = serializers.IntegerField()
    parcel = serializers.IntegerField()
    kind = serializers.ChoiceField(choices=HandoutKind.choices)
    carats = serializers.CharField(max_length=20, allow_blank=True)
    count = serializers.IntegerField(required=False, min_value=0, default=0)
    note = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")


class HandoutView(APIView):
    """Stones handed to a workshop from a parcel, taken back, or written off."""

    required_permissions = {"POST": "diamonds.handout"}

    @idempotent
    def post(self, request):
        payload = HandoutSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        d = payload.validated_data
        made = handouts.handout(handouts.HandoutInput(
            workshop_id=d["workshop"], parcel_id=d["parcel"], kind=d["kind"],
            carats=d["carats"], count=d["count"], note=d["note"]), actor=request.actor)
        return Response({"id": made.pk, "number": made.number}, status=status.HTTP_201_CREATED)


class HandoutVoidView(APIView):
    required_permissions = {"POST": "diamonds.handout"}

    @idempotent
    def post(self, request, pk):
        made = handouts.void_handout(pk, reason=str(request.data.get("reason") or ""),
                                     actor=request.actor)
        return Response({"id": made.pk, "status": made.status})


class HeldView(APIView):
    """The stones a workshop holds, by parcel (?workshop=…&branch=…): what a setting can take
    from the setter's own stones."""

    required_permissions = {"GET": "diamonds.view"}

    def get(self, request):
        services.require_enabled()
        rows = handouts.holdings()
        workshop = request.query_params.get("workshop", "")
        branch = request.query_params.get("branch", "")
        if workshop.isdigit():
            rows = rows.filter(workshop_id=int(workshop))
        if branch.isdigit():
            rows = rows.filter(parcel__branch_id=int(branch))
        return Response({"results": [{
            "id": row.parcel_id, "code": row.parcel.code, "carats": row.carats,
            "count": row.count, "cost_per_ct": row.cost_per_ct, "workshop": row.workshop_id,
            "name": f"{row.parcel.code} · {row.parcel.get_kind_display()} {row.parcel.size}"
                    .strip()} for row in rows]})


urlpatterns = [
    path("handouts/", HandoutView.as_view(), name="diamonds-handouts"),
    path("handouts/<int:pk>/void/", HandoutVoidView.as_view(), name="diamonds-handout-void"),
    path("held/", HeldView.as_view(), name="diamonds-held"),
    path("receive/", ReceiveView.as_view(), name="diamonds-receive"),
    path("items/<int:pk>/stones/", ItemStonesView.as_view(), name="diamonds-item-stones"),
    path("lookup/", LookupView.as_view(), name="diamonds-lookup"),
    path("settings/", SettingView.as_view(), name="diamonds-settings"),
    path("settings/<int:pk>/void/", SettingVoidView.as_view(), name="diamonds-setting-void"),
    path("settings/<int:pk>/remove/", RemovalView.as_view(), name="diamonds-setting-remove"),
    path("jobs/", JobView.as_view(), name="diamonds-jobs"),
    path("parcels/", ParcelsView.as_view(), name="diamonds-parcels"),
    path("parcels/<int:pk>/", ParcelView.as_view(), name="diamonds-parcel"),
    path("parcels/deals/", DealView.as_view(), name="diamonds-parcel-deals"),
    path("parcels/deals/<int:pk>/void/", DealVoidView.as_view(),
         name="diamonds-parcel-deal-void"),
    path("parcels/transfers/", TransferView.as_view(), name="diamonds-parcel-transfers"),
    path("parcels/transfers/<int:pk>/void/", TransferVoidView.as_view(),
         name="diamonds-parcel-transfer-void"),
    path("parcels/counts/", CountView.as_view(), name="diamonds-parcel-counts"),
    path("parcels/counts/<int:pk>/void/", CountVoidView.as_view(),
         name="diamonds-parcel-count-void"),
    path("buy-back/", BuyBackView.as_view(), name="diamonds-buy-back"),
    path("buy-back/quote/", BuyBackQuoteView.as_view(), name="diamonds-buy-back-quote"),
    path("buy-back/<int:pk>/void/", BuyBackVoidView.as_view(), name="diamonds-buy-back-void"),
    path("certificates/check/", CertificateCheckView.as_view(), name="certificate-check"),
    path("discount-classes/", DiscountClassesView.as_view(), name="diamonds-discount-classes"),
    path("discount-classes/assign/", AssignClassView.as_view(), name="diamonds-class-assign"),
    path("discount-classes/<int:pk>/", DiscountClassView.as_view(),
         name="diamonds-discount-class"),
    path("jobs/<int:pk>/return/", JobReturnView.as_view(), name="diamonds-job-return"),
    path("jobs/<int:pk>/set/", JobSetView.as_view(), name="diamonds-job-set"),
]
