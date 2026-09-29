"""Base class for management commands that act on tenant data (§5.6).

There is no implicit tenant: the command requires `--tenant <slug>` or `--all-tenants`.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from .context import tenant_context


class TenantCommand(BaseCommand):
    def add_arguments(self, parser):
        target = parser.add_mutually_exclusive_group(required=True)
        target.add_argument("--tenant", metavar="SLUG", help="Run for this tenant only.")
        target.add_argument(
            "--all-tenants", action="store_true", help="Run once for every active tenant."
        )
        self.add_tenant_arguments(parser)

    def add_tenant_arguments(self, parser):
        """Override to add command-specific arguments."""

    def handle(self, *args, **options):
        from apps.platform.tenants.models import Tenant, TenantStatus

        if options["all_tenants"]:
            tenants = list(Tenant.objects.filter(status=TenantStatus.ACTIVE).order_by("slug"))
        else:
            tenants = list(Tenant.objects.filter(slug=options["tenant"]))
            if not tenants:
                raise CommandError(f"Unknown tenant: {options['tenant']}")

        command_options = {k: v for k, v in options.items() if k not in ("tenant", "all_tenants")}
        for tenant in tenants:
            self._prefix = f"[{tenant.slug}] "
            with tenant_context(tenant.id):
                self.handle_tenant(tenant, **command_options)

    def handle_tenant(self, tenant, **options):
        raise NotImplementedError

    def log(self, message: str) -> None:
        self.stdout.write(f"{getattr(self, '_prefix', '')}{message}")
