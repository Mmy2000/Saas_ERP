"""Where money is kept (§7.9, §15): cash boxes per branch, bank accounts, card terminals. Each
holder owns one ledger account, so its balance is a ledger balance (ADR-005). Legacy: `Ca1`
(cash, single-sided), `Fb1`/`Fb2` (banks and card terminals mixed in one table).

Treasury documents move money between holders: transfers (box ↔ box, deposits, withdrawals,
bank to bank; between branches in two steps, send then receive), currency exchanges, and card
settlements (the bank pays out what the terminal took, less its fee).
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.core.models import Document, TenantScopedModel


class CashBox(TenantScopedModel):
    branch = models.ForeignKey("org.Branch", on_delete=models.PROTECT, related_name="+")
    currency = models.ForeignKey("catalog.Currency", on_delete=models.PROTECT, related_name="+")
    name = models.CharField(max_length=100, blank=True)  # blank = "Main cash"
    # The box used when a sale or voucher does not name one; one per branch and currency.
    is_default = models.BooleanField(default=False)
    account = models.OneToOneField("ledger.Account", on_delete=models.PROTECT, related_name="+")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["branch__code", "currency__code", "-is_default", "name"]
        verbose_name_plural = "cash boxes"
        constraints = [
            models.UniqueConstraint(fields=["tenant", "branch", "currency"],
                                    condition=Q(is_default=True), name="treasury_box_default_uniq"),
        ]
        indexes = [models.Index(fields=["tenant", "branch"], name="treasury_box_branch_idx")]

    def __str__(self):
        return self.label

    @property
    def label(self) -> str:
        return self.name or str(_("Main cash"))


class BankAccount(TenantScopedModel):
    name = models.CharField(max_length=100, blank=True)  # blank = "Bank account"
    bank_name = models.CharField(max_length=100, blank=True)
    account_number = models.CharField(max_length=60, blank=True)
    iban = models.CharField(max_length=40, blank=True)
    currency = models.ForeignKey("catalog.Currency", on_delete=models.PROTECT, related_name="+")
    # Usable by every branch, or only by those listed in BankAccountBranch.
    all_branches = models.BooleanField(default=True)
    account = models.OneToOneField("ledger.Account", on_delete=models.PROTECT, related_name="+")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["currency__code", "name", "id"]
        indexes = [models.Index(fields=["tenant", "currency"], name="treasury_bank_currency_idx")]

    def __str__(self):
        return self.label

    @property
    def label(self) -> str:
        return self.name or self.bank_name or str(_("Bank account"))


class BankAccountBranch(TenantScopedModel):
    bank_account = models.ForeignKey(BankAccount, on_delete=models.CASCADE,
                                     related_name="branch_links")
    branch = models.ForeignKey("org.Branch", on_delete=models.CASCADE, related_name="+")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "bank_account", "branch"],
                                    name="treasury_bank_branch_uniq"),
        ]


class CardTerminal(TenantScopedModel):
    """A card machine. Card payments wait on its account until the bank settles them."""

    name = models.CharField(max_length=100, blank=True)  # blank = "Card terminal"
    bank_account = models.ForeignKey(BankAccount, on_delete=models.PROTECT,
                                     related_name="terminals")
    branch = models.ForeignKey("org.Branch", null=True, blank=True, on_delete=models.PROTECT,
                               related_name="+")  # null = any branch
    fee_rate = models.DecimalField(max_digits=7, decimal_places=6, default=0)  # 0.02 = 2 %
    account = models.OneToOneField("ledger.Account", on_delete=models.PROTECT, related_name="+")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name", "id"]
        constraints = [
            models.CheckConstraint(condition=Q(fee_rate__gte=0, fee_rate__lt=1),
                                   name="treasury_terminal_fee_range_check"),
        ]
        indexes = [models.Index(fields=["tenant", "branch"], name="treasury_terminal_branch_idx")]

    def __str__(self):
        return self.label

    @property
    def label(self) -> str:
        return self.name or str(_("Card terminal"))

    @property
    def currency(self):
        return self.bank_account.currency


class TreasuryKind(models.TextChoices):
    TRANSFER = "transfer", _("Transfer")
    EXCHANGE = "exchange", _("Currency exchange")
    CARD_SETTLEMENT = "card_settlement", _("Card settlement")


class TreasuryDocument(Document):
    kind = models.CharField(max_length=16, choices=TreasuryKind.choices)
    # Exactly one source: a box or a bank account (transfers, exchanges) or a terminal (card
    # settlements). Exactly one destination: a box or a bank account.
    source_box = models.ForeignKey(CashBox, null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="+")
    source_bank = models.ForeignKey(BankAccount, null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="+")
    source_terminal = models.ForeignKey(CardTerminal, null=True, blank=True,
                                        on_delete=models.PROTECT, related_name="+")
    dest_box = models.ForeignKey(CashBox, null=True, blank=True, on_delete=models.PROTECT,
                                 related_name="+")
    dest_bank = models.ForeignKey(BankAccount, null=True, blank=True, on_delete=models.PROTECT,
                                  related_name="+")
    currency = models.ForeignKey("catalog.Currency", on_delete=models.PROTECT, related_name="+")
    amount = models.DecimalField(max_digits=18, decimal_places=2)  # leaves the source
    dest_currency = models.ForeignKey("catalog.Currency", on_delete=models.PROTECT,
                                      related_name="+")
    dest_amount = models.DecimalField(max_digits=18, decimal_places=2)  # reaches the destination
    fee_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)  # card fee
    rate = models.DecimalField(max_digits=18, decimal_places=8, default=1)  # dest per source unit
    functional_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    reference = models.CharField(max_length=60, blank=True)
    # Cash sent to a box in another branch sits in branch clearing until that branch receives.
    to_branch = models.ForeignKey("org.Branch", on_delete=models.PROTECT, related_name="+")
    needs_receipt = models.BooleanField(default=False)
    received_at = models.DateTimeField(null=True, blank=True)
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.PROTECT, related_name="+")
    journal_entry = models.ForeignKey("ledger.JournalEntry", null=True, blank=True,
                                      on_delete=models.PROTECT, related_name="+")
    receive_entry = models.ForeignKey("ledger.JournalEntry", null=True, blank=True,
                                      on_delete=models.PROTECT, related_name="+")

    class Meta(Document.Meta):
        ordering = ["-business_date", "-id"]
        constraints = [
            *Document.Meta.constraints,
            models.CheckConstraint(
                condition=(Q(source_box__isnull=False, source_bank__isnull=True,
                             source_terminal__isnull=True)
                           | Q(source_box__isnull=True, source_bank__isnull=False,
                               source_terminal__isnull=True)
                           | Q(source_box__isnull=True, source_bank__isnull=True,
                               source_terminal__isnull=False)),
                name="treasury_doc_one_source_check"),
            models.CheckConstraint(
                condition=(Q(dest_box__isnull=False, dest_bank__isnull=True)
                           | Q(dest_box__isnull=True, dest_bank__isnull=False)),
                name="treasury_doc_one_dest_check"),
            models.CheckConstraint(condition=Q(amount__gt=0, dest_amount__gt=0, fee_amount__gte=0),
                                   name="treasury_doc_amounts_check"),
        ]
        indexes = [
            models.Index(fields=["tenant", "branch", "-business_date"],
                         name="treasury_doc_date_idx"),
            models.Index(fields=["tenant", "to_branch"], name="treasury_doc_to_branch_idx",
                         condition=Q(needs_receipt=True, received_at__isnull=True)),
        ]

    def __str__(self):
        return self.number or f"#{self.pk}"

    @property
    def source(self):
        return self.source_box or self.source_bank or self.source_terminal

    @property
    def destination(self):
        return self.dest_box or self.dest_bank

    @property
    def in_transit(self) -> bool:
        return self.needs_receipt and self.received_at is None and self.status == "posted"

    @property
    def title(self) -> str:
        """What happened, in words: a deposit, a withdrawal, a cash transfer…"""
        if self.kind != TreasuryKind.TRANSFER:
            return self.get_kind_display()
        if self.source_box_id and self.dest_bank_id:
            return str(_("Bank deposit"))
        if self.source_bank_id and self.dest_box_id:
            return str(_("Bank withdrawal"))
        if self.source_bank_id:
            return str(_("Bank transfer"))
        return str(_("Cash transfer"))


# --- cheques ------------------------------------------------------------------------------------

class ChequeDirection(models.TextChoices):
    RECEIVED = "received", _("Received")
    ISSUED = "issued", _("Issued")


class ChequeStatus(models.TextChoices):
    IN_HAND = "in_hand", _("In hand")  # received, not deposited yet
    DEPOSITED = "deposited", _("Deposited")  # at the bank, waiting to clear
    OUTSTANDING = "outstanding", _("Outstanding")  # issued, not paid by the bank yet
    CLEARED = "cleared", _("Cleared")
    BOUNCED = "bounced", _("Bounced")
    RETURNED = "returned", _("Returned")  # handed back to the drawer
    ENDORSED = "endorsed", _("Endorsed")  # passed on to a supplier


OPEN_CHEQUE = (ChequeStatus.IN_HAND, ChequeStatus.DEPOSITED, ChequeStatus.OUTSTANDING)


class Cheque(Document):
    """A cheque received from or given to a customer, supplier, trader or workshop. Received
    cheques wait on "cheques received" until they clear into a bank account (or bounce);
    issued ones on "cheques payable" until the bank pays them."""

    direction = models.CharField(max_length=10, choices=ChequeDirection.choices)
    side = models.CharField(max_length=16)  # settlements.PartySide
    party = models.ForeignKey("parties.Party", on_delete=models.PROTECT, related_name="+")
    cheque_number = models.CharField(max_length=40)
    drawn_on = models.CharField(max_length=100, blank=True)  # the drawer's bank, received ones
    due_date = models.DateField()
    currency = models.ForeignKey("catalog.Currency", on_delete=models.PROTECT, related_name="+")
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    # Issued: the account it is drawn on. Received: the account it was deposited into.
    bank_account = models.ForeignKey(BankAccount, null=True, blank=True, on_delete=models.PROTECT,
                                     related_name="cheques")
    state = models.CharField(max_length=12, choices=ChequeStatus.choices)
    state_on = models.DateField(null=True, blank=True)  # when it reached that state
    endorsed_side = models.CharField(max_length=16, blank=True)
    endorsed_to = models.ForeignKey("parties.Party", null=True, blank=True,
                                    on_delete=models.PROTECT, related_name="+")
    journal_entry = models.ForeignKey("ledger.JournalEntry", null=True, blank=True,
                                      on_delete=models.PROTECT, related_name="+")
    settle_entry = models.ForeignKey("ledger.JournalEntry", null=True, blank=True,
                                     on_delete=models.PROTECT, related_name="+")

    class Meta(Document.Meta):
        ordering = ["due_date", "id"]
        constraints = [
            *Document.Meta.constraints,
            models.CheckConstraint(condition=Q(amount__gt=0), name="treasury_cheque_amount_check"),
        ]
        indexes = [
            models.Index(fields=["tenant", "state", "due_date"], name="treasury_cheque_due_idx"),
            models.Index(fields=["tenant", "party"], name="treasury_cheque_party_idx"),
        ]

    def __str__(self):
        return self.number or f"#{self.pk}"

    @property
    def is_open(self) -> bool:
        return self.status == "posted" and self.state in OPEN_CHEQUE


# --- bank reconciliation ------------------------------------------------------------------------

class BankReconciliation(TenantScopedModel):
    """A bank statement matched against the books: the lines ticked as on the statement must
    bring the cleared balance to the statement's closing balance."""

    bank_account = models.ForeignKey(BankAccount, on_delete=models.PROTECT,
                                     related_name="reconciliations")
    statement_date = models.DateField()
    statement_balance = models.DecimalField(max_digits=18, decimal_places=2)
    completed_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                     on_delete=models.PROTECT, related_name="+")
    note = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-statement_date", "-id"]
        constraints = [
            # One reconciliation in progress per bank account.
            models.UniqueConstraint(fields=["tenant", "bank_account"],
                                    condition=Q(completed_at__isnull=True),
                                    name="treasury_recon_one_open_uniq"),
        ]

    @property
    def is_completed(self) -> bool:
        return self.completed_at is not None


class ReconciledLine(TenantScopedModel):
    """A bank journal line ticked as on a statement. A line is cleared once only."""

    reconciliation = models.ForeignKey(BankReconciliation, on_delete=models.CASCADE,
                                       related_name="lines")
    journal_line = models.OneToOneField("ledger.JournalLine", on_delete=models.PROTECT,
                                        related_name="+")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "journal_line"],
                                    name="treasury_recon_line_uniq"),
        ]


# --- cash counts --------------------------------------------------------------------------------

class CashCount(Document):
    """A cash box counted, usually when the day closes. The difference with the books is booked
    as cash over or short, so the box holds what was counted."""

    cash_box = models.ForeignKey(CashBox, on_delete=models.PROTECT, related_name="counts")
    expected = models.DecimalField(max_digits=18, decimal_places=2)  # the books, at the count
    counted = models.DecimalField(max_digits=18, decimal_places=2)
    difference = models.DecimalField(max_digits=18, decimal_places=2)  # counted - expected
    # {"200": 5, "0.5": 3}: notes and coins counted, when counted by denomination.
    denominations = models.JSONField(default=dict, blank=True)
    journal_entry = models.ForeignKey("ledger.JournalEntry", null=True, blank=True,
                                      on_delete=models.PROTECT, related_name="+")

    class Meta(Document.Meta):
        ordering = ["-posted_at", "-id"]
        constraints = [
            *Document.Meta.constraints,
            models.CheckConstraint(condition=Q(counted__gte=0),
                                   name="treasury_count_counted_check"),
        ]
        indexes = [models.Index(fields=["tenant", "cash_box", "-business_date"],
                                name="treasury_count_box_idx")]

    def __str__(self):
        return self.number or f"#{self.pk}"
