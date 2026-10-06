"""Default chart of accounts for a jewelry business (§7.10), used when a tenant is provisioned.

Plain data so that both the runtime seeding service and data migrations can read it. Each row:
(code, parent code, English name, type, nature, postable, subledger, commodity scope, role).
`role` binds the account to a posting role (e.g. "customers") that posting rules look up; a
tenant can later point a role at another account. Names are translated at display time.
"""

from django.utils.translation import gettext_lazy as _

A, L, E, INC, X = "asset", "liability", "equity", "income", "expense"
DR, CR = "debit", "credit"
ANY, MONEY, METAL = "any", "money", "metal"
NONE, PARTY = "none", "party"

CHART = [
    # code,  parent, name,                                   type, nature, postable, subledger, scope, role
    ("1",    None,  _("Assets"),                              A, DR, False, NONE, ANY, ""),
    ("11",   "1",   _("Cash and banks"),                      A, DR, False, NONE, ANY, ""),
    # Groups: each cash box, bank account and card terminal gets its own account under these
    # (apps.treasury). The role names the group new holders are created in.
    ("1101", "11",  _("Cash on hand"),                        A, DR, False, NONE, MONEY, "cash"),
    ("1102", "11",  _("Bank accounts"),                       A, DR, False, NONE, MONEY, "bank"),
    ("1103", "11",  _("Card settlements receivable"),         A, DR, False, NONE, MONEY, "card_receivable"),
    ("1104", "11",  _("Cheques received"),                    A, DR, True, NONE, MONEY, "cheques_received"),
    ("12",   "1",   _("Receivables"),                         A, DR, False, NONE, ANY, ""),
    ("1201", "12",  _("Customers"),                           A, DR, True, PARTY, ANY, "customers"),
    ("1202", "12",  _("Trade accounts"),                      A, DR, True, PARTY, ANY, "trade_accounts"),
    ("1203", "12",  _("Employee advances"),                   A, DR, True, NONE, MONEY, "employee_advances"),
    ("13",   "1",   _("Inventory"),                           A, DR, False, NONE, ANY, ""),
    ("1301", "13",  _("Gold inventory"),                      A, DR, True, NONE, ANY, "inventory_gold"),
    ("1302", "13",  _("Scrap gold"),                          A, DR, True, NONE, ANY, "inventory_scrap"),
    ("1303", "13",  _("Diamonds and stones"),                 A, DR, True, NONE, ANY, "inventory_diamonds"),
    ("1304", "13",  _("Goods at workshops"),                  A, DR, True, NONE, ANY, "inventory_at_workshop"),
    ("1305", "13",  _("Production in progress"),              A, DR, True, NONE, ANY, "inventory_in_production"),
    ("14",   "1",   _("Inter-branch"),                        A, DR, False, NONE, ANY, ""),
    ("1401", "14",  _("Branch clearing"),                     A, DR, True, NONE, ANY, "branch_clearing"),
    ("2",    None,  _("Liabilities"),                         L, CR, False, NONE, ANY, ""),
    ("21",   "2",   _("Payables"),                            L, CR, False, NONE, ANY, ""),
    ("2101", "21",  _("Suppliers"),                           L, CR, True, PARTY, ANY, "suppliers"),
    ("2102", "21",  _("Workshops"),                           L, CR, True, PARTY, ANY, "workshops"),
    ("2103", "21",  _("Customer deposits"),                   L, CR, True, PARTY, MONEY, "customer_deposits"),
    ("2104", "21",  _("Cheques payable"),                     L, CR, True, NONE, MONEY, "cheques_payable"),
    ("22",   "2",   _("Taxes"),                               L, CR, False, NONE, ANY, ""),
    ("2201", "22",  _("VAT payable"),                         L, CR, True, NONE, MONEY, "vat_payable"),
    ("3",    None,  _("Equity"),                              E, CR, False, NONE, ANY, ""),
    ("3101", "3",   _("Partners' capital"),                   E, CR, True, PARTY, ANY, "partners_capital"),
    ("3201", "3",   _("Retained earnings"),                   E, CR, True, NONE, ANY, "retained_earnings"),
    ("3301", "3",   _("Opening balances"),                    E, CR, True, NONE, ANY, "opening_equity"),
    # Metal bought or sold for money (scrap trade-ins, bullion): holds the metal side so that
    # every entry still balances per metal, like an FX position account.
    ("3401", "3",   _("Metal trading position"),              E, CR, True, NONE, ANY, "metal_position"),
    ("4",    None,  _("Income"),                            INC, CR, False, NONE, ANY, ""),
    ("4101", "4",   _("Gold sales"),                        INC, CR, True, NONE, ANY, "sales_gold"),
    ("4102", "4",   _("Making charges"),                    INC, CR, True, NONE, MONEY, "sales_making"),
    ("4103", "4",   _("Diamond sales"),                     INC, CR, True, NONE, MONEY, "sales_diamonds"),
    ("4104", "4",   _("Repair charges"),                    INC, CR, True, NONE, MONEY, "repair_income"),
    ("4201", "4",   _("Metal gains"),                       INC, CR, True, NONE, ANY, "metal_gain"),
    ("4301", "4",   _("Exchange gains"),                    INC, CR, True, NONE, MONEY, "fx_gain"),
    ("4302", "4",   _("Bank interest"),                     INC, CR, True, NONE, MONEY, "bank_interest"),
    ("5",    None,  _("Expenses"),                            X, DR, False, NONE, ANY, ""),
    ("5101", "5",   _("Cost of gold sold"),                   X, DR, True, NONE, ANY, "cogs_gold"),
    ("5102", "5",   _("Cost of diamonds sold"),               X, DR, True, NONE, MONEY, "cogs_diamonds"),
    ("5103", "5",   _("Repair costs"),                        X, DR, True, NONE, MONEY, "repair_costs"),
    ("5201", "5",   _("Operating expenses"),                  X, DR, True, NONE, MONEY, "expenses"),
    ("5202", "5",   _("Salaries"),                            X, DR, True, NONE, MONEY, "salaries"),
    ("5203", "5",   _("Card fees"),                           X, DR, True, NONE, MONEY, "card_fees"),
    ("5204", "5",   _("Sales commissions"),                   X, DR, True, NONE, MONEY, "commissions"),
    # In-house labour added to the cost of goods made (salaries are already an expense).
    ("5205", "5",   _("Production labour absorbed"),          X, CR, True, NONE, MONEY, "labour_absorbed"),
    ("5206", "5",   _("Bank charges"),                        X, DR, True, NONE, MONEY, "bank_charges"),
    ("5207", "5",   _("Cash over and short"),                 X, DR, True, NONE, MONEY, "cash_over_short"),
    ("5301", "5",   _("Metal losses"),                        X, DR, True, NONE, ANY, "metal_loss"),
    ("5302", "5",   _("Exchange losses"),                     X, DR, True, NONE, MONEY, "fx_loss"),
    ("5401", "5",   _("Rounding differences"),                X, DR, True, NONE, MONEY, "rounding"),
]

NAMES = {code: name for code, _parent, name, *_rest in CHART}

# Commodities every tenant starts with, besides its functional currency.
# (code, kind, currency code or metal code, decimal places)
METAL_COMMODITIES = [("XAU", "metal", "gold", 4), ("XAG", "metal", "silver", 4)]
