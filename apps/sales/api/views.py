from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.api.idempotency import idempotent
from apps.sales import codes, services, walkins
from apps.sales.models import SalesInvoice

from .serializers import InvoiceSerializer, SaleSerializer, VoidSerializer


def sale_input(data) -> services.SaleInput:
    return services.SaleInput(
        branch_id=data["branch"],
        lines=tuple(services.SaleLineInput(item_id=ln["item"], barcode=ln["barcode"],
                                           discount_rate=ln["discount_rate"],
                                           category_id=ln["category"], karat_id=ln["karat"],
                                           gross_weight_g=ln["gross_weight_g"], qty=ln["qty"])
                    for ln in data["lines"]),
        trade_ins=tuple(services.TradeInInput(karat_id=t["karat"],
                                              gross_weight_g=t["gross_weight_g"],
                                              loss_weight_g=t["loss_weight_g"],
                                              price_override=t["price_override"])
                        for t in data["trade_ins"]),
        payments=tuple(services.PaymentInput(kind=p["kind"], currency_code=p["currency"],
                                             amount=p["amount"], reference=p["reference"],
                                             cash_box_id=p["cash_box"],
                                             bank_account_id=p["bank_account"],
                                             terminal_id=p["terminal"])
                       for p in data["payments"]),
        payment_terms=data["payment_terms"], customer_id=data["customer"],
        customer_name=data["customer_name"], customer_phone=data["customer_phone"],
        note=data["note"], code_id=data.get("code"), skip_code=data.get("skip_code", False),
    )


def _parse(request) -> services.SaleInput:
    payload = SaleSerializer(data=request.data)
    payload.is_valid(raise_exception=True)
    return sale_input(payload.validated_data)


class QuoteView(APIView):
    """Live pricing for the sales screen. Never writes; posting recomputes the same numbers."""

    required_permissions = {"POST": "sales.invoice.create"}

    def post(self, request):
        data = _parse(request)
        quote = services.quote_sale(data, actor=request.actor)
        need = services.code_need(quote, data)
        return Response({
            "code": {"needed": need.needed, "reason": need.reason,
                     "phone": codes.masked(need.phone) if need.phone else "",
                     "can_skip": need.needed and request.actor.can("sales.code.skip",
                                                                   branch=quote.branch)},
            "board": quote.board.pk,
            "max_discount_rate": request.actor.limit(services.DISCOUNT_LIMIT),
            "max_diamond_discount_rate": request.actor.limit(services.DIAMOND_DISCOUNT_LIMIT),
            "lines": [{
                "item": ln.item_id, "lot": ln.lot_id, "qty": ln.qty,
                "barcode": ln.barcode, "description": ln.description,
                "karat": ln.karat_label, "gross_weight_g": ln.gross_weight_g,
                "metal_price_per_g": ln.metal_price_per_g, "making_rate": ln.making_rate,
                "discount_rate": ln.discount_rate, "making_rate_net": ln.making_rate_net,
                "line_total": ln.line_total, "at_cost_floor": ln.at_cost_floor,
                "label_price": ln.label_price, "stones_amount": ln.stones_amount,
                "offer": ln.offer_name, "offer_saving": ln.offer_saving,
                "cashback": ln.cashback, "cashback_offer": ln.cashback_name,
            } for ln in quote.lines],
            "trade_ins": [{
                "karat": t.karat_label, "gross_weight_g": t.gross_weight_g,
                "loss_weight_g": t.loss_weight_g, "net_weight_g": t.net_weight_g,
                "price_per_g": t.price_per_g, "amount": t.amount,
            } for _k, t in quote.trade_ins],
            "payments": [{"kind": p.kind, "currency": p.currency.code, "amount": p.amount,
                          "fx_rate": p.fx_rate, "functional_amount": p.functional_amount}
                         for p in quote.payments],
            "totals": {"subtotal": quote.subtotal, "discount": quote.discount,
                       "total": quote.total, "trade_in": quote.trade_in, "due": quote.due,
                       "paid": quote.paid, "remaining": quote.remaining,
                       "change": quote.change, "balance": quote.balance,
                       "offer_saving": quote.offer_saving, "cashback": quote.cashback},
            "problems": [{"code": code, "message": services.PROBLEM_MESSAGES[code]()}
                         for code in quote.problems],
        })


class SalesInvoiceViewSet(viewsets.GenericViewSet):
    queryset = (SalesInvoice.objects.select_related("customer")
                .prefetch_related("lines__item", "lines__karat__metal", "trade_ins__karat__metal",
                                  "payments__currency"))
    serializer_class = InvoiceSerializer
    filterset_fields = {"status": ["exact"], "customer": ["exact"],
                        "business_date": ["gte", "lte"]}
    required_permissions = {
        "list": "sales.invoice.view",
        "retrieve": "sales.invoice.view",
        "create": "sales.invoice.create",
        "void": "sales.invoice.void",
        "make_customer": "parties.customer.create",
    }

    def get_queryset(self):
        queryset = super().get_queryset()
        branches = self.request.actor.branch_ids("sales.invoice.view")
        return queryset if branches is None else queryset.filter(branch_id__in=branches)

    def list(self, request):
        page = self.paginate_queryset(self.filter_queryset(self.get_queryset()))
        return self.get_paginated_response(self.get_serializer(page, many=True).data)

    def retrieve(self, request, pk=None):
        return Response(self.get_serializer(self.get_object()).data)

    @idempotent
    def create(self, request):
        invoice = services.post_sale(_parse(request), actor=request.actor)
        return Response(self.get_serializer(self.get_queryset().get(pk=invoice.pk)).data,
                        status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    @idempotent
    def void(self, request, pk=None):
        payload = VoidSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        invoice = services.void_sale(int(pk), reason=payload.validated_data["reason"],
                                     actor=request.actor)
        return Response(self.get_serializer(self.get_queryset().get(pk=invoice.pk)).data)

    @action(detail=True, methods=["post"], url_path="make-customer")
    @idempotent
    def make_customer(self, request, pk=None):
        """A walk-in buyer becomes a customer (and their walk-in sales with the same phone)."""
        invoice = self.get_object()
        party, linked = walkins.make_customer(invoice.pk, name=str(request.data.get("name") or ""),
                                              actor=request.actor)
        return Response({"customer": party.pk, "name": party.name, "linked": linked,
                         "url": f"/customers/{party.pk}/"})
