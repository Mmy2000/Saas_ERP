"""Plans kept in the database and managed from the console."""

from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone

from apps.core.errors import DomainError
from apps.core.tenancy import tenant_context
from apps.iam.models import User
from apps.platform.tenants import features, traffic
from apps.platform.tenants.models import Plan, PlanFeature, PlatformEvent, Tenant
from conftest import PASSWORD

pytestmark = pytest.mark.django_db


@pytest.fixture
def console(db):
    User.objects.create_user(email="ops@gweb.test", password=PASSWORD, display_name="Ops",
                             is_platform_staff=True)
    client = Client(HTTP_HOST="admin.localhost")
    client.cookies["django_language"] = "en"
    assert client.post("/login/", {"username": "ops@gweb.test", "password": PASSWORD}
                       ).status_code == 302
    return client


def _plan_data(**overrides):
    data = {"code": "gold-plus", "name": "Gold Plus", "name_ar": "جولد بلس",
            "description": "For big shops", "price": "1500", "currency": "EGP",
            "billing_period": "monthly", "max_branches": "5", "max_users": "20",
            "requests_per_minute": "900", "trial_days": "", "position": "5",
            "is_active": "on"}
    data.update({f"feature_{f.key}": "on" for f in features.FEATURES
                 if f.key not in ("manufacturing", "wholesale")})
    data.update(overrides)
    return {k: v for k, v in data.items() if v is not None}


def test_the_old_plans_are_in_the_database(console):
    assert list(Plan.objects.values_list("code", flat=True)) == [
        "trial", "standard", "professional", "enterprise"]
    assert Plan.objects.get(is_default=True).code == "standard"
    html = console.get("/plans/").content.decode()
    assert all(name in html for name in ("Trial", "Standard", "Professional", "Enterprise"))


def test_create_and_edit_a_plan(console):
    assert console.get("/plans/new/").status_code == 200
    response = console.post("/plans/new/", _plan_data(is_default="on"))
    assert response.status_code == 302, response.content.decode()[:3000]
    plan = Plan.objects.get(code="gold-plus")
    assert (plan.max_branches, plan.max_users, plan.is_default) == (5, 20, True)
    assert Plan.objects.filter(is_default=True).count() == 1  # standard is no longer default
    assert dict(PlanFeature.objects.filter(plan=plan).values_list("key", "enabled"))[
        "manufacturing"] is False
    assert features.plan_defaults()["gold-plus"]["repairs"] is True
    assert PlatformEvent.objects.filter(action="plan.created").exists()

    # The code is fixed once created; everything else can change.
    console.post(f"/plans/{plan.pk}/", _plan_data(code="other", name="Gold Max",
                                                  is_default="on"))
    plan.refresh_from_db()
    assert (plan.code, plan.name) == ("gold-plus", "Gold Max")

    # The default plan must be offered for new clients.
    hidden = console.post(f"/plans/{plan.pk}/", _plan_data(is_active=None, is_default="on"))
    assert hidden.status_code == 200 and "must be offered" in hidden.content.decode()


def test_clients_follow_their_plans_limits(console, tenant_a, settings):
    from apps.org.services import BranchInput, create_branch

    Plan.objects.filter(code=tenant_a.plan_id).update(max_branches=1, requests_per_minute=50)
    tenant_a.refresh_from_db()
    assert tenant_a.branch_limit == 1 and traffic.limit_for(tenant_a) == 50
    with tenant_context(tenant_a.id), pytest.raises(DomainError):
        create_branch(BranchInput(code=2, name="Second"))
    tenant_a.max_branches, tenant_a.requests_per_minute = 3, 0  # the client's own values win
    tenant_a.save()
    assert tenant_a.branch_limit == 3 and traffic.limit_for(tenant_a) is None
    settings.TENANT_REQUESTS_PER_MINUTE = 600
    Plan.objects.filter(code=tenant_a.plan_id).update(requests_per_minute=None)
    tenant_a.requests_per_minute = None  # nothing set anywhere: the platform default
    tenant_a.save()
    assert traffic.limit_for(Tenant.objects.get(pk=tenant_a.pk)) == 600


def test_trial_days_set_a_new_clients_trial_end(console):
    Plan.objects.create(code="try", name="Try", trial_days=14, position=9)
    console.post("/clients/new/", {
        "name": "Nile Gold", "slug": "nile", "locale": "ar", "country": "EG",
        "functional_currency": "EGP", "timezone": "Africa/Cairo", "fineness_24k": "999.9",
        "owner_display_name": "Sara", "owner_username": "sara",
        "owner_email": "sara@nile.test", "owner_password": PASSWORD, "plan": "try"})
    tenant = Tenant.objects.get(slug="nile")
    assert tenant.plan_id == "try"
    assert tenant.trial_ends_on == timezone.localdate() + timedelta(days=14)


def test_hidden_plans_are_not_offered_for_new_clients(console, tenant_a):
    Plan.objects.filter(code="enterprise").update(is_active=False)
    assert 'value="enterprise"' not in console.get("/clients/new/").content.decode()
    tenant_a.plan_id = "enterprise"
    tenant_a.save()
    page = console.get(f"/clients/{tenant_a.pk}/").content.decode()
    assert 'value="enterprise"' in page  # a client already on it keeps it


def test_deleting_a_plan_moves_its_clients(console, tenant_a, tenant_b):
    plan = Plan.objects.get(code=tenant_a.plan_id)
    page = console.get(f"/plans/{plan.pk}/delete/")
    assert page.status_code == 200 and tenant_a.name in page.content.decode()
    response = console.post(f"/plans/{plan.pk}/delete/", {"move_to": "professional"})
    assert response.status_code == 302
    assert not Plan.objects.filter(pk=plan.pk).exists()
    tenant_a.refresh_from_db()
    tenant_b.refresh_from_db()
    assert tenant_a.plan_id == tenant_b.plan_id == "professional"
    assert Plan.objects.get(is_default=True).code == "professional"  # it was the default
    assert PlatformEvent.objects.filter(action="plan.deleted").exists()
    assert "professional" in console.get("/clients/?plan=professional").content.decode() \
        or tenant_a.name in console.get("/clients/?plan=professional").content.decode()


def test_the_last_plan_cannot_be_deleted(console):
    Plan.objects.exclude(code="trial").delete()
    plan = Plan.objects.get()
    response = console.post(f"/plans/{plan.pk}/delete/", {"move_to": "trial"})
    assert response.status_code == 302 and Plan.objects.filter(pk=plan.pk).exists()


def test_plans_pages_are_staff_only(tenant_a):
    assert "/login/" in Client(HTTP_HOST="admin.localhost").get("/plans/")["Location"]


def test_a_database_without_plans_still_creates_clients(make_tenant):
    PlanFeature.objects.all().delete()
    Plan.objects.all().delete()
    tenant = make_tenant("fresh")
    assert tenant.plan_id == "standard"
    assert Plan.objects.count() == 4
