"""Printed documents (invoices, receipts, vouchers…) with a design per client.

Every document type turns its record into one plain, normalised shape (`Doc`: company, title,
number, meta lines, party, columns + rows, extra sections, totals, signatures). A layout,
built-in (templates/printing/documents/), the drag-and-drop designer (builder.py) or a custom
HTML written by platform staff, renders that shape. So any design prints any document type, and
a design only ever sees plain data: no models, no queries, nothing of another client.

Adding a document type: a DocumentType below (columns, a `build(record, design, ctx)`, a
`sample(design, company)` for previews, the permission and how to find a record), and a print
link to `print-document` on its page.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from decimal import Decimal

from django.utils import translation
from django.utils.formats import date_format
from django.utils.translation import gettext_lazy as _

ZERO = Decimal(0)
D = Decimal
SAMPLE_DATE = "04/10/2026 14:35"


# ---- language and numbers ------------------------------------------------------------------

def _labels(pairs: dict, language: str) -> dict[str, str]:
    """{key: lazy text} → {key: text} in the document's language; "both" gives "عربي · English"."""
    def render(lang):
        with translation.override(lang):
            return {key: str(text) for key, text in pairs.items()}

    if language == "both":
        ar, en = render("ar"), render("en")
        # The English part is a bidi isolate (FSI…PDI), so its punctuation stays put in RTL.
        return {key: ar[key] if ar[key] == en[key] else f"{ar[key]} · ⁨{en[key]}⁩"
                for key in pairs}
    return render(language)


def _t(text, language: str) -> str:
    return _labels({"x": text}, language)["x"]


def _num(value, places=2) -> str:
    from apps.core.templatetags.ui import num

    return str(num(value, places))


def _lang_of(design) -> str:
    """The language model labels (choices, karat names) are read in."""
    return design.language if design.language != "both" else "ar"


# ---- structure -----------------------------------------------------------------------------

@dataclass(frozen=True)
class Column:
    key: str
    label: object
    align: str = "start"  # start | end
    default: bool = True
    fixed: bool = False  # always printed
    fmt: str = "text"  # text | money | w3 (grams) | w4 (fine grams) | int
    cost: bool = False  # printed only for people who may see costs


@dataclass
class Doc:
    """The normalised document every design renders."""

    title: str
    number: str
    date: str
    company: dict
    labels: dict
    meta: list = field(default_factory=list)  # [(label, value)]
    party: list = field(default_factory=list)  # [(label, value)]
    columns: list = field(default_factory=list)  # [{"key", "label", "align"}]
    rows: list = field(default_factory=list)  # [{"cells": [{"value", "align"}], …}]
    sections: list = field(default_factory=list)  # [{"title", "lines": [(label, value)]}]
    totals: list = field(default_factory=list)  # [{"label", "value", "strong"}]
    signatures: tuple = ("", "")
    status: str = ""  # e.g. "Cancelled" stamp
    currency: str = ""
    lang: str = "ar"
    dir: str = "rtl"


@dataclass(frozen=True)
class DocumentType:
    key: str
    label: object
    group: object
    columns: tuple[Column, ...]
    builder: object  # (record, design, ctx) -> Doc
    sampler: object  # (design, company) -> Doc
    permission: str = ""
    finder: object = None  # (request) -> queryset of the records this user may see
    options: tuple[tuple[str, object, bool], ...] = (
        ("show_signatures", _("Signature lines"), True),)  # (key, label, default on)
    signatures: tuple = (_("Signature"), _("Customer's signature"))

    def default_columns(self) -> list[str]:
        return [c.key for c in self.columns if c.default or c.fixed]


COMMON_LABELS = {
    "number": _("No."), "date": _("Date"), "branch": _("Branch"), "customer": _("Customer"),
    "phone": _("Phone"), "total": _("Total"), "tax_no": _("Tax no."),
    "cancelled": _("Cancelled"), "g": _("g"), "page": _("Page"),
    "walk_in": _("Walk-in customer"), "by_weight": _("By weight"), "pieces": _("pcs"),
    "signature": _("Signature"), "customer_signature": _("Customer's signature"),
}
FORMATS = {"money": 2, "w3": 3, "w4": 4}


def _cell(value, fmt) -> str:
    if value is None or value == "":
        return "—"
    if fmt in FORMATS:
        return _num(value, FORMATS[fmt])
    return str(value)


def make_doc(kind: DocumentType, design, *, company: dict, number, date, status="",
             title=None, meta=(), party=(), rows=(), sections=(), totals=(), currency="",
             can_cost=True) -> Doc:
    """One Doc from plain values. Labels may be lazy text: they come out in the document's
    language. Empty meta/party/total values are left out."""
    language = design.language
    labels = _labels(COMMON_LABELS, language)
    enabled = design.enabled_columns(kind.key)
    columns = [c for c in kind.columns if c.key in enabled and (can_cost or not c.cost)]
    column_labels = _labels({c.key: c.label for c in columns}, language)
    doc = Doc(title=_t(title or kind.label, language), number=number or "—", date=date,
              company=company, labels=labels, currency=currency,
              status=labels["cancelled"] if status == "voided" else "")
    doc.meta = [(labels["number"], doc.number), (labels["date"], date)]
    if company.get("branch"):
        doc.meta.append((labels["branch"], company["branch"]))
    doc.meta += [(_t(label, language), value) for label, value in meta if value not in (None, "")]
    doc.party = [(_t(label, language), value) for label, value in party
                 if value not in (None, "")]
    doc.columns = [{"key": c.key, "label": column_labels[c.key], "align": c.align}
                   for c in columns]
    for raw in rows:
        doc.rows.append({
            "cells": [{"value": _cell(raw.get(c.key), c.fmt), "align": c.align} for c in columns],
            # Plain text values too, for custom designs: row.piece, row.description…
            **{key: _cell(value, next((c.fmt for c in kind.columns if c.key == key), "text"))
               for key, value in raw.items()},
        })
    doc.sections = [{"title": _t(name, language), "lines": lines} for name, lines in sections
                    if lines]
    doc.totals = [{"label": _t(label, language), "value": value, "strong": strong}
                  for label, value, strong in totals if value not in (None, "")]
    doc.signatures = tuple(_t(text, language) for text in kind.signatures)
    return doc


def _company(profile, branch_name="") -> dict:
    return {
        "name": profile.display_name if profile else "",
        "legal_name": getattr(profile, "legal_name", "") or "",
        "phone": getattr(profile, "phone", "") or "",
        "whatsapp": getattr(profile, "whatsapp_number", "") or "",
        "address": getattr(profile, "address", "") or "",
        "tax_no": getattr(profile, "tax_registration_no", "") or "",
        "logo": profile.logo.url if profile is not None and profile.logo else "",
        "branch": branch_name,
    }


def _record_company(branch) -> dict:
    from apps.org.models import TenantProfile

    return _company(TenantProfile.objects.first(), branch.name if branch else "")


def _when(record) -> str:
    moment = getattr(record, "posted_at", None) or getattr(record, "created_at", None)
    return date_format(moment, "SHORT_DATETIME_FORMAT") if moment else ""


def _day(value) -> str:
    return date_format(value, "SHORT_DATE_FORMAT") if value else ""


def _home() -> str:
    from apps.pricing.selectors import functional_currency

    return functional_currency()


BY_WEIGHT = _("By weight")


def _item(line, language, *, bulk=BY_WEIGHT) -> str:
    """Barcode · category for a piece, category · "by weight" for bulk."""
    if getattr(line, "item_id", None):
        return f"{line.item.barcode} · {line.category.name}"
    return f"{line.category.name} · {_t(bulk, language)}"


def _note(title, text):
    return [(title, [(text, "")])] if text else []


# ---- sales invoice -------------------------------------------------------------------------

SALES_COLUMNS = (
    Column("piece", _("Piece"), fixed=True),
    Column("description", _("Description")),
    Column("karat", _("Karat")),
    Column("weight", _("Weight (g)"), "end", fmt="w3"),
    Column("gold_price", _("Gold / g"), "end", fmt="money"),
    Column("making", _("Making / g"), "end", fmt="money"),
    Column("discount", _("Discount"), "end", default=False, fmt="money"),
    Column("total", _("Total"), "end", fixed=True, fmt="money"),
)
SALES_OPTIONS = (
    ("show_seller", _("Show who sold it"), True),
    ("show_payments", _("Show payments"), True),
    ("show_trade_ins", _("Show scrap gold received"), True),
    ("show_signatures", _("Signature lines"), False),
)


def _sales_doc(design, *, company, number, date, status, customer, phone, seller, payment,
               lines, trade_ins, payments, discount, total, trade_in, change, balance, currency):
    kind = TYPES["sales_invoice"]
    lang = design.language
    party = [(_("Customer"), customer or _t(_("Walk-in customer"), lang)), (_("Phone"), phone),
             (_("Payment"), payment)]
    if design.option(kind.key, "show_seller"):
        party.append((_("Sold by"), seller))
    sections = []
    if trade_ins and design.option(kind.key, "show_trade_ins"):
        g = _t(_("g"), lang)
        sections.append((_("Scrap gold received"), [
            (f"{t['karat']} · {_num(t['weight'], 3)} {g} × {_num(t['price'])}", _num(t["amount"]))
            for t in trade_ins]))
    totals = []
    if discount:
        totals.append((_("Discount"), f"−{_num(discount)}", False))
    totals.append((_("Total"), f"{_num(total)} {currency}", True))
    if trade_in:
        totals.append((_("Scrap trade-in"), f"−{_num(trade_in)}", False))
    if design.option(kind.key, "show_payments"):
        totals += [(p["label"], _num(p["amount"]), False) for p in payments]
    if change:
        totals.append((_("Change given"), _num(change), True))
    if balance:
        totals.append((_("On the customer's account"), _num(balance), True))
    rows = [{**line, "discount": line["discount"] or None} for line in lines]
    return make_doc(kind, design, company=company, number=number, date=date, status=status,
                    party=party, rows=rows, sections=sections, totals=totals, currency=currency)


def build_sales_invoice(invoice, design, ctx=None):
    lang = design.language
    with translation.override(_lang_of(design)):
        lines = [{"piece": line.item.barcode if line.item_id else (
                      _t(_("By weight"), lang)
                      + (f" · {line.qty} {_t(_('pcs'), lang)}" if line.qty else "")),
                  "description": line.category.name, "karat": line.karat.label,
                  "weight": line.gross_weight_g, "gold_price": line.metal_price_per_g,
                  "making": line.making_rate_net, "discount": line.discount_amount,
                  "total": line.line_total}
                 for line in invoice.lines.select_related("item", "category", "karat__metal")]
        trade_ins = [{"karat": t.karat.label, "weight": t.net_weight_g, "price": t.price_per_g,
                      "amount": t.amount}
                     for t in invoice.trade_ins.select_related("karat__metal")]
        home = _home()
        payments = [{"label": p.get_kind_display() + (
                        f" ({p.currency.code} {_num(p.amount)})" if p.currency.code != home
                        else ""), "amount": p.functional_amount}
                    for p in invoice.payments.select_related("currency")]
        payment = invoice.get_payment_terms_display()
    return _sales_doc(
        design, company=_record_company(invoice.branch), number=invoice.number,
        date=_when(invoice), status=invoice.status,
        customer=str(invoice.buyer) if invoice.buyer else "", phone=invoice.customer_phone,
        seller=invoice.sold_by.display_name if invoice.sold_by_id else "", payment=payment,
        lines=lines, trade_ins=trade_ins, payments=payments, discount=invoice.discount_amount,
        total=invoice.total_amount, trade_in=invoice.trade_in_amount,
        change=invoice.change_amount, balance=invoice.balance_amount, currency=home)


def sample_sales_invoice(design, company):
    cash = _t(_("Cash"), design.language)
    lines = [
        {"piece": "1010000041", "description": "خاتم سوليتير", "karat": "21K",
         "weight": D("4.250"), "gold_price": D("4560"), "making": D("150"),
         "discount": ZERO, "total": D("20017.50")},
        {"piece": "1020000007", "description": "سلسلة كارتير", "karat": "18K",
         "weight": D("7.800"), "gold_price": D("3910"), "making": D("120"),
         "discount": D("80"), "total": D("31354.00")},
        {"piece": _t(_("By weight"), design.language), "description": "سبيكة", "karat": "24K",
         "weight": D("10.000"), "gold_price": D("5210"), "making": D("40"),
         "discount": ZERO, "total": D("52500.00")},
    ]
    return _sales_doc(
        design, company=company, number="S-2026-000123", date=SAMPLE_DATE, status="posted",
        customer="سارة عادل", phone="01001234567", seller="Ahmed", payment=cash, lines=lines,
        trade_ins=[{"karat": "21K", "weight": D("3.100"), "price": D("4400"),
                    "amount": D("13640.00")}],
        payments=[{"label": cash, "amount": D("90231.50")}], discount=ZERO,
        total=D("103871.50"), trade_in=D("13640.00"), change=ZERO, balance=ZERO,
        currency="EGP")


# ---- customer return -----------------------------------------------------------------------

RETURN_COLUMNS = (
    Column("piece", _("Piece"), fixed=True), Column("description", _("Description")),
    Column("karat", _("Karat")), Column("weight", _("Weight (g)"), "end", fmt="w3"),
    Column("total", _("Total"), "end", fixed=True, fmt="money"),
)


def build_sales_return(doc, design, ctx=None):
    home = _home()
    with translation.override(_lang_of(design)):
        rows = [{"piece": o.item.barcode if o.item_id else _t(_("By weight"), design.language),
                 "description": o.category.name, "karat": o.karat.label,
                 "weight": o.gross_weight_g, "total": o.line_total}
                for o in (line.original_line for line in doc.lines.select_related(
                    "original_line__item", "original_line__category",
                    "original_line__karat"))]
        refund = doc.get_refund_method_display()
    totals = [(_("Returned value"), _num(doc.returned_amount), False)]
    if doc.deduction_amount:
        totals.append((_("Deduction"), f"−{_num(doc.deduction_amount)}", False))
    totals.append((_("Refund"), f"{_num(doc.refund_amount)} {home} · {refund}", True))
    return make_doc(TYPES["sales_return"], design, company=_record_company(doc.branch),
                    number=doc.number, date=_when(doc), status=doc.status,
                    meta=[(_("Sale"), doc.original_invoice.number)],
                    party=[(_("Customer"), doc.customer.name if doc.customer_id else "")],
                    rows=rows, totals=totals, currency=home, sections=_note(_("Reason"),
                                                                             doc.reason))


def sample_sales_return(design, company):
    return make_doc(
        TYPES["sales_return"], design, company=company, number="SR-2026-000017",
        date=SAMPLE_DATE, meta=[(_("Sale"), "S-2026-000123")],
        party=[(_("Customer"), "سارة عادل")],
        rows=[{"piece": "1010000041", "description": "خاتم سوليتير", "karat": "21K",
               "weight": D("4.250"), "total": D("20017.50")}],
        totals=[(_("Returned value"), _num(D("20017.50")), False),
                (_("Deduction"), f"−{_num(D('500'))}", False),
                (_("Refund"), f"{_num(D('19517.50'))} EGP", True)], currency="EGP")


# ---- reservation ---------------------------------------------------------------------------

RESERVATION_COLUMNS = (
    Column("piece", _("Barcode"), fixed=True), Column("description", _("Item")),
    Column("karat", _("Karat")), Column("weight", _("Weight (g)"), "end", fmt="w3"),
    Column("total", _("Reserved at"), "end", fixed=True, fmt="money"),
)


def build_reservation(res, design, ctx=None):
    home = _home()
    lang = design.language
    with translation.override(_lang_of(design)):
        rows = [{"piece": line.item.barcode, "description": line.item.category.name,
                 "karat": line.item.karat.label, "weight": line.item.gross_weight_g,
                 "total": line.quoted_total}
                for line in res.lines.select_related("item__category", "item__karat__metal")]
        deposits = [(f"{d.number} · {_day(d.business_date)} · {d.get_kind_display()}",
                     f"{_num(d.amount)} {d.currency.code}")
                    for d in res.deposits.select_related("currency")]
    price = (_("Locked at the reservation date") if res.price_locked
             else _("The price on the day of completion"))
    return make_doc(
        TYPES["reservation"], design, company=_record_company(res.branch), number=res.number,
        date=_when(res), status="voided" if res.state == "cancelled" else "",
        meta=[(_("Hold until"), _day(res.expires_on))],
        party=[(_("Customer"), res.customer.name),
               (_("Phone"), getattr(res.customer, "phone", "")),
               (_("Gold price"), _t(price, lang))],
        rows=rows, sections=[(_("Deposits"), deposits), *_note(_("Note"), res.note)],
        totals=[(_("Total"), f"{_num(res.quoted_total)} {home}", False),
                (_("Deposits held"), f"{_num(res.deposit_amount)} {home}", True)],
        currency=home)


def sample_reservation(design, company):
    return make_doc(
        TYPES["reservation"], design, company=company, number="R-2026-000045", date=SAMPLE_DATE,
        meta=[(_("Hold until"), "18/10/2026")],
        party=[(_("Customer"), "منى سمير"), (_("Phone"), "01112223334"),
               (_("Gold price"), _t(_("Locked at the reservation date"), design.language))],
        rows=[{"piece": "1010000052", "description": "دبلة", "karat": "21K",
               "weight": D("5.100"), "total": D("24021.00")},
              {"piece": "1030000004", "description": "حلق", "karat": "18K",
               "weight": D("3.200"), "total": D("13280.00")}],
        sections=[(_("Deposits"), [("RD-2026-000031 · 04/10/2026 · نقدي", "10,000.00 EGP")])],
        totals=[(_("Total"), f"{_num(D('37301.00'))} EGP", False),
                (_("Deposits held"), f"{_num(D('10000.00'))} EGP", True)], currency="EGP")


# ---- wholesale sale and return ---------------------------------------------------------------

TRADE_COLUMNS = (
    Column("piece", _("Item"), fixed=True), Column("karat", _("Karat")),
    Column("qty", _("Pieces"), "end", fmt="int"),
    Column("weight", _("Weight (g)"), "end", fmt="w3"),
    Column("fine", _("Fine gold"), "end", fmt="w4"),
    Column("gold_price", _("Gold price/g"), "end", fmt="money"),
    Column("gold_value", _("Gold value"), "end", fmt="money"),
    Column("making_rate", _("Making/g"), "end", fmt="money"),
    Column("making", _("Making"), "end", fixed=True, fmt="money"),
)


def _trade_rows(lines, in_metal, lang):
    return [{"piece": _item(line, lang), "karat": line.karat.label, "qty": line.qty,
             "weight": line.gross_weight_g, "fine": line.fine_weight_g,
             "gold_price": None if in_metal else line.metal_price_per_g,
             "gold_value": None if in_metal else line.metal_amount,
             "making_rate": line.making_rate, "making": line.making_amount}
            for line in lines]


def build_trade_sale(doc, design, ctx=None):
    home = _home()
    with translation.override(_lang_of(design)):
        rows = _trade_rows(doc.lines.select_related("item", "category", "karat__metal"),
                           doc.in_metal, design.language)
        basis = doc.get_settlement_basis_display()
    totals = [(_("Weight (g)"), _num(doc.total_gross_weight_g, 3), False)]
    if doc.in_metal:
        totals.append((_("Fine gold owed (g)"), _num(doc.total_fine_weight_g, 4), True))
    else:
        totals.append((_("Gold value"), _num(doc.metal_amount), False))
    totals += [(_("Making charge"), _num(doc.making_amount), False),
               (_("Owed in money"), f"{_num(doc.money_amount)} {home}", True)]
    return make_doc(TYPES["trade_sale"], design, company=_record_company(doc.branch),
                    number=doc.number, date=_when(doc), status=doc.status,
                    party=[(_("Trade account"), doc.trade_account.name),
                           (_("The trader pays"), basis)],
                    rows=rows, totals=totals, currency=home, sections=_note(_("Note"), doc.note))


def build_trade_return(doc, design, ctx=None):
    home = _home()
    sale = doc.original_sale
    with translation.override(_lang_of(design)):
        lines = [line.original_line for line in doc.lines.select_related(
            "original_line__item", "original_line__category", "original_line__karat")]
        rows = _trade_rows(lines, sale.in_metal, design.language)
    totals = [(_("Weight (g)"), _num(doc.total_gross_weight_g, 3), False)]
    if sale.in_metal:
        totals.append((_("Fine gold credited (g)"), _num(doc.total_fine_weight_g, 4), True))
    totals.append((_("Money credited"), f"{_num(doc.money_amount)} {home}", True))
    return make_doc(TYPES["trade_return"], design, company=_record_company(doc.branch),
                    number=doc.number, date=_when(doc), status=doc.status,
                    meta=[(_("Sale"), sale.number)],
                    party=[(_("Trade account"), doc.trade_account.name)], rows=rows,
                    totals=totals, currency=home, sections=_note(_("Reason"), doc.reason))


TRADE_SAMPLE_ROWS = [
    {"piece": "سلاسل · بالوزن", "karat": "21K", "qty": 0, "weight": D("150.000"),
     "fine": D("131.2500"), "gold_price": D("4560"), "gold_value": D("684000.00"),
     "making_rate": D("35"), "making": D("5250.00")},
    {"piece": "1010000077 · خواتم", "karat": "18K", "qty": 1, "weight": D("6.400"),
     "fine": D("4.8000"), "gold_price": D("3910"), "gold_value": D("25024.00"),
     "making_rate": D("60"), "making": D("384.00")},
]


def sample_trade_sale(design, company):
    return make_doc(
        TYPES["trade_sale"], design, company=company, number="TS-2026-000009", date=SAMPLE_DATE,
        party=[(_("Trade account"), "مؤسسة الذهبي للتجارة"), (_("The trader pays"), "نقداً")],
        rows=TRADE_SAMPLE_ROWS,
        totals=[(_("Weight (g)"), _num(D("156.400"), 3), False),
                (_("Gold value"), _num(D("709024.00")), False),
                (_("Making charge"), _num(D("5634.00")), False),
                (_("Owed in money"), f"{_num(D('714658.00'))} EGP", True)], currency="EGP")


def sample_trade_return(design, company):
    return make_doc(
        TYPES["trade_return"], design, company=company, number="TR-2026-000003",
        date=SAMPLE_DATE, meta=[(_("Sale"), "TS-2026-000009")],
        party=[(_("Trade account"), "مؤسسة الذهبي للتجارة")], rows=TRADE_SAMPLE_ROWS[1:],
        totals=[(_("Weight (g)"), _num(D("6.400"), 3), False),
                (_("Money credited"), f"{_num(D('25408.00'))} EGP", True)], currency="EGP")


# ---- repair --------------------------------------------------------------------------------

REPAIR_COLUMNS = (
    Column("description", _("Piece and work"), fixed=True), Column("karat", _("Karat")),
    Column("weight_in", _("Weight in (g)"), "end", fmt="w3"),
    Column("weight_out", _("Weight out (g)"), "end", fmt="w3"),
    Column("difference", _("Difference"), "end", default=False),
    Column("total", _("Price"), "end", fixed=True, fmt="money"),
)


def _difference(loss):
    if loss is None:
        return None
    if loss > 0:
        return f"−{_num(loss, 3)}"
    return f"+{_num(abs(loss), 3)}" if loss < 0 else "0.000"


def build_repair(order, design, ctx=None):
    home = _home()
    with translation.override(_lang_of(design)):
        rows = [{"description": line.description,
                 "karat": line.karat.label if line.karat_id else None,
                 "weight_in": line.weight_in_g, "weight_out": line.weight_out_g,
                 "difference": _difference(line.loss_g), "total": line.charge_amount}
                for line in order.lines.select_related("karat")]
        title = order.get_kind_display()
        payments = [(f"{p.get_stage_display()} · {_day(p.business_date)}",
                     f"{_num(p.amount)} {p.currency.code}")
                    for p in order.payments.select_related("currency").order_by("id")]
    phone = order.customer.phone if order.customer_id else order.customer_phone
    totals = [(_("Charge"), _num(order.charge_amount), False)]
    if order.state != "delivered":
        totals += [(_("Deposit held"), f"−{_num(order.deposit_amount)}", False),
                   (_("Still to pay"), f"{_num(order.due_amount)} {home}", True)]
    else:
        totals.append((_("Paid on delivery"), _num(order.paid_amount), True))
        if order.change_amount:
            totals.append((_("Change given"), _num(order.change_amount), False))
        if order.balance_amount:
            totals.append((_("On the customer's account"), _num(order.balance_amount), False))
    return make_doc(
        TYPES["repair"], design, title=title, company=_record_company(order.branch),
        number=order.number, date=_when(order), status=order.status,
        meta=[(_("Bag"), order.bag_number), (_("Promised for"), _day(order.promised_on))],
        party=[(_("Customer"), str(order.who)), (_("Phone"), phone)], rows=rows,
        sections=[(_("Payments"), payments), *_note(_("Note"), order.note)], totals=totals,
        currency=home)


def sample_repair(design, company):
    return make_doc(
        TYPES["repair"], design, company=company, number="RP-2026-000088", date=SAMPLE_DATE,
        meta=[(_("Bag"), "17"), (_("Promised for"), "11/10/2026")],
        party=[(_("Customer"), "هالة محمود"), (_("Phone"), "01223344556")],
        rows=[{"description": "تصليح قفل سلسلة", "karat": "21K", "weight_in": D("8.300"),
               "weight_out": D("8.250"), "difference": "−0.050", "total": D("250.00")},
              {"description": "تلميع خاتم", "karat": "18K", "weight_in": D("3.900"),
               "weight_out": None, "difference": None, "total": D("120.00")}],
        sections=[(_("Payments"), [("عربون · 04/10/2026", "200.00 EGP")])],
        totals=[(_("Charge"), _num(D("370.00")), False),
                (_("Deposit held"), f"−{_num(D('200.00'))}", False),
                (_("Still to pay"), f"{_num(D('170.00'))} EGP", True)], currency="EGP")


# ---- receipts and payments -----------------------------------------------------------------

def build_settlement(s, design, ctx=None):
    home = _home()
    g = _t(_("g"), design.language)
    with translation.override(_lang_of(design)):
        title = s.get_kind_display()
        side = s.get_side_display()
        method = s.get_method_display() if s.kind in ("receipt", "payment") else ""
        direction = s.get_direction_display() if s.kind == "conversion" else ""
    meta, totals = [], []
    if s.kind in ("receipt", "payment"):
        meta.append((_("How"), method))
        totals.append((_("Amount"), f"{_num(s.amount)} {s.currency.code}", True))
        if s.currency.code != home:
            totals.append((_("Value"), f"{_num(s.functional_amount)} {home}", False))
    elif s.kind == "conversion":
        meta.append((_("Which way"), direction))
        totals += [(_("Fine gold"), f"{_num(s.fine_weight_g, 4)} {g}", False),
                   (_("Price per fine gram"), _num(s.price_per_fine_g), False),
                   (_("Amount"), f"{_num(s.amount)} {s.currency.code}", True)]
    else:
        meta.append((_("Karat"), s.karat.label if s.karat_id else ""))
        totals += [(_("Weight"), f"{_num(s.gross_weight_g, 3)} {g}", True),
                   (_("Fine gold"), f"{_num(s.fine_weight_g, 4)} {g}", False)]
    meta.append((_("Reference"), s.reference))
    return make_doc(TYPES["settlement"], design, title=title,
                    company=_record_company(s.branch), number=s.number, date=_when(s),
                    status=s.status, meta=meta, party=[(side, s.party.name)], totals=totals,
                    currency=home, sections=_note(_("Note"), s.note))


def sample_settlement(design, company):
    return make_doc(
        TYPES["settlement"], design, title=_("Receipt"), company=company,
        number="RC-2026-000210", date=SAMPLE_DATE,
        meta=[(_("How"), _t(_("Cash"), design.language)), (_("Reference"), "دفعة من الحساب")],
        party=[(_("Customer"), "عمرو حسن")],
        totals=[(_("Amount"), f"{_num(D('15000.00'))} EGP", True)], currency="EGP")


# ---- purchases -----------------------------------------------------------------------------

PURCHASE_COLUMNS = (
    Column("description", _("Category"), fixed=True), Column("karat", _("Karat")),
    Column("qty", _("Pieces"), "end", fmt="int"),
    Column("weight", _("Weight (g)"), "end", fmt="w3"),
    Column("fine", _("Fine (g)"), "end", fmt="w4"),
    Column("cost", _("Making cost"), "end", fmt="money", cost=True),
)


def build_purchase_invoice(inv, design, ctx=None):
    from apps.purchasing.services import totals as purchase_totals

    can_cost = (ctx or {}).get("can_cost", False)
    lang = design.language
    with translation.override(_lang_of(design)):
        rows = [{"description": line.category.name,
                 "karat": line.karat.label if line.karat_id else None, "qty": line.qty,
                 "weight": line.gross_weight_g, "fine": line.fine_weight_g,
                 "cost": line.making_cost_amount}
                for line in inv.lines.select_related("category", "karat__metal")]
        role = inv.get_seller_role_display()
    t = purchase_totals(inv)
    g = _t(_("g"), lang)
    totals = [(_("Pieces"), str(t["pieces"]), False),
              (_("Weight (g)"), _num(t["gross_weight_g"], 3), False)]
    totals += [(f"{_t(_('Fine gold'), lang)} ({metal})", f"{_num(w, 4)} {g}", True)
               for metal, w in t["fine_by_metal"].items()]
    if can_cost:
        totals.append((_("Making cost"), f"{_num(t['making_cost_amount'])} {inv.currency.code}",
                       True))
    return make_doc(TYPES["purchase_invoice"], design, company=_record_company(inv.branch),
                    number=inv.number, date=_when(inv), status=inv.status,
                    meta=[(_("Their no."), inv.supplier_reference)],
                    party=[(role, inv.supplier.name)], rows=rows, totals=totals,
                    currency=inv.currency.code, can_cost=can_cost,
                    sections=_note(_("Note"), inv.note))


def sample_purchase_invoice(design, company):
    return make_doc(
        TYPES["purchase_invoice"], design, company=company, number="P-2026-000061",
        date=SAMPLE_DATE, meta=[(_("Their no."), "INV-5521")],
        party=[(_("Supplier"), "مصنع النور للمشغولات")],
        rows=[{"description": "خواتم", "karat": "21K", "qty": 12, "weight": D("48.600"),
               "fine": D("42.5250"), "cost": D("4860.00")},
              {"description": "سلاسل", "karat": "18K", "qty": 0, "weight": D("120.000"),
               "fine": D("90.0000"), "cost": D("6000.00")}],
        totals=[(_("Weight (g)"), _num(D("168.600"), 3), False),
                (_("Fine gold"), _num(D("132.5250"), 4), True),
                (_("Making cost"), f"{_num(D('10860.00'))} EGP", True)], currency="EGP")


SUPPLIER_RETURN_COLUMNS = (
    Column("piece", _("Item"), fixed=True), Column("karat", _("Karat")),
    Column("qty", _("Pieces"), "end", fmt="int"),
    Column("weight", _("Weight (g)"), "end", fmt="w3"),
    Column("fine", _("Fine gold"), "end", fmt="w4"),
    Column("cost", _("Making cost"), "end", fmt="money", cost=True),
)


def build_supplier_return(doc, design, ctx=None):
    can_cost = (ctx or {}).get("can_cost", False)
    with translation.override(_lang_of(design)):
        rows = [{"piece": _item(line, design.language),
                 "karat": line.karat.label if line.karat_id else None, "qty": line.qty,
                 "weight": line.gross_weight_g, "fine": line.fine_weight_g,
                 "cost": line.cost_amount}
                for line in doc.lines.select_related("item", "category", "karat__metal")]
        role = doc.get_seller_role_display()
    totals = [(_("Pieces"), str(doc.total_qty), False),
              (_("Weight (g)"), _num(doc.total_gross_weight_g, 3), False),
              (_("Fine gold"), _num(doc.total_fine_weight_g, 4), True)]
    if can_cost:
        totals.append((_("Making cost"), _num(doc.total_cost), True))
    return make_doc(TYPES["supplier_return"], design, company=_record_company(doc.branch),
                    number=doc.number, date=_when(doc), status=doc.status,
                    party=[(role, doc.supplier.name)], rows=rows, totals=totals,
                    can_cost=can_cost, sections=_note(_("Note"), doc.note))


def sample_supplier_return(design, company):
    return make_doc(
        TYPES["supplier_return"], design, company=company, number="PR-2026-000004",
        date=SAMPLE_DATE, party=[(_("Supplier"), "مصنع النور للمشغولات")],
        rows=[{"piece": "1010000033 · خواتم", "karat": "21K", "qty": 1, "weight": D("4.050"),
               "fine": D("3.5438"), "cost": D("405.00")}],
        totals=[(_("Weight (g)"), _num(D("4.050"), 3), False),
                (_("Fine gold"), _num(D("3.5438"), 4), True)])


SCRAP_BUY_COLUMNS = (
    Column("karat", _("Karat"), fixed=True), Column("weight", _("Weight (g)"), "end", fmt="w3"),
    Column("loss", _("Loss (g)"), "end", fmt="w3"), Column("net", _("Net (g)"), "end", fmt="w3"),
    Column("price", _("Price / g"), "end", fmt="money"),
    Column("total", _("Amount"), "end", fixed=True, fmt="money"),
)
SCRAP_SELL_COLUMNS = (
    Column("karat", _("Karat"), fixed=True), Column("weight", _("Weight (g)"), "end", fmt="w3"),
    Column("fine", _("Fine gold"), "end", fmt="w4"),
    Column("price", _("Price / g"), "end", fmt="money"),
    Column("total", _("Amount"), "end", fixed=True, fmt="money"),
)


def build_scrap_purchase(doc, design, ctx=None):
    home = _home()
    with translation.override(_lang_of(design)):
        rows = [{"karat": line.karat.label, "weight": line.gross_weight_g,
                 "loss": line.loss_weight_g, "net": line.net_weight_g,
                 "price": line.price_per_g, "total": line.amount}
                for line in doc.lines.select_related("karat__metal")]
        paid = doc.get_payment_display()
    seller = doc.seller.name if doc.seller_id else doc.seller_name
    return make_doc(TYPES["scrap_purchase"], design, company=_record_company(doc.branch),
                    number=doc.number, date=_when(doc), status=doc.status,
                    party=[(_("Seller"), seller), (_("Phone"), doc.seller_phone),
                           (_("Paid"), paid)], rows=rows,
                    totals=[(_("Weight (g)"), _num(doc.total_gross_weight_g, 3), False),
                            (_("Total"), f"{_num(doc.total_amount)} {home}", True)],
                    currency=home, sections=_note(_("Note"), doc.note))


def build_scrap_sale(doc, design, ctx=None):
    home = _home()
    with translation.override(_lang_of(design)):
        rows = [{"karat": line.karat.label, "weight": line.gross_weight_g,
                 "fine": line.fine_weight_g, "price": line.price_per_g, "total": line.amount}
                for line in doc.lines.select_related("karat__metal")]
        received = doc.get_payment_display()
    return make_doc(TYPES["scrap_sale"], design, company=_record_company(doc.branch),
                    number=doc.number, date=_when(doc), status=doc.status,
                    party=[(_("Buyer"), doc.buyer.name), (_("Received"), received)], rows=rows,
                    totals=[(_("Weight (g)"), _num(doc.total_gross_weight_g, 3), False),
                            (_("Fine gold"), _num(doc.total_fine_weight_g, 4), False),
                            (_("Total"), f"{_num(doc.total_amount)} {home}", True)],
                    currency=home, sections=_note(_("Note"), doc.note))


def sample_scrap_purchase(design, company):
    return make_doc(
        TYPES["scrap_purchase"], design, company=company, number="SP-2026-000140",
        date=SAMPLE_DATE, party=[(_("Seller"), "كريم علي"), (_("Phone"), "01009988776"),
                                 (_("Paid"), _t(_("Cash"), design.language))],
        rows=[{"karat": "21K", "weight": D("12.400"), "loss": D("0.200"), "net": D("12.200"),
               "price": D("4400"), "total": D("53680.00")}],
        totals=[(_("Weight (g)"), _num(D("12.400"), 3), False),
                (_("Total"), f"{_num(D('53680.00'))} EGP", True)], currency="EGP")


def sample_scrap_sale(design, company):
    return make_doc(
        TYPES["scrap_sale"], design, company=company, number="SS-2026-000012", date=SAMPLE_DATE,
        party=[(_("Buyer"), "تاجر الكسر الحاج سيد"),
               (_("Received"), _t(_("Cash"), design.language))],
        rows=[{"karat": "21K", "weight": D("250.000"), "fine": D("218.7500"),
               "price": D("4480"), "total": D("1120000.00")}],
        totals=[(_("Weight (g)"), _num(D("250.000"), 3), False),
                (_("Total"), f"{_num(D('1120000.00'))} EGP", True)], currency="EGP")


# ---- expenses and treasury -----------------------------------------------------------------

def build_expense(v, design, ctx=None):
    home = _home()
    totals = [(_("Amount"), f"{_num(v.amount)} {v.currency.code}", True)]
    if v.currency.code != home:
        totals.append((_("Value"), f"{_num(v.functional_amount)} {home}", False))
    return make_doc(TYPES["expense"], design, company=_record_company(v.branch),
                    number=v.number, date=_when(v), status=v.status,
                    meta=[(_("Expense category"), v.category.name),
                          (_("Reference"), v.reference)],
                    party=[(_("Paid to"), v.payee), (_("Paid from"), str(v.paid_from.label))],
                    totals=totals, currency=home, sections=_note(_("Note"), v.note))


def sample_expense(design, company):
    return make_doc(
        TYPES["expense"], design, company=company, number="EX-2026-000033", date=SAMPLE_DATE,
        meta=[(_("Expense category"), "كهرباء")],
        party=[(_("Paid to"), "شركة الكهرباء"), (_("Paid from"), "الخزينة الرئيسية")],
        totals=[(_("Amount"), f"{_num(D('2350.00'))} EGP", True)], currency="EGP")


def build_treasury_document(doc, design, ctx=None):
    home = _home()
    source = str(doc.source.label) + (f" · {doc.branch.name}" if doc.source_box else "")
    dest = str(doc.destination.label) + (f" · {doc.to_branch.name}" if doc.dest_box else "")
    if doc.kind == "exchange":
        totals = [(_("Sold"), f"{_num(doc.amount)} {doc.currency.code}", False),
                  (_("Bought"), f"{_num(doc.dest_amount)} {doc.dest_currency.code}", True),
                  (_("Rate"), _num(doc.rate, 4), False)]
    elif doc.kind == "card_settlement":
        totals = [(_("Card payments settled"), f"{_num(doc.amount)} {doc.currency.code}", False),
                  (_("Bank fee"), f"{_num(doc.fee_amount)} {doc.currency.code}", False),
                  (_("Paid into the bank"), f"{_num(doc.dest_amount)} {doc.currency.code}",
                   True)]
    else:
        totals = [(_("Amount"), f"{_num(doc.amount)} {doc.currency.code}", True)]
    with translation.override(_lang_of(design)):
        title = str(doc.title)
    return make_doc(TYPES["treasury_document"], design, title=title,
                    company=_record_company(doc.branch), number=doc.number, date=_when(doc),
                    status=doc.status, meta=[(_("Reference"), doc.reference)],
                    party=[(_("From"), source), (_("To"), dest)], totals=totals, currency=home,
                    sections=_note(_("Note"), doc.note))


def sample_treasury_document(design, company):
    return make_doc(
        TYPES["treasury_document"], design, title=_("Transfer"), company=company,
        number="TD-2026-000019", date=SAMPLE_DATE,
        party=[(_("From"), "الخزينة الرئيسية"), (_("To"), "البنك الأهلي")],
        totals=[(_("Amount"), f"{_num(D('250000.00'))} EGP", True)], currency="EGP")


# ---- stock transfer ------------------------------------------------------------------------

TRANSFER_COLUMNS = (
    Column("piece", _("Item"), fixed=True), Column("karat", _("Karat")),
    Column("qty", _("Pieces"), "end", fmt="int"),
    Column("weight", _("Weight (g)"), "end", fmt="w3"),
    Column("fine", _("Fine gold"), "end", fmt="w4"),
)


def build_transfer(t, design, ctx=None):
    with translation.override(_lang_of(design)):
        rows = [{"piece": _item(line, design.language, bulk=_("Bulk")),
                 "karat": line.karat.label if line.karat_id else None, "qty": line.qty,
                 "weight": line.gross_weight_g, "fine": line.fine_weight_g}
                for line in t.lines.select_related("item", "category", "karat__metal")]
    return make_doc(TYPES["transfer"], design, company=_record_company(t.branch),
                    number=t.number, date=_when(t), status=t.status,
                    party=[(_("From"), t.branch.name), (_("To"), t.to_branch.name)], rows=rows,
                    totals=[(_("Pieces"), str(t.total_qty), False),
                            (_("Weight (g)"), _num(t.total_gross_weight_g, 3), True),
                            (_("Fine gold"), _num(t.total_fine_weight_g, 4), False)])


def sample_transfer(design, company):
    return make_doc(
        TYPES["transfer"], design, company=company, number="ST-2026-000027", date=SAMPLE_DATE,
        party=[(_("From"), "الفرع الرئيسي"), (_("To"), "فرع المعادي")],
        rows=[{"piece": "1010000041 · خواتم", "karat": "21K", "qty": 1, "weight": D("4.250"),
               "fine": D("3.7188")},
              {"piece": "سلاسل · بالجملة", "karat": "18K", "qty": 0, "weight": D("85.000"),
               "fine": D("63.7500")}],
        totals=[(_("Weight (g)"), _num(D("89.250"), 3), True)])


# ---- finding records: the same scoping as each document's own page --------------------------

def _finder(path: str):
    def find(request):
        module, name = path.rsplit(".", 1)
        return getattr(importlib.import_module(module), name)(request)
    return find


def _sales_returns(request):
    from apps.sales.models import SalesReturn

    branches = request.actor.branch_ids("sales.invoice.view")
    qs = SalesReturn.objects.select_related("original_invoice", "branch", "customer")
    return qs if branches is None else qs.filter(branch_id__in=branches)


def _trade(model_name):
    def find(request):
        from apps.sales import models
        from apps.sales.web.trade_views import _scope

        return _scope(request, getattr(models, model_name).objects.select_related(
            "trade_account", "branch"))
    return find


def _scrap(model_name):
    def find(request):
        from apps.purchasing import models
        from apps.purchasing.web.scrap_views import _visible

        return _visible(request, getattr(models, model_name))
    return find


SALES, PURCHASES, MONEY, GOODS = _("Sales"), _("Purchases"), _("Money"), _("Goods")
HANDOVER = (_("Handed over by"), _("Received by"))

TYPES: dict[str, DocumentType] = {t.key: t for t in [
    DocumentType("sales_invoice", _("Sales invoice"), SALES, SALES_COLUMNS, build_sales_invoice,
                 sample_sales_invoice, "sales.invoice.view",
                 _finder("apps.sales.web.views._visible"), SALES_OPTIONS),
    DocumentType("sales_return", _("Customer return"), SALES, RETURN_COLUMNS,
                 build_sales_return, sample_sales_return, "sales.invoice.view", _sales_returns),
    DocumentType("reservation", _("Reservation"), SALES, RESERVATION_COLUMNS,
                 build_reservation, sample_reservation, "sales.reservation.view",
                 _finder("apps.sales.web.views._visible_reservations"),
                 signatures=(_("Customer"), _("Received by"))),
    DocumentType("trade_sale", _("Wholesale sale"), SALES, TRADE_COLUMNS, build_trade_sale,
                 sample_trade_sale, "sales.trade.view", _trade("TradeSale"),
                 signatures=HANDOVER),
    DocumentType("trade_return", _("Wholesale return"), SALES, TRADE_COLUMNS,
                 build_trade_return, sample_trade_return, "sales.trade.view",
                 _trade("TradeReturn"), signatures=HANDOVER),
    DocumentType("repair", _("Repair or custom order"), SALES, REPAIR_COLUMNS, build_repair,
                 sample_repair, "repairs.order.view", _finder("apps.repairs.views._visible"),
                 signatures=(_("Customer's signature"), _("Received by"))),
    DocumentType("purchase_invoice", _("Purchase invoice"), PURCHASES, PURCHASE_COLUMNS,
                 build_purchase_invoice, sample_purchase_invoice, "purchasing.invoice.view",
                 _finder("apps.purchasing.web.views._visible"), signatures=HANDOVER),
    DocumentType("supplier_return", _("Return to supplier"), PURCHASES,
                 SUPPLIER_RETURN_COLUMNS, build_supplier_return, sample_supplier_return,
                 "purchasing.invoice.view",
                 _finder("apps.purchasing.web.return_views._visible"), signatures=HANDOVER),
    DocumentType("scrap_purchase", _("Scrap purchase"), PURCHASES, SCRAP_BUY_COLUMNS,
                 build_scrap_purchase, sample_scrap_purchase, "purchasing.scrap.view",
                 _scrap("ScrapPurchase"), signatures=(_("Seller"), _("Received by"))),
    DocumentType("scrap_sale", _("Scrap sale"), PURCHASES, SCRAP_SELL_COLUMNS,
                 build_scrap_sale, sample_scrap_sale, "purchasing.scrap.view",
                 _scrap("ScrapSale"), signatures=(_("Handed over by"), _("Buyer"))),
    DocumentType("settlement", _("Receipt or payment"), MONEY, (), build_settlement,
                 sample_settlement, "settlements.view",
                 _finder("apps.settlements.views._visible"),
                 signatures=(_("Received by"), _("Paid by"))),
    DocumentType("expense", _("Expense voucher"), MONEY, (), build_expense, sample_expense,
                 "expenses.view", _finder("apps.expenses.views._visible"),
                 signatures=(_("Received by"), _("Paid by"))),
    DocumentType("treasury_document", _("Cash and bank movement"), MONEY, (),
                 build_treasury_document, sample_treasury_document, "treasury.view",
                 _finder("apps.treasury.views._visible_documents"), signatures=HANDOVER),
    DocumentType("transfer", _("Stock transfer"), GOODS, TRANSFER_COLUMNS, build_transfer,
                 sample_transfer, "inventory.stock.view",
                 _finder("apps.inventory.web.views._visible_transfers"), signatures=HANDOVER),
]}
SALES_TYPE = "sales_invoice"
DEFAULT_SOURCE = SALES_TYPE  # a document type with no design of its own follows this one


def grouped_types() -> list[tuple[str, list[DocumentType]]]:
    groups: dict[str, list] = {}
    for kind in TYPES.values():
        groups.setdefault(str(kind.group), []).append(kind)
    return list(groups.items())
