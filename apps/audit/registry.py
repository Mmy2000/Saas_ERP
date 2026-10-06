"""The master data whose every change is recorded. Documents are not here: posted documents are
never edited (they are cancelled, which they record themselves), and stock and ledger rows are
append-only.

model label → columns to leave out. Items: only edits are recorded (purchases create them by
the hundred, and sales and transfers change their status and branch, which stock movements
already record).
"""

AUDITED: dict[str, tuple[str, ...]] = {
    "catalog.Currency": (),
    "catalog.Karat": (),
    "catalog.ItemCategory": (),
    "catalog.CategoryMakingCharge": (),
    "pricing.FxRate": (),
    "pricing.MetalPriceBoard": (),
    "pricing.MetalPriceBoardLine": (),
    "parties.Party": (),
    "parties.PartyRole": (),
    "parties.CustomerProfile": (),
    "parties.SupplierProfile": (),
    "org.TenantProfile": (),
    "org.Branch": (),
    "iam.Membership": (),
    "iam.Role": (),
    "iam.RolePermission": (),
    "iam.MembershipRole": (),
    "iam.MembershipRoleBranch": (),
    "iam.MembershipLimit": (),
    "treasury.CashBox": (),
    "treasury.BankAccount": (),
    "treasury.BankAccountBranch": (),
    "treasury.CardTerminal": (),
    "ledger.Account": (),
    "hr.Employee": (),
    "expenses.ExpenseCategory": (),
    "printing.LabelTemplate": (),
    "printing.DocumentDesign": (),
    "inventory.Item": ("status", "branch_id"),
    "diamonds.ItemStone": (),  # trigger installed by diamonds.0002 (AuditModel)
}
UPDATES_ONLY = {"inventory.Item"}
