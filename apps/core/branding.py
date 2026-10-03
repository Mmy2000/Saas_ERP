"""Who the screens say they belong to: the platform's branding (console → Settings) and, on a
client's hosts, the client's own name and logo (TenantProfile, set by the client or in the
console)."""

from __future__ import annotations

from django.core.files.storage import default_storage


def workspace_profile(request) -> dict | None:
    """The client's display name, accent and logo, read once per request."""
    if getattr(request, "tenant", None) is None:
        return None
    if not hasattr(request, "_workspace_profile"):
        from apps.org.models import TenantProfile

        request._workspace_profile = TenantProfile.objects.values(
            "display_name", "accent", "logo").first() or {}
    return request._workspace_profile


def _url(name: str) -> str:
    return default_storage.url(name) if name else ""


def branding(request):
    from apps.platform.tenants.models import PlatformSettings

    platform = PlatformSettings.load()
    logo = _url(platform.logo.name if platform.logo else "")
    logo_dark = _url(platform.logo_dark.name if platform.logo_dark else "") or logo
    profile = workspace_profile(request) or {}
    workspace_logo = _url(profile.get("logo") or "")
    return {"brand": {
        "name": platform.brand_name,
        "tagline": platform.tagline or platform.brand_name,
        "logo": logo,
        "logo_dark": logo_dark,
        "footer_text": platform.footer_text,
        "links": getattr(platform, "cached_links", []),
        "workspace_name": profile.get("display_name", ""),
        "workspace_logo": workspace_logo,
        # Browser tab icon: the client's logo on its hosts, else the platform's.
        "favicon": workspace_logo or logo,
    }}
