"""The platform console: served on PLATFORM_HOSTS only, for platform staff (§5.4, §5.7)."""

from __future__ import annotations

from functools import wraps

from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.core.errors import ValidationError
from apps.core.tenancy import tenant_context
from apps.platform.tenants.models import (
    PlatformEvent,
    Tenant,
    TenantDomain,
    TenantPlan,
    TenantStatus,
)
from apps.platform.tenants.services import ProvisionTenantCommand, provision_tenant

from . import metrics
from .forms import (
    DomainForm,
    ProfileForm,
    StaffLoginForm,
    TenantCreateForm,
    TenantSettingsForm,
    domain_for,
)

LIMIT_FIELDS = ("plan", "trial_ends_on", "max_branches", "max_users", "contact_name",
                "contact_email", "contact_phone", "notes")
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
    queryset = Tenant.objects.prefetch_related("domains").order_by("name")
    if term:
        queryset = queryset.filter(Q(name__icontains=term) | Q(slug__icontains=term)
                                   | Q(contact_name__icontains=term)
                                   | Q(contact_email__icontains=term)
                                   | Q(domains__domain__icontains=term)).distinct()
    if status in TenantStatus.values:
        queryset = queryset.filter(status=status)
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
    form = TenantCreateForm(request.POST or None, initial={"plan": TenantPlan.TRIAL})
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
                setattr(tenant, name, d[name] if d[name] is not None else
                        Tenant._meta.get_field(name).get_default())
            tenant.save()
            record(request, "tenant.created", tenant, slug=tenant.slug, plan=tenant.plan)
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
        "name": tenant.name, **{name: getattr(tenant, name) for name in LIMIT_FIELDS}})
    profile_form = ProfileForm(initial={
        "display_name": profile.display_name, "locale": profile.locale,
        "country": profile.country, "timezone": profile.timezone} if profile else None)
    return render(request, "console/tenant.html", {
        "tenant": tenant, "usage": usage, "profile": profile,
        "domains": tenant.domains.order_by("-is_primary", "domain"),
        "primary": _primary_domain(tenant),
        "owners": _owners(tenant) if profile else [],
        "events": tenant.events.select_related("actor")[:12],
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
    form = TenantSettingsForm(request.POST)
    if not form.is_valid():
        _invalid(request, form)
        return redirect("console-tenant", pk)
    before = {name: getattr(tenant, name) for name in ("name", *LIMIT_FIELDS)}
    tenant.name = form.cleaned_data["name"]
    for name in LIMIT_FIELDS:
        setattr(tenant, name, form.cleaned_data[name])
    tenant.save()
    changed = {name: getattr(tenant, name) for name in before
               if getattr(tenant, name) != before[name]}
    record(request, "tenant.updated", tenant, **changed)
    messages.success(request, _("Saved."))
    return redirect("console-tenant", pk)


@staff_required
@require_POST
def tenant_profile(request, pk):
    from apps.org.models import TenantProfile

    tenant = _tenant(pk)
    form = ProfileForm(request.POST)
    if not form.is_valid():
        _invalid(request, form)
        return redirect("console-tenant", pk)
    with tenant_context(tenant.id):
        profile = TenantProfile.objects.first()
        for name, value in form.cleaned_data.items():
            setattr(profile, name, value)
        profile.save()
    record(request, "profile.updated", tenant, **form.cleaned_data)
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

