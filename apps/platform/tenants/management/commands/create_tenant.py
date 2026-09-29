from getpass import getpass

from django.core.management.base import BaseCommand, CommandError

from apps.core.errors import AppError
from apps.platform.tenants.services import ProvisionTenantCommand, provision_tenant


class Command(BaseCommand):
    help = "Provision a tenant: domain, profile, head-office branch and owner membership."

    def add_arguments(self, parser):
        parser.add_argument("--slug", required=True)
        parser.add_argument("--name", required=True)
        parser.add_argument("--domain", help="Defaults to <slug>.localhost")
        parser.add_argument("--owner-username", required=True)
        parser.add_argument("--owner-email")
        parser.add_argument("--owner-password", help="Prompted for when omitted.")
        parser.add_argument("--currency", default="EGP")
        parser.add_argument("--timezone", default="Africa/Cairo")
        parser.add_argument("--locale", default="ar", choices=["ar", "en"])
        parser.add_argument("--country", default="EG")

    def handle(self, *args, **opts):
        password = opts["owner_password"] or getpass("Owner password: ")
        if not password:
            raise CommandError("An owner password is required.")
        try:
            tenant = provision_tenant(
                ProvisionTenantCommand(
                    slug=opts["slug"],
                    name=opts["name"],
                    domain=opts["domain"] or f"{opts['slug']}.localhost",
                    owner_username=opts["owner_username"],
                    owner_email=opts["owner_email"],
                    owner_password=password,
                    functional_currency=opts["currency"],
                    timezone=opts["timezone"],
                    locale=opts["locale"],
                    country=opts["country"],
                )
            )
        except AppError as exc:
            raise CommandError(exc.message) from exc
        self.stdout.write(self.style.SUCCESS(f"Tenant '{tenant.slug}' is {tenant.status}."))
