"""Theme (light / dark / system) and accent colour.

Each user picks both in the appearance menu (cookies, so the server renders the right theme
with no flash). The accent falls back to the workspace's own colour (TenantProfile.accent, set
in the platform console), then to gold. The palettes themselves are CSS: assets/css/app.css.
"""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

THEME_COOKIE = "gweb_theme"
ACCENT_COOKIE = "gweb_accent"
SIDEBAR_COOKIE = "gweb_sidebar"  # "collapsed" = icon rail (desktop)
THEMES = ("light", "dark", "system")
DEFAULT_ACCENT = "gold"

# key, label, swatch (the palette's 500/600 colour, shown in the menu)
ACCENTS = [
    ("gold", _("Gold"), "#c28a30"),
    ("emerald", _("Emerald"), "#059669"),
    ("teal", _("Teal"), "#0d9488"),
    ("blue", _("Blue"), "#2563eb"),
    ("indigo", _("Indigo"), "#4f46e5"),
    ("violet", _("Violet"), "#7c3aed"),
    ("rose", _("Rose"), "#e11d48"),
    ("graphite", _("Graphite"), "#3f3f46"),
]
ACCENT_KEYS = frozenset(key for key, _label, _swatch in ACCENTS)
ACCENT_CHOICES = [(key, label) for key, label, _swatch in ACCENTS]


def accent_swatch(key: str) -> str:
    """The colour of an accent key (the default accent's for an unknown one)."""
    swatches = {k: swatch for k, _label, swatch in ACCENTS}
    return swatches.get(key) or swatches[DEFAULT_ACCENT]


def _workspace_accent(request) -> str:
    """The client's own colour. Tenant hosts render inside the tenant transaction."""
    from .branding import workspace_profile

    accent = (workspace_profile(request) or {}).get("accent")
    return accent if accent in ACCENT_KEYS else DEFAULT_ACCENT


def appearance(request):
    theme = request.COOKIES.get(THEME_COOKIE, "system")
    if theme not in THEMES:
        theme = "system"
    chosen = request.COOKIES.get(ACCENT_COOKIE, "")
    if chosen not in ACCENT_KEYS:
        chosen = ""
    default = _workspace_accent(request)
    return {"ui": {
        "theme": theme,  # the choice; "system" is resolved in the browser before first paint
        "accent": chosen or default,
        "chosen_accent": chosen,  # "" = follow the workspace
        "default_accent": default,
        "default_accent_label": dict(ACCENT_CHOICES)[default],
        "accents": ACCENTS,
        "workspace": getattr(request, "tenant", None) is not None,
        "sidebar_collapsed": request.COOKIES.get(SIDEBAR_COOKIE) == "collapsed",
    }}
