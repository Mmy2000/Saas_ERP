"""Customer, supplier and trade account use cases (§7.4)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.utils.translation import gettext as _

from apps.catalog.models import Karat
from apps.core.crypto import blind_index, encrypt
from apps.core.errors import NotFound, ValidationError
from apps.core.sequences import allocate_value
from apps.org.models import Branch, TenantProfile

from .domain import InvalidPhone, normalize_national_id, normalize_phone
from .models import (
    BarcodeWeightRule,
    CustomerProfile,
    Party,
    PartyKind,
    PartyRole,
    PartyRoleType,
    SupplierProfile,
)

NATIONAL_ID_PURPOSE = "party.national_id"


@dataclass(frozen=True)
class PartyData:
    name: str
    kind: str = PartyKind.PERSON
    phone: str = ""
    alt_phone: str = ""
    email: str = ""
    national_id: str | None = None  # None on update = leave unchanged; "" = clear
    address: str = ""
    notes: str = ""
    home_branch_id: int | None = None


@dataclass(frozen=True)
class CustomerData:
    gender: str = ""
    birth_date: date | None = None
    anniversary: date | None = None
    ring_size: str = ""
    bracelet_size: str = ""
    preferences: str = ""


@dataclass(frozen=True)
class SupplierData:
    account_karat_id: int | None = None
    barcode_weight_rule: str = BarcodeWeightRule.NONE
    lookup_url: str = ""


def national_id_lookup_hash(raw: str) -> str:
    return blind_index(normalize_national_id(raw), purpose=NATIONAL_ID_PURPOSE)


def _region() -> str:
    return TenantProfile.objects.values_list("country", flat=True).first() or "EG"


def _apply_party(party: Party, data: PartyData) -> None:
    region = _region()
    errors: dict[str, list[str]] = {}

    name = data.name.strip()
    if not name:
        errors["name"] = [_("This field is required.")]
    try:
        phone_e164 = normalize_phone(data.phone, region)
    except InvalidPhone:
        errors["phone"] = [_("Enter a valid phone number.")]
        phone_e164 = ""
    try:
        normalize_phone(data.alt_phone, region)
    except InvalidPhone:
        errors["alt_phone"] = [_("Enter a valid phone number.")]

    home_branch = None
    if data.home_branch_id is not None:
        home_branch = Branch.objects.filter(pk=data.home_branch_id).first()
        if home_branch is None:
            errors["home_branch"] = [_("Unknown branch.")]

    if errors:
        raise ValidationError(_("Please correct the highlighted fields."), fields=errors)

    party.name = name
    party.kind = data.kind
    party.phone = data.phone.strip()
    party.phone_e164 = phone_e164
    party.alt_phone = data.alt_phone.strip()
    party.email = data.email.strip()
    party.address = data.address.strip()
    party.notes = data.notes.strip()
    party.home_branch = home_branch
    if data.national_id is not None:
        national_id = normalize_national_id(data.national_id)
        party.national_id_encrypted = encrypt(national_id)
        party.national_id_hash = blind_index(national_id, purpose=NATIONAL_ID_PURPOSE)

    party._stamp_tenant()
    try:
        party.full_clean(exclude=["national_id_encrypted", "national_id_hash"])
    except DjangoValidationError as exc:
        raise ValidationError(_("Please correct the highlighted fields."),
                              fields=exc.message_dict) from exc


def _save_party(party: Party, actor) -> None:
    user = getattr(actor, "user", None)
    if party.pk is None:
        party.created_by = user
    party.updated_by = user
    party.save()


def _ensure_role(party: Party, role: str) -> PartyRole:
    existing = party.roles.filter(role=role).first()
    if existing is not None:
        return existing
    return PartyRole.objects.create(party=party, role=role,
                                    code=allocate_value(f"party.{role}"))


def _duplicate_phone() -> ValidationError:
    return ValidationError(_("Another customer already has this phone number."),
                           code="PARTIES_DUPLICATE_PHONE",
                           fields={"phone": [_("Already used by another customer.")]})


def _save_customer_profile(party: Party, data: CustomerData) -> None:
    if party.phone_e164 and CustomerProfile.objects.filter(
            phone_e164=party.phone_e164).exclude(party=party).exists():
        raise _duplicate_phone()
    profile = getattr(party, "customer_profile", None) or CustomerProfile(party=party)
    profile.phone_e164 = party.phone_e164
    profile.gender = data.gender
    profile.birth_date = data.birth_date
    profile.anniversary = data.anniversary
    profile.ring_size = data.ring_size.strip()
    profile.bracelet_size = data.bracelet_size.strip()
    profile.preferences = data.preferences.strip()
    try:
        with transaction.atomic():
            profile.save()
    except IntegrityError as exc:  # lost a race with a concurrent save of the same phone
        raise _duplicate_phone() from exc


def _save_supplier_profile(party: Party, data: SupplierData) -> None:
    karat = None
    if data.account_karat_id is not None:
        karat = Karat.objects.filter(pk=data.account_karat_id, is_active=True).first()
        if karat is None:
            raise ValidationError(_("Unknown karat."),
                                  fields={"account_karat": [_("Unknown karat.")]})
    profile = getattr(party, "supplier_profile", None) or SupplierProfile(party=party)
    profile.account_karat = karat
    profile.barcode_weight_rule = data.barcode_weight_rule
    profile.lookup_url = data.lookup_url.strip()
    profile.save()


def create_customer(data: PartyData, profile: CustomerData | None = None, *,
                    actor=None) -> Party:
    if actor is not None:
        actor.require("parties.customer.create")
    with transaction.atomic():
        party = Party()
        _apply_party(party, data)
        _save_party(party, actor)
        _ensure_role(party, PartyRoleType.CUSTOMER)
        _save_customer_profile(party, profile or CustomerData())
    return party


def update_customer(party_id: int, data: PartyData, profile: CustomerData | None = None, *,
                    actor=None) -> Party:
    if actor is not None:
        actor.require("parties.customer.edit")
    with transaction.atomic():
        party = _locked(party_id, PartyRoleType.CUSTOMER)
        _apply_party(party, data)
        _save_party(party, actor)
        _save_customer_profile(party, profile or CustomerData())
    return party


def create_supplier(data: PartyData, profile: SupplierData | None = None, *,
                    actor=None) -> Party:
    if actor is not None:
        actor.require("parties.supplier.create")
    with transaction.atomic():
        party = Party(kind=PartyKind.ORGANIZATION)
        _apply_party(party, data)
        _save_party(party, actor)
        _ensure_role(party, PartyRoleType.SUPPLIER)
        _save_supplier_profile(party, profile or SupplierData())
    return party


def update_supplier(party_id: int, data: PartyData, profile: SupplierData | None = None, *,
                    actor=None) -> Party:
    if actor is not None:
        actor.require("parties.supplier.edit")
    with transaction.atomic():
        party = _locked(party_id, PartyRoleType.SUPPLIER)
        _apply_party(party, data)
        _save_party(party, actor)
        _save_supplier_profile(party, profile or SupplierData())
    return party


def create_trade_account(data: PartyData, *, actor=None) -> Party:
    """A shop or trader buying wholesale by weight (legacy `Sh1`)."""
    if actor is not None:
        actor.require("parties.trade_account.create")
    with transaction.atomic():
        party = Party(kind=PartyKind.ORGANIZATION)
        _apply_party(party, data)
        _save_party(party, actor)
        _ensure_role(party, PartyRoleType.TRADE_ACCOUNT)
    return party


def update_trade_account(party_id: int, data: PartyData, *, actor=None) -> Party:
    if actor is not None:
        actor.require("parties.trade_account.edit")
    with transaction.atomic():
        party = _locked(party_id, PartyRoleType.TRADE_ACCOUNT)
        _apply_party(party, data)
        _save_party(party, actor)
    return party


def set_party_active(party_id: int, role: str, active: bool, *, actor=None) -> Party:
    if actor is not None:
        actor.require(f"parties.{role}.deactivate")
    # TODO(ledger): refuse to deactivate a party with a non-zero balance (§6.7).
    with transaction.atomic():
        party = _locked(party_id, role)
        party.is_active = active
        _save_party(party, actor)
    return party


def _locked(party_id: int, role: str) -> Party:
    party = (Party.objects.select_for_update(of=("self",))
             .filter(pk=party_id, roles__role=role).first())
    if party is None:
        raise NotFound(_("Not found."))
    return party
