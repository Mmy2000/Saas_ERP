from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class ExpensesConfig(AppConfig):
    name = "apps.expenses"
    label = "expenses"
    verbose_name = "Expenses"

    def ready(self):
        from apps.core.documents import register_document

        register_document("expenses.ExpenseVoucher", "expense-view", _("Expense"))
