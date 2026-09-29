"""Tenant resolution from the Host header (§5.4) and the per-request tenant transaction (§5.5)."""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404

from apps.platform.tenants.models import TenantStatus
from apps.platform.tenants.selectors import tenant_for_host

from .context import tenant_context


class TenantResolutionMiddleware:
    """Host → tenant, then run the rest of the request inside `tenant_context`.

    The tenant only ever comes from the Host header: never from the body, query string,
    another header or a cookie. Unknown hosts get 404, suspended tenants 403.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.platform_hosts = {h.lower() for h in settings.PLATFORM_HOSTS}

    def __call__(self, request):
        host = request.get_host().rsplit(":", 1)[0].lower()

        if host in self.platform_hosts:
            request.tenant = None
            request.urlconf = settings.PLATFORM_URLCONF
            return self.get_response(request)

        tenant = tenant_for_host(host)
        if tenant is None or tenant.status in (TenantStatus.ARCHIVED, TenantStatus.PURGED):
            raise Http404("Unknown host")
        if tenant.status != TenantStatus.ACTIVE:
            # provisioning / migrating / suspended: no end-user access (§5.7).
            raise PermissionDenied("This workspace is not available.")

        request.tenant = tenant
        with tenant_context(tenant.id):
            response = self.get_response(request)
            if response.status_code >= 500:
                # The inner handler already turned the exception into a response, so the
                # atomic block would otherwise commit whatever ran before the failure.
                transaction.set_rollback(True)
        return response
