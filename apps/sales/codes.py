"""One-time codes that confirm a sale with the customer's phone (legacy GHC16/GHC30 OTP).

The shop chooses when a code is needed (sales left on the customer's account, completing a
reservation, sales above an amount) once the platform has switched on "sale_codes" for it.
The sales screen asks for a code to the customer's phone for the amount being sold; the
customer reads it out; the sale is posted with that code, which is then used up. Someone with
`sales.code.skip` may sell without one, and the invoice says so.

Rules (Gweb's): 6 digits, 5 minutes, 5 wrong tries, a new code at most every 60 seconds and 3
sends per code. Only a keyed hash of the code is stored.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core.errors import DomainError, NotFound, ValidationError
from apps.core.numeric import round_money
from apps.core.tenancy.context import require_current_tenant_id
from apps.messaging.codes import CodeMessage, configured, send_code
from apps.org.models import Branch, TenantProfile
from apps.parties.domain import InvalidPhone, normalize_phone
from apps.parties.models import Party
from apps.platform.tenants.features import is_enabled
from apps.pricing.selectors import functional_currency

from .models import PaymentTerms, Reservation, SaleCode, SaleCodeRules

LENGTH = 6
VALID_FOR = timedelta(minutes=5)
USE_WITHIN = timedelta(minutes=15)  # after it was verified
MAX_ATTEMPTS = 5
MAX_SENDS = 3
RESEND_AFTER = timedelta(seconds=60)


@dataclass(frozen=True)
class Need:
    needed: bool
    phone: str = ""  # E.164, where the code goes
    reason: str = ""


def rules() -> SaleCodeRules:
    found = SaleCodeRules.objects.first()
    return found or SaleCodeRules(on_account=True, on_reservations=True)


def save_rules(*, on_account: bool, on_reservations: bool, above_amount, actor=None):
    if actor is not None:
        actor.require("sales.code.manage")
    amount = None
    if above_amount not in (None, ""):
        try:
            amount = round_money(Decimal(str(above_amount)))
        except (ArithmeticError, ValueError):
            raise ValidationError(_("Please correct the highlighted fields."),
                                  fields={"above_amount": [_("Enter a number.")]}) from None
        if amount <= 0:
            raise ValidationError(_("Please correct the highlighted fields."),
                                  fields={"above_amount": [_("Must be greater than zero.")]})
    current = SaleCodeRules.objects.first() or SaleCodeRules()
    current.on_account, current.on_reservations = on_account, on_reservations
    current.above_amount = amount
    current.updated_by = getattr(actor, "user", None)
    current.save()
    return current


def switched_on() -> bool:
    return is_enabled(require_current_tenant_id(), "sale_codes")


def _phone(raw: str) -> str:
    region = TenantProfile.objects.values_list("country", flat=True).first() or "EG"
    try:
        return normalize_phone(raw, region)
    except InvalidPhone:
        return ""


def masked(phone: str) -> str:
    """"+201012345678" → "+20 ••• ••• 5678": enough for the seller to check it with them."""
    return f"{phone[:3]} ••• ••• {phone[-4:]}" if len(phone) > 7 else phone


def need(*, total: Decimal, payment_terms: str, customer: Party | None, walk_in_phone: str = "",
         reservation: Reservation | None = None) -> Need:
    """Whether this sale needs a code, and which phone it goes to."""
    if not switched_on():
        return Need(False)
    current = rules()
    reasons = []
    if current.on_account and payment_terms == PaymentTerms.CREDIT:
        reasons.append(_("left on the customer's account"))
    if current.on_reservations and reservation is not None:
        reasons.append(_("completing a reservation"))
    if current.above_amount and total >= current.above_amount:
        reasons.append(_("above %(amount)s") % {"amount": f"{current.above_amount:,.2f}"})
    if not reasons:
        return Need(False)
    phone = _phone(customer.phone if customer else walk_in_phone)
    return Need(True, phone, ", ".join(reasons))


def _hash(code_id: int, code: str) -> str:
    key = settings.SECRET_KEY.encode()
    return hmac.new(key, f"{code_id}:{code}".encode(), hashlib.sha256).hexdigest()


def _deliver(sale_code: SaleCode, actor) -> None:
    code = "".join(secrets.choice("0123456789") for _digit in range(LENGTH))
    minutes = int(VALID_FOR.total_seconds() // 60)
    shop = TenantProfile.objects.values_list("display_name", flat=True).first() or ""
    text = _("%(shop)s: your code to confirm the purchase of %(amount)s %(currency)s is "
             "%(code)s. It is valid for %(minutes)s minutes. Do not share it with anyone else.") % {
        "shop": shop, "amount": f"{sale_code.amount:,.2f}", "currency": functional_currency(),
        "code": code, "minutes": minutes}
    user = getattr(actor, "user", None)
    sent = send_code(CodeMessage(
        phone=sale_code.phone, code=code, text=text, amount=sale_code.amount,
        currency=functional_currency(),
        customer_name=sale_code.customer.name if sale_code.customer_id else "",
        employee_name=(getattr(user, "display_name", "") or getattr(user, "username", ""))))
    if not sent.ok:
        raise DomainError(_("The code could not be sent. Try again, or ask a manager to skip it."),
                          code="SALE_CODE_NOT_SENT")
    now = timezone.now()
    sale_code.code_hash = _hash(sale_code.pk, code)
    sale_code.channel = sent.channel
    sale_code.sends += 1
    sale_code.attempts = 0
    sale_code.last_sent_at = now
    sale_code.expires_at = now + VALID_FOR
    sale_code.save()


def request_code(*, branch_id: int, amount, customer_id: int | None = None, phone: str = "",
                 reservation_id: int | None = None, actor=None) -> SaleCode:
    """Send a new code for this sale. Each request makes a new code for the amount."""
    if not switched_on():
        raise DomainError(_("Sale codes are not switched on."), code="FEATURE_DISABLED")
    if not configured():
        raise DomainError(_("Sending codes is not set up yet. Ask the platform."),
                          code="SALE_CODE_NOT_SENT")
    branch = Branch.objects.filter(pk=branch_id, is_active=True).first()
    if branch is None:
        raise NotFound(_("Unknown branch."))
    if actor is not None:
        actor.require("sales.reservation.complete" if reservation_id
                      else "sales.invoice.create", branch=branch)
    customer = Party.objects.filter(pk=customer_id).first() if customer_id else None
    reservation = None
    if reservation_id:
        from .reservations import quote_completion

        reservation = (Reservation.objects.select_related("customer", "branch")
                       .filter(pk=reservation_id, branch=branch).first())
        if reservation is None or reservation.state != "open":
            raise NotFound(_("Not found."))
        customer = reservation.customer
        amount = quote_completion(reservation, (), actor=actor)[3].total
    target = _phone(customer.phone if customer else phone)
    if not target:
        raise ValidationError(_("The customer has no mobile number to send the code to."),
                              fields={"phone": [_("Enter a valid phone number.")]},
                              code="SALE_CODE_NO_PHONE")
    try:
        total = round_money(Decimal(str(amount)))
    except (ArithmeticError, ValueError):
        raise ValidationError(_("Enter a number."), fields={"amount": [_("Enter a number.")]}) \
            from None
    recent = SaleCode.objects.filter(phone=target,
                                     last_sent_at__gt=timezone.now() - RESEND_AFTER).exists()
    if recent:
        raise DomainError(_("A code was just sent. Wait a minute before sending another."),
                          code="SALE_CODE_TOO_SOON")
    with transaction.atomic():
        now = timezone.now()
        sale_code = SaleCode.objects.create(
            branch=branch, customer=customer, reservation=reservation, phone=target,
            amount=total, code_hash="", last_sent_at=now, expires_at=now,
            created_by=getattr(actor, "user", None))
        _deliver(sale_code, actor)
    return sale_code


def resend(code_id: int, *, actor=None) -> SaleCode:
    with transaction.atomic():
        sale_code = SaleCode.objects.select_for_update().filter(pk=code_id).first()
        if sale_code is None or sale_code.invoice_id:
            raise NotFound(_("Not found."))
        if actor is not None:
            actor.require("sales.invoice.create", branch=sale_code.branch)
        if sale_code.sends >= MAX_SENDS:
            raise DomainError(_("This code was sent %(n)s times already. Start again.")
                              % {"n": MAX_SENDS}, code="SALE_CODE_TOO_MANY")
        if timezone.now() - sale_code.last_sent_at < RESEND_AFTER:
            raise DomainError(_("A code was just sent. Wait a minute before sending another."),
                              code="SALE_CODE_TOO_SOON")
        sale_code.verified_at = None
        _deliver(sale_code, actor)
    return sale_code


def verify(code_id: int, code: str, *, actor=None) -> SaleCode:
    with transaction.atomic():
        sale_code = SaleCode.objects.select_for_update().filter(pk=code_id).first()
        if sale_code is None or sale_code.invoice_id:
            raise NotFound(_("Not found."))
        if actor is not None:
            actor.require("sales.invoice.create", branch=sale_code.branch)
        if sale_code.verified_at:
            return sale_code
        if timezone.now() > sale_code.expires_at:
            raise DomainError(_("The code has expired. Send a new one."),
                              code="SALE_CODE_EXPIRED")
        if sale_code.attempts >= MAX_ATTEMPTS:
            raise DomainError(_("Too many wrong tries. Send a new code."),
                              code="SALE_CODE_TOO_MANY")
        entered = "".join(ch for ch in str(code) if ch.isdigit())
        right = hmac.compare_digest(_hash(sale_code.pk, entered), sale_code.code_hash)
        if right:
            sale_code.verified_at = timezone.now()
        else:
            sale_code.attempts += 1
        sale_code.save(update_fields=["verified_at", "attempts", "updated_at"])
    if not right:  # raised after the wrong try is saved: it counts even though this fails
        message = _("That is not the code. %(n)s tries left.") % {
            "n": MAX_ATTEMPTS - sale_code.attempts}
        raise ValidationError(message, fields={"code": [message]}, code="SALE_CODE_WRONG")
    return sale_code


def settle(*, need_: Need, code_id: int | None, skip: bool, total: Decimal,
           customer: Party | None, reservation: Reservation | None, branch, actor) -> tuple:
    """Inside the sale's transaction: the code that confirms it (to link once the invoice
    exists) and what the invoice records. Raises when a code is needed and missing."""
    if not need_.needed:
        return None, ""
    if code_id:
        sale_code = SaleCode.objects.select_for_update().filter(pk=code_id).first()
        fresh = (sale_code is not None and sale_code.verified_at is not None
                 and sale_code.invoice_id is None
                 and timezone.now() - sale_code.verified_at <= USE_WITHIN)
        if not fresh:
            raise DomainError(_("The code is no longer valid. Send a new one."),
                              code="SALE_CODE_REQUIRED")
        if (sale_code.amount != round_money(total)
                or sale_code.branch_id != branch.pk
                or sale_code.customer_id != getattr(customer, "pk", None)
                or sale_code.reservation_id != getattr(reservation, "pk", None)
                or (need_.phone and sale_code.phone != need_.phone)):
            raise DomainError(_("The sale changed after the code was sent. Send a new one."),
                              code="SALE_CODE_REQUIRED")
        return sale_code, "confirmed"
    if skip:
        if actor is not None:
            actor.require("sales.code.skip", branch=branch)
        return None, "skipped"
    raise DomainError(_("This sale needs the code sent to the customer's phone (%(why)s).")
                      % {"why": need_.reason}, code="SALE_CODE_REQUIRED")
