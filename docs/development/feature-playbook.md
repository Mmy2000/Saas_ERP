# Feature playbook

How to build a feature from the request to the merged pull request. The worked example is a
real, small feature already in the code: **expense refunds**. Money from an expense comes back
(a deposit returned, an overcharge refunded), all or part of it, into a cash box or bank
account. Read its files alongside this page; copy the shape, not the details.

## The workflow

1. **Understand the requirement.** Write the acceptance criteria as examples a shop would
   recognise: "a 5,000 rent voucher, 1,000 refunded to the main box: the box is up 1,000, rent
   expense is down 1,000; a second refund of 4,500 is refused (only 4,000 left)". Ask about
   cancelling, permissions and branches now; they're the usual surprises.
2. **Find the affected modules** in [modules](../architecture/modules.md). Refunds belong to
   `expenses` (the document), post through `ledger`, and pay into `treasury` holders. Nothing
   new is needed in the lower layers.
3. **List the implications.**
   - *Data:* a new document model.
   - *API:* a refund action on the voucher, a void on the refund.
   - *Security:* which permission, and per branch.
   - *Ledger:* which lines are posted.
   - *Integrations:* none.
   - Check `PROJECT_PLAN.md` for the section (§7.12 here).
4. **Design the simplest solution that fits.**
   - A refund is a `Document` linked to its voucher. Posting it is a service function, and
     cancelling it is a reversal.
   - No new pattern or abstraction.
5. **Record a decision** only if it's hard to reverse or others must follow it
   ([decisions](../decisions/README.md)). Refunds needed none.
6. **Implement the business logic in a service** (below).
7. **Validation, authorization and errors** go in the service. Use `actor.require(...,
   branch=...)`, `ValidationError(fields=...)`, and `DomainError(code=...)`.
8. **Write the tests** together with the code ([testing](../standards/testing.md#what-every-change-must-test)).
9. **API and docs:** the endpoint, README "What exists so far" (or its section), and
   translations.
10. **Run the checks:** `python ops/check.py`.
11. **Review your own diff** against the [review checklist](../../CONTRIBUTING.md#review-checklist):
    duplication, coupling, security, regressions.
12. **Open the pull request** ([CONTRIBUTING](../../CONTRIBUTING.md#pull-requests)).

## The example, file by file

### Model: `apps/expenses/models.py`

```python
class ExpenseRefund(Document):          # number, status, branch, business_date, posted/voided…
    voucher = models.ForeignKey(ExpenseVoucher, on_delete=models.PROTECT, related_name="refunds")
    cash_box = models.ForeignKey("treasury.CashBox", null=True, blank=True,
                                 on_delete=models.PROTECT, related_name="+")
    bank_account = models.ForeignKey(...)
    currency = models.ForeignKey("catalog.Currency", on_delete=models.PROTECT, related_name="+")
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    functional_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    journal_entry = models.ForeignKey("ledger.JournalEntry", null=True, blank=True,
                                      on_delete=models.PROTECT, related_name="+")
```

- It is a `Document` (tenant-scoped, numbered, cancellable).
- Every foreign key uses `PROTECT`.
- Amounts are Decimal, with the schema's precisions.
- Invariants are database constraints: exactly one of box or bank, and the amount above zero.
  See `ExpenseVoucher.Meta` for the same pattern.

### Migrations: `0004_refunds.py`, `0005_refunds_rls.py`

- `makemigrations expenses --name refunds` generates the table.
- A second migration adds isolation:

  ```python
  operations = [EnableTenantRLS("ExpenseRefund"), GuardTenantFKs("ExpenseRefund")]
  ```

- Forget it and `tests/isolation/test_model_coverage.py` fails.

### Service: `apps/expenses/services.py`

```python
def refund_expense(voucher_id: int, amount, *, method: str, cash_box_id=None,
                   bank_account_id=None, note: str = "", actor=None) -> ExpenseRefund:
    with transaction.atomic():
        voucher = (ExpenseVoucher.objects.select_for_update(of=("self",))   # refunds queue up
                   .select_related("branch", "category__account", "currency")
                   .filter(pk=voucher_id).first())
        ...
        actor.require("expenses.voucher.create", branch=voucher.branch)
        # validate: posted voucher, amount > 0, not more than is left (refunded())
        # resolve where the money goes: treasury.holders.resolve_tender(...)
        # number: allocate_number("XR", ...); post_entry(Dr holder, Cr expense account)
```

Things to notice:

- **The voucher is locked first.** Two refunds of the same voucher wait for each other, so
  "never more than is left" holds.
- **The ledger effect is described in the module docstring** and posted through `post_entry`.
- **Cancelling is `void_refund`:** lock, check it's posted, `reverse_entry`, set
  `voided_at/by/reason`. The voucher's own `void_expense` refuses while refunds stand.

### Permissions: `apps/expenses/permissions.py`

- Refunds reuse `expenses.voucher.create` and `expenses.void`.
- A new act that needs its own permission adds a line to `register_permissions(...)`, plus an
  Arabic translation.

### API: `apps/expenses/api.py`

```python
class VoucherViewSet(viewsets.GenericViewSet):
    required_permissions = {..., "refund": "expenses.voucher.create"}

    @action(detail=True, methods=["post"])
    @idempotent                                  # money moves: required
    def refund(self, request, pk=None):
        payload = RefundWriteSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        d = payload.validated_data
        refund = services.refund_expense(int(pk), d["amount"], method=d["method"], ...,
                                         actor=request.actor)
        ...

class RefundViewSet(viewsets.GenericViewSet):
    required_permissions = {"void": "expenses.void"}
```

### Linking and pages

- **Linking.** `ExpensesConfig.ready()` calls
  `register_document("expenses.ExpenseRefund", "expense-refund-view", _("Expense refund"))`, so
  statements and the ledger can link to a refund.
- **The page.** Refunds are shown and made on the voucher's page (`expenses/detail.html`),
  whose forms post to the API with `data-api-form`. `views.refund_detail`
  (`@permission_required("expenses.view")`, routed in `config/urls.py` as
  `expenses/refunds/<int:pk>/`) redirects there. It finds the refund through the
  branch-filtered `_visible(request)`, so an id from another branch is a 404.
- **Translations.** Run `python ops/i18n/messages.py extract`, translate the new entries in
  `locale/ar/LC_MESSAGES/django.po`, then run `compile`.

### Tests: `apps/expenses/tests/test_refunds.py`

- `test_partial_refunds`: refund part, then the rest. A refund over what is left is refused.
  Balances and the trial balance are right; cancelling a refund restores them; the voucher
  can't be cancelled while refunds stand.
- `test_reports_and_pages`: the pages render, and reports show refunds.
- **Still missing for a new feature today:** a permission refusal, a branch-scope refusal, and
  an idempotent replay ([testing](../standards/testing.md#what-every-change-must-test)). Add
  them in yours.

## A template to start from

Copy this checklist into your pull request description and tick it:

```markdown
- [ ] Acceptance criteria written as examples (with cancel, permission and branch cases)
- [ ] Model(s): Document / TenantScopedModel, PROTECT, Decimal precisions, constraints, tenant index
- [ ] Migration + EnableTenantRLS + GuardTenantFKs (+ AuditModel for master data)
- [ ] Service: atomic, lock-then-check, actor.require(branch=), apps.core.errors, ledger via post_entry
- [ ] Cancel path (void_*) reverses; refusals once it can't be cancelled
- [ ] Permissions registered (+ Arabic)
- [ ] API: required_permissions, branch-filtered queryset, @idempotent for money/stock
- [ ] Page(s) with @permission_required, data-api-form, added to tests/test_i18n_and_pages.py
- [ ] register_document if it appears in statements
- [ ] Tests: success, each refusal, cancel, replay, permission, branch
- [ ] Translations extracted, translated, compiled
- [ ] README / docs updated; debt register if something is left undone
- [ ] python ops/check.py passes
```
