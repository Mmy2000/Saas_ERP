from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class HrConfig(AppConfig):
    name = "apps.hr"
    label = "hr"
    verbose_name = "Employees and payroll"

    def ready(self):
        from apps.core.documents import register_document

        register_document("hr.EmployeeAdvance", "employee-advance", _("Advance"))
        register_document("hr.PayrollRun", "payroll", _("Payroll"))
