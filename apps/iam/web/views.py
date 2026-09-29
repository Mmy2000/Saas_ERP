from collections import defaultdict

from django.db.models import Count
from django.http import Http404
from django.shortcuts import render
from django.urls import reverse

from apps.iam.authz import permission_required
from apps.iam.catalog import WILDCARD, permissions
from apps.iam.models import Membership, Role
from apps.org.models import Branch


@permission_required("admin.users.view")
def users(request):
    members = (Membership.objects.select_related("user", "default_branch")
               .prefetch_related("roles__role", "roles__branches__branch")
               .order_by("username"))
    return render(request, "iam/users.html", {"members": members})


def _user_form(request, membership=None):
    assigned = {}
    if membership is not None:
        for assignment in membership.roles.prefetch_related("branches"):
            assigned[assignment.role_id] = {
                "all": assignment.all_branches,
                "branches": {b.branch_id for b in assignment.branches.all()},
            }
    roles = []
    for role in Role.objects.prefetch_related("grants").order_by("name"):
        codes = {g.permission for g in role.grants.all()}
        roles.append({"role": role, "is_owner": WILDCARD in codes,
                      "assignment": assigned.get(role.pk)})
    endpoint = (reverse("member-detail", args=[membership.pk]) if membership
                else reverse("member-list"))
    return render(request, "iam/user_form.html", {
        "member": membership,
        "roles": roles,
        "branches": Branch.objects.filter(is_active=True).order_by("code"),
        "endpoint": endpoint,
        "method": "PUT" if membership else "POST",
    })


@permission_required("admin.users.manage")
def user_new(request):
    return _user_form(request)


@permission_required("admin.users.manage")
def user_edit(request, pk):
    membership = Membership.objects.select_related("user").filter(pk=pk).first()
    if membership is None:
        raise Http404
    return _user_form(request, membership)


@permission_required("admin.users.view")
def roles(request):
    items = (Role.objects.prefetch_related("grants")
             .annotate(member_count=Count("assignments", distinct=True)).order_by("name"))
    return render(request, "iam/roles.html", {
        "roles": [{"role": r, "is_owner": any(g.permission == WILDCARD for g in r.grants.all()),
                   "count": len(r.grants.all())} for r in items],
    })


def _permission_groups():
    groups = defaultdict(list)
    for definition in permissions().values():
        groups[str(definition.group)].append(definition)
    return sorted(groups.items())


def _role_form(request, role=None):
    granted = set(role.grants.values_list("permission", flat=True)) if role else set()
    endpoint = reverse("role-detail", args=[role.pk]) if role else reverse("role-list")
    return render(request, "iam/role_form.html", {
        "role": role,
        "is_owner": WILDCARD in granted,
        "granted": granted,
        "groups": _permission_groups(),
        "endpoint": endpoint,
        "method": "PUT" if role else "POST",
    })


@permission_required("admin.roles.manage")
def role_new(request):
    return _role_form(request)


@permission_required("admin.roles.manage")
def role_edit(request, pk):
    role = Role.objects.filter(pk=pk).first()
    if role is None:
        raise Http404
    return _role_form(request, role)
