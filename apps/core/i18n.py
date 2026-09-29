"""Per-request language: the viewer's choice (cookie) first, then the tenant's default locale,
then settings.LANGUAGE_CODE. Replaces django.middleware.locale.LocaleMiddleware; URLs carry no
language prefix because the host already identifies the tenant."""

from __future__ import annotations

from django.conf import settings
from django.core.cache import cache
from django.utils import translation
from django.utils.cache import patch_vary_headers

SUPPORTED = {code for code, _ in settings.LANGUAGES}
TENANT_LOCALE_CACHE_SECONDS = 300


def tenant_locale_cache_key(tenant_id: int) -> str:
    return f"t:{tenant_id}:locale"


def _tenant_default(tenant) -> str | None:
    from apps.org.models import TenantProfile

    key = tenant_locale_cache_key(tenant.id)
    locale = cache.get(key)
    if locale is None:
        locale = TenantProfile.objects.values_list("locale", flat=True).first() or ""
        cache.set(key, locale, TENANT_LOCALE_CACHE_SECONDS)
    return locale or None


def resolve_language(request) -> str:
    chosen = request.COOKIES.get(settings.LANGUAGE_COOKIE_NAME)
    if chosen in SUPPORTED:
        return chosen
    tenant = getattr(request, "tenant", None)
    if tenant is not None:
        default = _tenant_default(tenant)
        if default in SUPPORTED:
            return default
    return settings.LANGUAGE_CODE


class TenantLocaleMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        language = resolve_language(request)
        translation.activate(language)
        request.LANGUAGE_CODE = translation.get_language()
        response = self.get_response(request)
        patch_vary_headers(response, ("Cookie",))
        response.headers.setdefault("Content-Language", request.LANGUAGE_CODE)
        return response
