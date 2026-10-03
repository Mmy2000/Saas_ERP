"""Counts every request to a client's workspace and enforces its requests-per-minute limit."""

from __future__ import annotations

import time

from django.conf import settings
from django.core.exceptions import DisallowedHost
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.utils.translation import gettext as _

from . import traffic
from .models import TenantStatus
from .selectors import tenant_for_host


class TrafficMiddleware:
    """Sits before TenantResolutionMiddleware, so a refused request never opens the tenant
    transaction and the counters are written outside it. Platform hosts, static files and
    unknown hosts are not counted."""

    def __init__(self, get_response):
        self.get_response = get_response
        self.platform_hosts = {h.lower() for h in settings.PLATFORM_HOSTS}
        self.skip_prefixes = ("/" + settings.STATIC_URL.lstrip("/"), "/favicon.ico")

    def __call__(self, request):
        tenant = self._tenant(request)
        if tenant is None:
            return self.get_response(request)

        minute = traffic.current_minute()
        if traffic.is_over_limit(tenant, minute):
            traffic.record(tenant.id, minute, throttled=True, status=429)
            return self._too_many(request, minute)

        started = time.perf_counter()
        response = self.get_response(request)
        ms = int((time.perf_counter() - started) * 1000)
        traffic.record(tenant.id, minute, route=self._route(request), ms=ms,
                       status=response.status_code)
        return response

    def _tenant(self, request):
        if request.path.startswith(self.skip_prefixes):
            return None
        try:
            host = request.get_host().rsplit(":", 1)[0].lower()
        except DisallowedHost:
            return None
        if host in self.platform_hosts:
            return None
        tenant = tenant_for_host(host)
        return tenant if tenant and tenant.status == TenantStatus.ACTIVE else None

    @staticmethod
    def _route(request) -> str:
        match = getattr(request, "resolver_match", None)
        pattern = "/" + (match.route or "") if match else "(other)"  # (other): 404s
        return f"{request.method} {pattern}"

    @staticmethod
    def _too_many(request, minute):
        retry_after = max(1, 60 - int((timezone.now() - minute).total_seconds()))
        message = _("Too many requests. Please wait a minute and try again.")
        if request.path.startswith("/api/"):
            response = JsonResponse({"error": {
                "code": "THROTTLED", "message": message, "fields": {},
                "request_id": getattr(request, "request_id", None)}}, status=429)
        else:
            response = render(request, "429.html", {"retry_after": retry_after}, status=429)
        response["Retry-After"] = str(retry_after)
        return response
