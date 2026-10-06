from django.utils.translation import gettext_lazy as _

from apps.iam.catalog import register_permissions

register_permissions(_("Employees and payroll"), [
    ("hr.employee.view", _("View employees")),
    ("hr.employee.manage", _("Add and edit employees, their salaries and commissions")),
    ("hr.advance.create", _("Give advances to employees and add bonuses or deductions")),
    ("hr.advance.void", _("Cancel advances")),
    ("hr.payroll.view", _("View payroll and commissions")),
    ("hr.payroll.post", _("Pay the monthly payroll")),
    ("hr.payroll.void", _("Cancel a payroll")),
])
