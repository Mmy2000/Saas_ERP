"""Delete old traffic counters. Run daily (cron / Task Scheduler):

    python manage.py prune_traffic            # per-minute rows older than 30 days, routes 90
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.platform.tenants.models import TenantRouteTraffic, TenantTraffic


class Command(BaseCommand):
    help = "Delete per-minute traffic older than --days, per-route traffic older than --route-days."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=30)
        parser.add_argument("--route-days", type=int, default=90)

    def handle(self, *args, days, route_days, **options):
        minutes, _ = TenantTraffic.objects.filter(
            minute__lt=timezone.now() - timedelta(days=days)).delete()
        routes, _ = TenantRouteTraffic.objects.filter(
            day__lt=timezone.localdate() - timedelta(days=route_days)).delete()
        self.stdout.write(f"deleted {minutes} minute rows, {routes} route rows")
