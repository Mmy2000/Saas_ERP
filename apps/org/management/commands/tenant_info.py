from apps.core.tenancy.commands import TenantCommand
from apps.iam.models import Membership
from apps.org.models import Branch


class Command(TenantCommand):
    help = "Print a one-line summary of a tenant (branches, members)."

    def handle_tenant(self, tenant, **options):
        self.log(
            f"{tenant.name}: status={tenant.status} "
            f"branches={Branch.objects.count()} members={Membership.objects.count()}"
        )
