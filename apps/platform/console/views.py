"""The platform console: served on PLATFORM_HOSTS only, for platform staff (§5.4, §5.7)."""

from __future__ import annotations

from datetime import timedelta
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.core.errors import ValidationError
from apps.core.media import replace_file
from apps.core.tenancy import tenant_context
from apps.platform.tenants import features as feature_registry
from apps.platform.tenants.models import (
    Plan,
    PlatformEvent,
    PlatformLink,
    PlatformSettings,
    Tenant,
    TenantDomain,
    TenantStatus,
)
from apps.platform.tenants.services import ProvisionTenantCommand, provision_tenant

from . import metrics
from . import traffic as traffic_report
from .forms import (
    DomainForm,
    PlanDeleteForm,
    PlanForm,
    PlatformLinkFormSet,
    PlatformSettingsForm,
    ProfileForm,
    StaffLoginForm,
    TenantCreateForm,
    TenantSettingsForm,
    TrafficLimitForm,
    domain_for,
)

LIMIT_FIELDS = ("plan", "trial_ends_on", "max_branches", "max_users", "contact_name",
                "contact_email", "contact_phone", "notes")
# The traffic pages' enable/disable switch moves a client between these two only.
ACCESS_STATUSES = (TenantStatus.ACTIVE, TenantStatus.SUSPENDED)
def _attr(name: str) -> str:
    """Form field → Tenant attribute (the plan is stored by its code)."""
    return "plan_id" if name == "plan" else name


# Which statuses can be set from the console, and from which.
TRANSITIONS = {
    TenantStatus.ACTIVE: (TenantStatus.SUSPENDED, TenantStatus.ARCHIVED,
                          TenantStatus.PROVISIONING),
    TenantStatus.SUSPENDED: (TenantStatus.ACTIVE,),
    TenantStatus.ARCHIVED: (TenantStatus.SUSPENDED,),
}


def staff_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"/login/?next={request.path}")
        if not request.user.is_platform_staff:
            raise PermissionDenied
        return view(request, *args, **kwargs)
    return wrapper


def record(request, action: str, tenant: Tenant | None = None, **detail) -> None:
    PlatformEvent.objects.create(actor=request.user, tenant=tenant, action=action,
                                 detail={k: str(v) for k, v in detail.items()})


class LoginView(auth_views.LoginView):
    template_name = "console/login.html"
    authentication_form = StaffLoginForm
    redirect_authenticated_user = True


def _tenant(pk: int) -> Tenant:
    tenant = Tenant.objects.prefetch_related("domains").filter(pk=pk).first()
    if tenant is None:
        raise Http404
    return tenant


def _primary_domain(tenant: Tenant) -> str | None:
    domain = next((d for d in tenant.domains.all() if d.is_primary), None)
    return domain.domain if domain else None


@staff_required
def home(request):
    data = metrics.overview()
    recent = PlatformEvent.objects.select_related("actor", "tenant")[:8]
    attention = sorted((u for u in data.tenants if u.health in ("idle", "new")
                        or u.tenant.status == TenantStatus.SUSPENDED),
                       key=lambda u: u.tenant.name)[:6]
    return render(request, "console/home.html", {
        "data": data, "recent": recent, "attention": attention, "section": "home",
    })


@staff_required
def tenants(request):
    term = request.GET.get("q", "").strip()
    status = request.GET.get("status", "")
    queryset = Tenant.objects.select_related("plan").prefetch_related("domains").order_by("name")
    if term:
        queryset = queryset.filter(Q(name__icontains=term) | Q(slug__icontains=term)
                                   | Q(contact_name__icontains=term)
                                   | Q(contact_email__icontains=term)
                                   | Q(domains__domain__icontains=term)).distinct()
    if status in TenantStatus.values:
        queryset = queryset.filter(status=status)
    plan_code = request.GET.get("plan", "")
    if plan_code:
        queryset = queryset.filter(plan_id=plan_code)
    page = Paginator(queryset, 25).get_page(request.GET.get("page"))
    usages = {}
    for tenant in page.object_list:
        if tenant.status in (TenantStatus.ACTIVE, TenantStatus.SUSPENDED):
            usages[tenant.pk] = metrics.tenant_usage(tenant)
    rows = [{"tenant": t, "usage": usages.get(t.pk), "domain": _primary_domain(t)}
            for t in page.object_list]
    return render(request, "console/tenants.html", {
        "page": page, "rows": rows, "term": term, "status": status,
        "statuses": TenantStatus.choices, "section": "tenants",
    })


@staff_required
def tenant_new(request):
    form = TenantCreateForm(request.POST or None,
                            initial={"plan": Tenant._meta.get_field("plan").get_default()})
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            tenant = provision_tenant(ProvisionTenantCommand(
                slug=d["slug"], name=d["name"], domain=domain_for(d["slug"]),
                owner_username=d["owner_username"], owner_password=d["owner_password"] or None,
                owner_email=d["owner_email"], owner_display_name=d["owner_display_name"],
                functional_currency=d["functional_currency"], timezone=d["timezone"],
                locale=d["locale"], country=d["country"], fineness_24k=d["fineness_24k"]))
        except ValidationError as exc:
            form.add_error(None, exc.message)
        else:
            for name in LIMIT_FIELDS:
                setattr(tenant, _attr(name), d[name] if d[name] is not None else
                        Tenant._meta.get_field(name).get_default())
            plan = Plan.objects.get(code=tenant.plan_id)
            if tenant.trial_ends_on is None and plan.trial_days:
                tenant.trial_ends_on = timezone.localdate() + timedelta(days=plan.trial_days)
            tenant.save()
            record(request, "tenant.created", tenant, slug=tenant.slug, plan=tenant.plan_id)
            messages.success(request, _("%(name)s is ready.") % {"name": tenant.name})
            return redirect("console-tenant", tenant.pk)
    return render(request, "console/tenant_new.html", {
        "form": form, "base_domain": domain_for(""), "section": "new"})


def _owners(tenant: Tenant) -> list:
    from apps.iam.models import Membership

    with tenant_context(tenant.id):
        return list(Membership.objects.select_related("user").filter(
            roles__role__code="owner").order_by("username"))


@staff_required
def tenant(request, pk):
    tenant = _tenant(pk)
    usage = (metrics.tenant_usage(tenant)
             if tenant.status in (TenantStatus.ACTIVE, TenantStatus.SUSPENDED) else None)
    profile = None
    if tenant.status not in (TenantStatus.PURGED,):
        from apps.org.models import TenantProfile

        with tenant_context(tenant.id):
            profile = TenantProfile.objects.first()
    settings_form = TenantSettingsForm(initial={
        "name": tenant.name, **{name: getattr(tenant, _attr(name)) for name in LIMIT_FIELDS}})
    profile_form = ProfileForm(initial={
        "display_name": profile.display_name, "locale": profile.locale,
        "country": profile.country, "timezone": profile.timezone,
        "accent": profile.accent, "logo": profile.logo or None} if profile else None)
    return render(request, "console/tenant.html", {
        "tenant": tenant, "usage": usage, "profile": profile,
        "domains": tenant.domains.order_by("-is_primary", "domain"),
        "primary": _primary_domain(tenant),
        "owners": _owners(tenant) if profile else [],
        "profile_logo_url": profile.logo.url if profile and profile.logo else "",
        "events": tenant.events.select_related("actor")[:12],
        "feature_states": feature_registry.states(tenant),
        "settings_form": settings_form, "profile_form": profile_form, "domain_form": DomainForm(),
        "transitions": [(s, TenantStatus(s).label) for s in TRANSITIONS
                        if tenant.status in TRANSITIONS[s]],
        "section": "tenants",
    })


def _invalid(request, form) -> None:
    for errors in form.errors.values():
        for error in errors:
            messages.error(request, error)


@staff_required
@require_POST
def tenant_settings(request, pk):
    tenant = _tenant(pk)
    form = TenantSettingsForm(request.POST, initial={"plan": tenant.plan_id})
    if not form.is_valid():
        _invalid(request, form)
        return redirect("console-tenant", pk)
    before = {name: getattr(tenant, _attr(name)) for name in ("name", *LIMIT_FIELDS)}
    tenant.name = form.cleaned_data["name"]
    for name in LIMIT_FIELDS:
        setattr(tenant, _attr(name), form.cleaned_data[name])
    tenant.save()
    changed = {name: getattr(tenant, _attr(name)) for name in before
               if getattr(tenant, _attr(name)) != before[name]}
    record(request, "tenant.updated", tenant, **changed)
    messages.success(request, _("Saved."))
    return redirect("console-tenant", pk)


@staff_required
@require_POST
def tenant_profile(request, pk):
    from apps.org.models import TenantProfile

    tenant = _tenant(pk)
    form = ProfileForm(request.POST, request.FILES)
    if not form.is_valid():
        _invalid(request, form)
        return redirect("console-tenant", pk)
    data = dict(form.cleaned_data)
    logo = data.pop("logo")
    with tenant_context(tenant.id):
        profile = TenantProfile.objects.first()
        for name, value in data.items():
            setattr(profile, name, value)
        replace_file(profile, "logo", logo)
        profile.save()
    if logo is not None:
        data["logo"] = _("removed") if logo is False else logo.name
    record(request, "profile.updated", tenant, **data)
    messages.success(request, _("Saved."))
    return redirect("console-tenant", pk)


@staff_required
@require_POST
def tenant_status(request, pk):
    tenant = _tenant(pk)
    status = request.POST.get("status", "")
    if status not in TRANSITIONS or tenant.status not in TRANSITIONS[status]:
        messages.error(request, _("That change is not allowed."))
        return redirect("console-tenant", pk)
    previous = tenant.status
    tenant.status = status
    tenant.save(update_fields=["status", "updated_at"])
    record(request, "tenant.status", tenant, before=previous, after=status)
    messages.success(request, _("%(name)s is now %(status)s.")
                     % {"name": tenant.name, "status": tenant.get_status_display()})
    return redirect("console-tenant", pk)


@staff_required
@require_POST
def domain_add(request, pk):
    tenant = _tenant(pk)
    form = DomainForm(request.POST)
    if not form.is_valid():
        _invalid(request, form)
        return redirect("console-tenant", pk)
    TenantDomain.objects.create(tenant=tenant, domain=form.cleaned_data["domain"])
    record(request, "domain.added", tenant, domain=form.cleaned_data["domain"])
    messages.success(request, _("Domain added."))
    return redirect("console-tenant", pk)


@staff_required
@require_POST
def domain_remove(request, pk, domain_id):
    tenant = _tenant(pk)
    domain = tenant.domains.filter(pk=domain_id).first()
    if domain is None:
        raise Http404
    if domain.is_primary:
        messages.error(request, _("The main address cannot be removed."))
        return redirect("console-tenant", pk)
    record(request, "domain.removed", tenant, domain=domain.domain)
    domain.delete()
    messages.success(request, _("Domain removed."))
    return redirect("console-tenant", pk)


@staff_required
def events(request):
    queryset = PlatformEvent.objects.select_related("actor", "tenant")
    tenant_id = request.GET.get("tenant", "")
    if tenant_id.isdigit():
        queryset = queryset.filter(tenant_id=int(tenant_id))
    page = Paginator(queryset, 50).get_page(request.GET.get("page"))
    return render(request, "console/events.html", {"page": page, "section": "events"})



# Traffic pages are live: the browser re-fetches these parts every few seconds (…/live/) and
# swaps them in. Each part is rendered by the same template the full page includes.
OVERVIEW_PARTS = {"tiles": "console/_traffic_tiles.html", "chart": "console/_traffic_chart.html",
                  "clients": "console/_traffic_rows.html"}
CLIENT_PARTS = {"tiles": "console/_traffic_tiles.html", "chart": "console/_traffic_chart.html",
                "routes": "console/_traffic_routes.html",
                "summary": "console/_traffic_summary.html", "access": "console/_access_card.html"}


def _live_strings() -> dict[str, str]:
    return {
        "updated": _("Live · updated %(time)s"), "paused": _("Paused"),
        "offline": _("Reconnecting…"), "pause": _("Pause"), "resume": _("Resume"),
        "disable_title": _("Disable %(name)s?"),
        "failed": _("Could not reach the server. Check your connection and try again."),
    }


def _traffic_context(request, tenant: Tenant | None = None) -> dict:
    report = traffic_report.report(request.GET.get("window", ""), tenant)
    context = {
        "report": report, "totals": report.totals, "windows": traffic_report.WINDOWS,
        "default_limit": settings.TENANT_REQUESTS_PER_MINUTE,
        "slow_ms": settings.TRAFFIC_SLOW_MS, "section": "traffic", "live_strings": _live_strings(),
    }
    if tenant is None:
        context["page_url"] = f"{reverse('console-traffic')}?window={report.key}"
    else:
        context.update({
            "tenant": tenant, "client": report.clients[0],
            "routes": traffic_report.routes(tenant, report.start),
            "page_url": f"{reverse('console-tenant-traffic', args=[tenant.pk])}"
                        f"?window={report.key}"})
    return context


def _live(request, parts: dict[str, str], context: dict) -> JsonResponse:
    return JsonResponse({
        "window": context["report"].key,
        "parts": {name: render_to_string(template, context, request=request)
                  for name, template in parts.items()},
    })


@staff_required
def traffic(request):
    return render(request, "console/traffic.html", _traffic_context(request))


@staff_required
def traffic_live(request):
    return _live(request, OVERVIEW_PARTS, _traffic_context(request))


@staff_required
def tenant_traffic(request, pk):
    tenant = _tenant(pk)
    form = TrafficLimitForm(request.POST or None,
                            initial={"requests_per_minute": tenant.requests_per_minute})
    if request.method == "POST":
        if not form.is_valid():
            _invalid(request, form)
            return redirect("console-tenant-traffic", pk)
        before = tenant.requests_per_minute
        tenant.requests_per_minute = form.cleaned_data["requests_per_minute"]
        tenant.save(update_fields=["requests_per_minute", "updated_at"])
        if tenant.requests_per_minute != before:
            record(request, "traffic.limit", tenant,
                   before="" if before is None else before,
                   after="" if tenant.requests_per_minute is None
                   else tenant.requests_per_minute)
        messages.success(request, _("Saved."))
        window = traffic_report.window(request.GET.get("window", ""))
        return redirect(f"{request.path}?window={window}")
    return render(request, "console/tenant_traffic.html",
                  {**_traffic_context(request, tenant), "form": form})


@staff_required
def tenant_traffic_live(request, pk):
    return _live(request, CLIENT_PARTS, _traffic_context(request, _tenant(pk)))


@staff_required
@require_POST
def tenant_access(request, pk):
    """Enable (active) or disable (suspended) a client. Disabling stops every sign-in and
    request at once; the data is kept. JSON for the live pages, a redirect otherwise."""
    tenant = _tenant(pk)
    wants_json = "application/json" in request.headers.get("Accept", "")
    target = TenantStatus.ACTIVE if request.POST.get("enabled") == "1" else TenantStatus.SUSPENDED
    if tenant.status not in ACCESS_STATUSES:
        message = _("That change is not allowed.")
        if wants_json:
            return JsonResponse({"error": {"message": message}}, status=409)
        messages.error(request, message)
    else:
        if tenant.status != target:
            previous = tenant.status
            tenant.status = target
            tenant.save(update_fields=["status", "updated_at"])
            record(request, "tenant.status", tenant, before=previous, after=target)
        message = (_("%(name)s is enabled.") if target == TenantStatus.ACTIVE
                   else _("%(name)s is disabled.")) % {"name": tenant.name}
        if wants_json:
            return JsonResponse({"status": tenant.status, "message": message})
        messages.success(request, message)
    next_url = request.POST.get("next", "")
    if not url_has_allowed_host_and_scheme(next_url, {request.get_host()}):
        next_url = reverse("console-tenant-traffic", args=[pk])
    return redirect(next_url)


@staff_required
def platform_settings(request):
    """The platform's name, logos and the links in every workspace's sidebar footer."""
    from django.core.files.storage import default_storage

    row = PlatformSettings.objects.filter(pk=1).first() or PlatformSettings(pk=1)
    before = {name: getattr(row, name).name for name in ("logo", "logo_dark")}
    form = PlatformSettingsForm(request.POST or None, request.FILES or None, instance=row)
    links = PlatformLinkFormSet(request.POST or None, prefix="links",
                                queryset=PlatformLink.objects.all())
    if request.method == "POST":
        if form.is_valid() and links.is_valid():
            form.save()
            links.save()
            PlatformSettings.objects.get(pk=1).save()  # drops the cached copy everywhere
            for name, old in before.items():
                if old and old != getattr(row, name).name:
                    default_storage.delete(old)
            record(request, "platform.settings", None, **{
                name: form.cleaned_data[name] for name in form.changed_data
                if name not in ("logo", "logo_dark")},
                **({"logos": ", ".join(n for n in form.changed_data if n.startswith("logo"))}
                   if any(n.startswith("logo") for n in form.changed_data) else {}))
            messages.success(request, _("Saved."))
            return redirect("console-settings")
        messages.error(request, _("Please correct the highlighted fields."))
    return render(request, "console/settings.html", {
        "form": form, "links": links, "settings_row": row, "section": "settings"})


# ---- Features (apps.platform.tenants.features) ----

def _back(request, fallback: str, anchor: str = ""):
    next_url = request.POST.get("next", "")
    if not url_has_allowed_host_and_scheme(next_url, {request.get_host()}):
        next_url = fallback
    return redirect(f"{next_url}{anchor}")


@staff_required
def features(request):
    """Every feature, which plans include it by default, and how many clients have it on."""
    defaults = feature_registry.plan_defaults()
    live = list(Tenant.objects.filter(status__in=(TenantStatus.ACTIVE, TenantStatus.SUSPENDED)))
    usage = {f.key: 0 for f in feature_registry.FEATURES}
    for tenant in live:
        for key in feature_registry.enabled_keys(tenant.pk):
            if key in usage:
                usage[key] += 1
    plans = [(p.code, p.label) for p in Plan.objects.all()]
    groups: dict[str, list] = {}
    for feature in feature_registry.FEATURES:
        groups.setdefault(str(feature.group), []).append({
            "feature": feature, "used_by": usage[feature.key],
            "plans": [(value, label, defaults.get(value, {}).get(feature.key, False))
                      for value, label in plans]})
    return render(request, "console/features.html", {
        "groups": groups, "plans": plans, "clients": len(live), "section": "features"})


@staff_required
@require_POST
def plan_feature(request):
    from apps.platform.tenants.models import PlanFeature

    key, plan = request.POST.get("key", ""), request.POST.get("plan", "")
    plan_row = Plan.objects.filter(code=plan).first()
    if key not in feature_registry.BY_KEY or plan_row is None:
        raise Http404
    enabled = request.POST.get("enabled") == "1"
    PlanFeature.objects.update_or_create(plan=plan_row, key=key,
                                         defaults={"enabled": enabled})
    feature_registry.forget()
    record(request, "feature.plan", None, plan=plan, feature=key, enabled=enabled)
    feature = feature_registry.BY_KEY[key]
    messages.success(request, (_("%(feature)s is now included in %(plan)s.") if enabled else
                               _("%(feature)s is no longer included in %(plan)s."))
                     % {"feature": feature.label, "plan": plan_row.label})
    return _back(request, reverse("console-features"), f"#f-{key}")


@staff_required
@require_POST
def tenant_feature(request, pk):
    from apps.platform.tenants.models import TenantFeature

    tenant = _tenant(pk)
    key, state = request.POST.get("key", ""), request.POST.get("state", "")
    if key not in feature_registry.BY_KEY or state not in ("on", "off", "default"):
        raise Http404
    if state == "default":
        TenantFeature.objects.filter(tenant=tenant, key=key).delete()
    else:
        TenantFeature.objects.update_or_create(tenant=tenant, key=key,
                                               defaults={"enabled": state == "on"})
    feature_registry.forget(tenant.pk)
    record(request, "feature.client", tenant, feature=key, state=state)
    feature = feature_registry.BY_KEY[key]
    messages.success(request, {
        "on": _("%(feature)s is on for %(name)s."),
        "off": _("%(feature)s is off for %(name)s."),
        "default": _("%(feature)s follows the plan again for %(name)s."),
    }[state] % {"feature": feature.label, "name": tenant.name})
    return _back(request, reverse("console-tenant", args=[pk]), f"#f-{key}")


# ---- Plans ----

@staff_required
def plans(request):
    from django.db.models import Count, Q

    rows = Plan.objects.annotate(
        clients=Count("tenants", distinct=True),
        live=Count("tenants", filter=Q(tenants__status=TenantStatus.ACTIVE), distinct=True))
    defaults = feature_registry.plan_defaults()
    total = len(feature_registry.FEATURES)
    return render(request, "console/plans.html", {
        "plans": [{"plan": p, "features_on": sum(defaults.get(p.code, {}).values()),
                   "features_total": total} for p in rows],
        "section": "plans"})


def _plan_form(request, plan: Plan | None):
    form = PlanForm(request.POST or None, instance=plan)
    if request.method == "POST" and form.is_valid():
        changed = [name for name in form.changed_data if not name.startswith("feature_")]
        saved = form.save()
        form.save_features(saved)
        feature_registry.forget()
        record(request, "plan.updated" if plan else "plan.created", None, plan=saved.code,
               **{name: form.cleaned_data[name] for name in changed if name != "code"})
        messages.success(request, _("Saved."))
        return redirect("console-plans")
    return render(request, "console/plan_form.html", {
        "form": form, "plan": plan, "section": "plans"})


@staff_required
def plan_new(request):
    return _plan_form(request, None)


@staff_required
def plan_edit(request, pk):
    plan = Plan.objects.filter(pk=pk).first()
    if plan is None:
        raise Http404
    return _plan_form(request, plan)


@staff_required
def plan_delete(request, pk):
    """Delete a plan. Its clients (if any) move to another plan first, in the same step."""
    from django.db import transaction

    plan = Plan.objects.filter(pk=pk).first()
    if plan is None:
        raise Http404
    clients = list(plan.tenants.order_by("name"))
    others = Plan.objects.exclude(pk=plan.pk)
    if not others.exists():
        messages.error(request, _("The last plan cannot be deleted."))
        return redirect("console-plans")
    form = PlanDeleteForm(request.POST or None, plan=plan, initial={
        "move_to": (others.filter(is_default=True).first() or others.first()).code})
    if request.method == "POST" and form.is_valid():
        target = Plan.objects.get(code=form.cleaned_data["move_to"])
        with transaction.atomic():
            for tenant in clients:
                tenant.plan = target
                tenant.save(update_fields=["plan", "updated_at"])
                record(request, "tenant.updated", tenant, plan=target.code)
            if plan.is_default:
                target.is_default, target.is_active = True, True
                target.save()
            code = plan.code
            plan.delete()
        feature_registry.forget()
        record(request, "plan.deleted", None, plan=code, moved_to=target.code,
               clients=len(clients))
        messages.success(request, _("Plan deleted."))
        return redirect("console-plans")
    return render(request, "console/plan_delete.html", {
        "plan": plan, "clients": clients, "form": form, "section": "plans"})
