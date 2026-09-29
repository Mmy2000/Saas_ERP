"""Customers, suppliers and other business partners (§7.4, ADR-006).

One Party per real person or company, with roles. The same party can be a customer and a
supplier; its balances (from the ledger, later) are then kept per role account.
Legacy sources: `Cust.Cu1`, `Sup.Su1`, `Sh.Sh1`, `Shh.Shh1`, `Part.Fbp1`.
"""

from __future__ import annotations

from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.core.crypto import decrypt
from apps.core.models import TenantScopedModel


class PartyKind(models.TextChoices):
    PERSON = "person", _("Person")
    ORGANIZATION = "organization", _("Company")


class PartyRoleType(models.TextChoices):
    CUSTOMER = "customer", _("Customer")
    SUPPLIER = "supplier", _("Supplier")
    TRADE_ACCOUNT = "trade_account", _("Trade account")
    WORKSHOP = "workshop", _("Workshop")
    SETTER = "setter", _("Stone setter")
    PARTNER = "partner", _("Partner")


class Party(TenantScopedModel):
    name = models.CharField(max_length=200)
    kind = models.CharField(max_length=16, choices=PartyKind.choices, default=PartyKind.PERSON)
    phone = models.CharField(max_length=30, blank=True)  # as typed
    phone_e164 = models.CharField(max_length=20, blank=True)  # normalized, for search/dedupe
    alt_phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    # National ID: encrypted at rest, searchable by exact match through the blind index.
    national_id_encrypted = models.TextField(blank=True)
    national_id_hash = models.CharField(max_length=64, blank=True)
    address = models.TextField(blank=True)
    home_branch = models.ForeignKey("org.Branch", null=True, blank=True,
                                    on_delete=models.PROTECT, related_name="+")
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "parties"
        indexes = [
            models.Index(fields=["tenant", "name"], name="parties_party_name_idx"),
            models.Index(fields=["tenant", "phone_e164"], name="parties_party_phone_idx"),
            models.Index(fields=["tenant", "national_id_hash"],
                         name="parties_party_national_id_idx"),
        ]

    def __str__(self):
        return self.name

    @property
    def national_id(self) -> str:
        return decrypt(self.national_id_encrypted)


class PartyRole(TenantScopedModel):
    party = models.ForeignKey(Party, on_delete=models.CASCADE, related_name="roles")
    role = models.CharField(max_length=16, choices=PartyRoleType.choices)
    code = models.PositiveIntegerField()  # per-role running number shown to users
    legacy_code = models.CharField(max_length=32, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "role", "code"],
                                    name="parties_partyrole_code_uniq"),
            models.UniqueConstraint(fields=["tenant", "party", "role"],
                                    name="parties_partyrole_party_uniq"),
        ]

    def __str__(self):
        return f"{self.role}:{self.code}"


class Gender(models.TextChoices):
    FEMALE = "female", _("Female")
    MALE = "male", _("Male")


class CustomerProfile(TenantScopedModel):
    party = models.OneToOneField(Party, on_delete=models.CASCADE, related_name="customer_profile")
    # Copy of party.phone_e164, kept here so the database can enforce the legacy rule that a
    # phone number identifies at most one customer (`Cu1.cutel` unique).
    phone_e164 = models.CharField(max_length=20, blank=True)
    gender = models.CharField(max_length=8, choices=Gender.choices, blank=True)
    birth_date = models.DateField(null=True, blank=True)
    anniversary = models.DateField(null=True, blank=True)
    ring_size = models.CharField(max_length=10, blank=True)
    bracelet_size = models.CharField(max_length=10, blank=True)
    preferences = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "party"],
                                    name="parties_customerprofile_party_uniq"),
            models.UniqueConstraint(fields=["tenant", "phone_e164"], condition=~Q(phone_e164=""),
                                    name="parties_customerprofile_phone_uniq"),
        ]


class BarcodeWeightRule(models.TextChoices):
    """How a supplier's printed barcode encodes weight (legacy `is_lz`/`is_mz`)."""

    NONE = "none", _("No weight in barcode")
    LAST15_FIRST5 = "last15_first5", _("First 5 of the last 15 digits")
    LAST5 = "last5", _("Last 5 digits")


class SupplierProfile(TenantScopedModel):
    party = models.OneToOneField(Party, on_delete=models.CASCADE, related_name="supplier_profile")
    # The karat the supplier's gold account is kept in (legacy `Su1.co`); balances are stored
    # in fine grams and shown in this karat.
    account_karat = models.ForeignKey("catalog.Karat", null=True, blank=True,
                                      on_delete=models.PROTECT, related_name="+")
    barcode_weight_rule = models.CharField(max_length=16, choices=BarcodeWeightRule.choices,
                                           default=BarcodeWeightRule.NONE)
    lookup_url = models.URLField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "party"],
                                    name="parties_supplierprofile_party_uniq"),
        ]
