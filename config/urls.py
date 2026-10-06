"""URLs served on tenant hosts. The Django admin is deliberately absent (platform host only)."""

from django.contrib.auth import views as auth_views
from django.urls import include, path

from apps.audit import views as audit
from apps.catalog.web import manage as catalog_manage
from apps.catalog.web import views as catalog
from apps.core import media
from apps.diamonds import views as diamonds
from apps.expenses import views as expenses
from apps.hr import views as hr
from apps.iam.web import views as iam
from apps.inventory.web import views as inventory
from apps.ledger.web import views as ledger
from apps.manufacturing import views as manufacturing
from apps.org.web import company
from apps.org.web import views as org
from apps.parties.web import views as parties
from apps.pricing.web import views as pricing
from apps.printing import design_views as documents
from apps.printing import designer_views as designer
from apps.printing import views as printing
from apps.purchasing.web import return_views as supplier_returns
from apps.purchasing.web import scrap_views as scrap
from apps.purchasing.web import views as purchasing
from apps.repairs import views as repairs
from apps.reports import views as reports
from apps.sales.web import trade_views as trade
from apps.sales.web import views as sales
from apps.settlements import views as settlements
from apps.treasury import views as treasury

urlpatterns = [
    path("", org.home, name="home"),
    path("login/", auth_views.LoginView.as_view(redirect_authenticated_user=True), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("i18n/", include("django.conf.urls.i18n")),
    path("media/<path:path>", media.serve, name="media"),

    path("settings/company/", company.company, name="company"),
    path("settings/documents/", documents.designs, name="document-designs"),
    path("settings/documents/<str:doc_type>/", documents.design_edit, name="document-design"),
    path("settings/documents/<str:doc_type>/preview/", documents.design_preview,
         name="document-design-preview"),
    path("settings/documents/<str:doc_type>/designer/", designer.designer,
         name="document-designer"),
    path("settings/documents/<str:doc_type>/designer/preview/", designer.preview,
         name="document-designer-preview"),
    path("settings/documents/<str:doc_type>/designer/save/", designer.save,
         name="document-designer-save"),
    path("branches/", org.branches, name="branches"),
    path("branches/new/", org.branch_new, name="branch-new"),
    path("branches/<int:pk>/", org.branch_edit, name="branch-edit"),
    path("settings/activity/", audit.activity, name="activity"),
    path("settings/users/", iam.users, name="users"),
    path("settings/users/new/", iam.user_new, name="user-new"),
    path("settings/users/<int:pk>/", iam.user_edit, name="user-edit"),
    path("settings/roles/", iam.roles, name="roles"),
    path("settings/roles/new/", iam.role_new, name="role-new"),
    path("settings/roles/<int:pk>/", iam.role_edit, name="role-edit"),
    path("catalog/karats/", catalog.karats, name="karats"),
    path("catalog/karats/new/", catalog_manage.karat_new, name="karat-new"),
    path("catalog/karats/<int:pk>/", catalog_manage.karat_edit, name="karat-edit"),
    path("catalog/categories/", catalog.categories, name="categories"),
    path("catalog/categories/new/", catalog_manage.category_new, name="category-new"),
    path("catalog/categories/<int:pk>/", catalog_manage.category_edit, name="category-edit"),
    path("pricing/gold/", pricing.gold_prices, name="gold-prices"),
    path("pricing/fx/", pricing.fx_rates, name="fx-rates"),
    path("customers/", parties.customers, name="customers"),
    path("customers/new/", parties.customer_new, name="customer-new"),
    path("customers/<int:pk>/", parties.customer_edit, name="customer-edit"),
    path("customers/<int:pk>/statement/", parties.customer_statement, name="customer-statement"),
    path("suppliers/", parties.suppliers, name="suppliers"),
    path("suppliers/new/", parties.supplier_new, name="supplier-new"),
    path("suppliers/<int:pk>/", parties.supplier_edit, name="supplier-edit"),
    path("suppliers/<int:pk>/statement/", parties.supplier_statement, name="supplier-statement"),
    path("trade-accounts/", parties.trade_accounts, name="trade-accounts"),
    path("trade-accounts/new/", parties.trade_account_new, name="trade-account-new"),
    path("trade-accounts/<int:pk>/", parties.trade_account_edit, name="trade-account-edit"),
    path("trade-accounts/<int:pk>/statement/", parties.trade_account_statement,
         name="trade-account-statement"),
    path("hr/", hr.employees, name="employees"),
    path("hr/new/", hr.employee_new, name="employee-new"),
    path("hr/<int:pk>/", hr.employee, name="employee"),
    path("hr/advances/<int:pk>/", hr.advance, name="employee-advance"),
    path("hr/payroll/", hr.payrolls, name="payrolls"),
    path("hr/payroll/new/", hr.payroll_new, name="payroll-new"),
    path("hr/payroll/<int:pk>/", hr.payroll, name="payroll"),
    path("hr/commissions/", hr.commissions, name="commissions"),
    path("repairs/", repairs.repairs, name="repairs"),
    path("repairs/new/", repairs.repair_new, name="repair-new"),
    path("repairs/<int:pk>/", repairs.repair, name="repair"),
    path("manufacturing/", manufacturing.work_orders, name="work-orders"),
    path("manufacturing/new/", manufacturing.work_order_new, name="work-order-new"),
    path("manufacturing/<int:pk>/", manufacturing.work_order, name="work-order"),
    path("diamonds/", diamonds.stock, name="diamonds"),
    path("diamonds/receive/", diamonds.receive, name="diamonds-receive-form"),
    path("diamonds/settings/", diamonds.settings_list, name="stone-settings"),
    path("diamonds/settings/new/", diamonds.setting_new, name="stone-setting-new"),
    path("diamonds/settings/<int:pk>/", diamonds.setting, name="stone-setting"),
    path("production/", manufacturing.production, name="production"),
    path("production/new/", manufacturing.production_new, name="production-new"),
    path("production/<int:pk>/", manufacturing.work_order, name="production-order"),
    path("workshops/", parties.workshops, name="workshops"),
    path("workshops/new/", parties.workshop_new, name="workshop-new"),
    path("workshops/<int:pk>/", parties.workshop_edit, name="workshop-edit"),
    path("workshops/<int:pk>/statement/", parties.workshop_statement,
         name="workshop-statement"),

    path("sales/", sales.invoices, name="sales"),
    path("sales/new/", sales.new_sale, name="sale-new"),
    path("sales/<int:pk>/", sales.invoice_detail, name="sale-detail"),
    path("sales/<int:pk>/print/", sales.invoice_print, name="sale-print"),
    path("print/<str:doc_type>/<int:pk>/", documents.print_document, name="print-document"),
    path("sales/returns/<int:pk>/", sales.return_detail, name="sale-return-detail"),
    path("sales/reservations/", sales.reservation_list, name="reservations"),
    path("sales/reservations/new/", sales.reservation_new, name="reservation-new"),
    path("sales/reservations/<int:pk>/", sales.reservation_detail, name="reservation"),
    path("sales/wholesale/", trade.trade_sales, name="trade-sales"),
    path("sales/wholesale/new/", trade.trade_sale_new, name="trade-sale-new"),
    path("sales/wholesale/<int:pk>/", trade.trade_sale, name="trade-sale"),
    path("sales/wholesale/returns/<int:pk>/", trade.trade_return, name="trade-return"),
    path("settlements/", settlements.settlements, name="settlements"),
    path("settlements/new/", settlements.new_settlement, name="settlement-new"),
    path("settlements/<int:pk>/", settlements.settlement_detail, name="settlement-view"),
    path("treasury/", treasury.overview, name="treasury"),
    path("treasury/movements/", treasury.documents, name="treasury-documents"),
    path("treasury/movements/new/", treasury.document_new, name="treasury-document-new"),
    path("treasury/movements/<int:pk>/", treasury.document_detail, name="treasury-document"),
    path("treasury/counts/", treasury.cash_counts, name="cash-counts"),
    path("treasury/counts/new/", treasury.cash_count_new, name="cash-count-new"),
    path("treasury/counts/<int:pk>/", treasury.cash_count, name="cash-count"),
    path("treasury/cheques/", treasury.cheques, name="cheques"),
    path("treasury/cheques/new/", treasury.cheque_new, name="cheque-new"),
    path("treasury/cheques/<int:pk>/", treasury.cheque_detail, name="cheque"),
    path("treasury/reconcile/<int:pk>/", treasury.reconcile, name="reconcile"),
    path("treasury/<str:kind>/new/", treasury.holder_new, name="treasury-holder-new"),
    path("treasury/<str:kind>/<int:pk>/", treasury.holder_edit, name="treasury-holder-edit"),
    path("treasury/<str:kind>/<int:pk>/statement/", treasury.holder_statement,
         name="treasury-holder-statement"),
    path("expenses/", expenses.expenses, name="expenses"),
    path("expenses/new/", expenses.expense_new, name="expense-new"),
    path("expenses/categories/", expenses.categories, name="expense-categories"),
    path("expenses/<int:pk>/", expenses.expense_detail, name="expense-view"),
    path("stock/", inventory.stock, name="stock"),
    path("stock/items/<int:pk>/", inventory.item_detail, name="stock-item"),
    path("reports/", reports.index, name="reports"),
    path("reports/<slug:code>/", reports.run_report, name="report"),
    path("stock/labels/", printing.labels, name="labels"),
    path("stock/labels/zpl/", printing.labels_zpl, name="labels-zpl"),
    path("settings/labels/", printing.label_templates, name="label-templates"),
    path("settings/labels/new/", printing.label_template_new, name="label-template-new"),
    path("settings/labels/preview/", printing.label_preview, name="label-preview"),
    path("settings/labels/<int:pk>/", printing.label_template_edit, name="label-template-edit"),
    path("stock/transfers/", inventory.transfers, name="stock-transfers"),
    path("stock/transfers/new/", inventory.transfer_new, name="stock-transfer-new"),
    path("stock/transfers/<int:pk>/", inventory.transfer_detail, name="stock-transfer"),
    path("stock/stocktakes/", inventory.stocktake_list, name="stocktakes"),
    path("stock/stocktakes/new/", inventory.stocktake_new, name="stocktake-new"),
    path("stock/stocktakes/<int:pk>/", inventory.stocktake_detail, name="stocktake"),
    path("purchasing/", purchasing.invoices, name="invoices"),
    path("purchasing/new/", purchasing.invoice_new, name="invoice-new"),
    path("purchasing/returns/", supplier_returns.supplier_returns, name="supplier-returns"),
    path("purchasing/returns/new/", supplier_returns.supplier_return_new,
         name="supplier-return-new"),
    path("purchasing/returns/<int:pk>/", supplier_returns.supplier_return, name="supplier-return"),
    path("purchasing/scrap/", scrap.scrap, name="scrap"),
    path("purchasing/scrap/buy/", scrap.scrap_buy, name="scrap-buy"),
    path("purchasing/scrap/sell/", scrap.scrap_sell, name="scrap-sell"),
    path("purchasing/scrap/bought/<int:pk>/", scrap.scrap_purchase, name="scrap-purchase"),
    path("purchasing/scrap/sold/<int:pk>/", scrap.scrap_sale, name="scrap-sale"),
    path("purchasing/<int:pk>/", purchasing.invoice_detail, name="invoice-detail"),
    path("purchasing/<int:pk>/edit/", purchasing.invoice_edit, name="invoice-edit"),
    path("accounting/accounts/", ledger.accounts, name="accounts"),
    path("accounting/journal/", ledger.journal, name="journal"),
    path("accounting/journal/new/", ledger.journal_new, name="journal-new"),
    path("accounting/trial-balance/", ledger.trial_balance_view, name="trial-balance"),
    path("accounting/profit-and-loss/", reports.run_report, {"code": "profit_loss"},
         name="profit-loss"),
    path("accounting/balance-sheet/", reports.run_report, {"code": "balance_sheet"},
         name="balance-sheet"),
    path("accounting/periods/", ledger.periods, name="periods"),
    path("accounting/periods/<int:year>/<int:month>/", ledger.period, name="period"),

    path("api/v1/", include("config.api_urls")),
]
