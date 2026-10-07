from collections import defaultdict
from decimal import Decimal

from django.db import transaction
from django.urls import path, reverse
from django.utils.translation import get_language
from django.utils.translation import gettext as _
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.api.idempotency import idempotent
from apps.core.errors import ValidationError

from . import imports, services


class PartySerializer(serializers.Serializer):
    role = serializers.CharField(max_length=20)
    party = serializers.IntegerField()
    amount = serializers.CharField(max_length=30)
    owes_us = serializers.BooleanField()
    currency = serializers.CharField(max_length=3, required=False, allow_blank=True, default="")
    karat = serializers.IntegerField(required=False, allow_null=True, default=None)


class HolderSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=("box", "bank"))
    holder = serializers.IntegerField()
    amount = serializers.CharField(max_length=30)


class StockSerializer(serializers.Serializer):
    category = serializers.IntegerField()
    karat = serializers.IntegerField(required=False, allow_null=True, default=None)
    gross_weight_g = serializers.CharField(max_length=30)
    qty = serializers.IntegerField(required=False, default=0)
    cost_amount = serializers.CharField(max_length=30, required=False, allow_blank=True,
                                        default="0")


class OpeningSerializer(serializers.Serializer):
    branch = serializers.IntegerField()
    business_date = serializers.DateField(required=False, allow_null=True, default=None)
    note = serializers.CharField(required=False, allow_blank=True, default="")
    parties = PartySerializer(many=True, required=False, default=list)
    holders = HolderSerializer(many=True, required=False, default=list)
    stock = StockSerializer(many=True, required=False, default=list)


def _created(doc) -> Response:
    return Response({"id": doc.pk, "number": doc.number,
                     "url": reverse("opening-balance", args=[doc.pk])}, status=201)


class OpeningView(APIView):
    required_permissions = {"POST": "ledger.opening.post"}

    @idempotent
    def post(self, request):
        serializer = OpeningSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        doc = services.post_opening(services.OpeningInput(
            branch_id=data["branch"], business_date=data["business_date"], note=data["note"],
            parties=[services.PartyBalance(role=p["role"], party_id=p["party"],
                                           amount=p["amount"], owes_us=p["owes_us"],
                                           currency_code=p["currency"], karat_id=p["karat"])
                     for p in data["parties"]],
            holders=[services.HolderBalance(kind=h["kind"], holder_id=h["holder"],
                                            amount=h["amount"]) for h in data["holders"]],
            stock=[services.StockBalance(category_id=s["category"], karat_id=s["karat"],
                                         gross_weight_g=s["gross_weight_g"], qty=s["qty"],
                                         cost_amount=s["cost_amount"] or "0")
                   for s in data["stock"]],
        ), actor=request.actor)
        return _created(doc)


class ImportSerializer(serializers.Serializer):
    file = serializers.FileField()
    branch = serializers.IntegerField()
    business_date = serializers.DateField(required=False, allow_null=True, default=None)
    note = serializers.CharField(required=False, allow_blank=True, default="")
    dry_run = serializers.BooleanField(required=False, default=True)


class _Rollback(Exception):
    pass


class ImportView(APIView):
    """Opening stock from Excel. `dry_run` (the default) checks every row exactly as posting
    would, then rolls back; the shop imports once the file is clean."""

    required_permissions = {"POST": "ledger.opening.post"}

    @idempotent
    def post(self, request):
        serializer = ImportSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if data["file"].size > 10 * 1024 * 1024:
            raise ValidationError(_("The file is larger than 10 MB."))
        try:
            rows = imports.parse(data["file"])
        except imports.ImportError_ as exc:
            raise ValidationError(str(exc), fields={"file": [str(exc)]}) from exc
        problems = [{"line": row.line, "column": column, "message": str(message)}
                    for row in rows for column, message in row.errors.items()]
        if not rows:
            raise ValidationError(_("The file has no pieces."), fields={"file": [
                _("The file has no pieces.")]})
        # The rows that read well are checked against the books too (barcodes already used…),
        # so every problem in the file is listed in one go.
        readable = [row for row in rows if row.piece is not None]
        if readable:
            opening = services.OpeningInput(branch_id=data["branch"],
                                            business_date=data["business_date"], note=data["note"],
                                            pieces=[row.piece for row in readable])
            try:
                with transaction.atomic():
                    doc = services.post_opening(opening, actor=request.actor)
                    if data["dry_run"] or problems:
                        raise _Rollback
                return _created(doc)
            except _Rollback:
                pass
            except ValidationError as exc:
                if not any(key.startswith("pieces.") for key in exc.fields):
                    raise
                for key, messages in exc.fields.items():
                    parts = key.split(".")
                    if parts[0] == "pieces" and len(parts) == 3:
                        problems += [{"line": readable[int(parts[1])].line, "column": parts[2],
                                      "message": message} for message in messages]
        if problems:
            arabic = (get_language() or "").startswith("ar")
            names = {key: (arabic_name if arabic else english)
                     for key, english, arabic_name in imports.TEMPLATE_HEADERS}
            for problem in problems:
                problem["column"] = names.get(problem["column"], problem["column"])
            return Response({"ok": False, "rows": len(rows),
                             "problems": sorted(problems, key=lambda p: p["line"])[:500],
                             "problem_count": len(problems)}, status=200)
        return Response({"ok": True, "rows": len(rows), **_summary(rows)})


def _summary(rows) -> dict:
    by_karat: dict[str, list] = defaultdict(lambda: [0, Decimal(0)])
    cost = Decimal(0)
    for row in rows:
        label = row.values.get("karat") or "—"
        by_karat[label][0] += 1
        by_karat[label][1] += Decimal(str(row.piece.gross_weight_g or 0))
        cost += Decimal(str(row.piece.cost_amount or 0))
    return {"by_karat": [{"karat": k, "pieces": n, "weight": f"{w:.3f}"}
                         for k, (n, w) in sorted(by_karat.items())],
            "making_cost": f"{cost:.2f}"}


class PartyLookupView(APIView):
    """Parties holding a role, by name, code or phone (for the opening balances form)."""

    required_permissions = {"GET": "ledger.opening.post"}

    def get(self, request):
        from apps.parties.selectors import parties_with_role, search

        role = request.query_params.get("role", "")
        if role not in services.ROLE_ACCOUNTS:
            return Response({"results": []})
        parties = search(parties_with_role(role).filter(is_active=True),
                         request.query_params.get("q", ""))
        return Response({"results": [{"id": p.pk, "name": f"{p.name} · {p.code}" if p.code
                                      else p.name} for p in parties.order_by("name")[:20]]})


class VoidSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")


class VoidView(APIView):
    required_permissions = {"POST": "ledger.opening.void"}

    def post(self, request, pk):
        serializer = VoidSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        doc = services.void_opening(pk, reason=serializer.validated_data["reason"],
                                    actor=request.actor)
        return Response({"id": doc.pk, "status": doc.status})


urlpatterns = [
    path("", OpeningView.as_view(), name="opening-create"),
    path("import/", ImportView.as_view(), name="opening-import-api"),
    path("parties/", PartyLookupView.as_view(), name="opening-parties"),
    path("<int:pk>/void/", VoidView.as_view(), name="opening-void"),
]
