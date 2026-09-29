"""Members, roles and assignments (§14.2).

Guard rails enforced here (not in the UI):
* no privilege escalation: an actor can only grant permissions they hold themselves, and only
  wildcard (Owner) actors can hand out the Owner role;
* the workspace always keeps at least one active Owner;
* an email already used by another account is refused (invitations come later).
"""

from __future__ import annotations

from dataclasses import dataclass

from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils.text import slugify
from django.utils.translation import gettext as _

from apps.core.errors import NotFound, PermissionDenied, ValidationError

from .catalog import WILDCARD, is_known_permission, permissions
from .models import (
    SYSTEM_ROLE_NAMES,
    Membership,
    MembershipRole,
    MembershipRoleBranch,
    MembershipStatus,
    Role,
    RolePermission,
    User,
)

OWNER = "owner"
MANAGER = "manager"
VIEWER = "viewer"


def _system_role_templates() -> dict[str, tuple[str, str, list[str]]]:
    catalog = permissions()
    viewing = [c for c in catalog if c.endswith(".view")]
    managing = [c for c in catalog if not c.startswith("admin.")] + ["admin.users.view"]
    names = {code: (str(n), str(d)) for code, (n, d) in SYSTEM_ROLE_NAMES.items()}
    return {
        OWNER: (*names[OWNER], [WILDCARD]),
        MANAGER: (*names[MANAGER], managing),
        VIEWER: (*names[VIEWER], viewing),
    }


def seed_system_roles() -> dict[str, Role]:
    """Create the Owner/Manager/Viewer roles for a new tenant. Idempotent."""
    roles = {}
    for code, (name, description, grants) in _system_role_templates().items():
        role, created = Role.objects.get_or_create(
            code=code, defaults={"name": name, "description": description, "is_system": True}
        )
        if created:
            RolePermission.objects.bulk_create(
                [RolePermission(role=role, permission=p) for p in grants]
            )
        roles[code] = role
    return roles


# --- guard rails ------------------------------------------------------------------------------

def _is_superior(actor) -> bool:
    return actor is None or WILDCARD in actor.grants


def _ensure_can_grant(actor, codes) -> None:
    if _is_superior(actor):
        return
    if WILDCARD in codes or any(not actor.can(code) for code in codes):
        raise PermissionDenied(_("You can only grant permissions you have yourself."),
                               code="IAM_ESCALATION")


def _role_codes(role: Role) -> set[str]:
    return set(role.grants.values_list("permission", flat=True))


def _ensure_an_owner_remains() -> None:
    active_owners = (MembershipRole.objects
                     .filter(role__code=OWNER, membership__status=MembershipStatus.ACTIVE)
                     .values("membership").distinct().count())
    if active_owners == 0:
        raise ValidationError(_("The workspace must keep at least one active owner."),
                              code="IAM_LAST_OWNER")


# --- roles ------------------------------------------------------------------------------------

def _clean_permissions(codes) -> list[str]:
    codes = sorted(set(codes))
    unknown = [c for c in codes if not is_known_permission(c) or c == WILDCARD]
    if unknown:
        raise ValidationError(_("Unknown permissions."), fields={"permissions": unknown})
    return codes


def set_role_permissions(role: Role, codes: list[str], *, actor=None) -> None:
    if role.code == OWNER:
        raise ValidationError(_("The Owner role cannot be changed."), code="IAM_OWNER_LOCKED")
    codes = _clean_permissions(codes)
    _ensure_can_grant(actor, codes)
    with transaction.atomic():
        role.grants.all().delete()
        RolePermission.objects.bulk_create([RolePermission(role=role, permission=c) for c in codes])


def _unique_role_code(name: str) -> str:
    base = slugify(name)[:40] or "role"
    code, n = base, 1
    while Role.objects.filter(code=code).exists():
        n += 1
        code = f"{base}-{n}"
    return code


def create_role(name: str, description: str, codes: list[str], *, actor=None) -> Role:
    if actor is not None:
        actor.require("admin.roles.manage")
    name = name.strip()
    if not name:
        raise ValidationError(_("Please correct the highlighted fields."),
                              fields={"name": [_("This field is required.")]})
    with transaction.atomic():
        role = Role.objects.create(code=_unique_role_code(name), name=name,
                                   description=description.strip())
        set_role_permissions(role, codes, actor=actor)
    return role


def update_role(role_id: int, name: str, description: str, codes: list[str], *,
                actor=None) -> Role:
    if actor is not None:
        actor.require("admin.roles.manage")
    role = Role.objects.filter(pk=role_id).first()
    if role is None:
        raise NotFound(_("Not found."))
    if role.code == OWNER:
        raise ValidationError(_("The Owner role cannot be changed."), code="IAM_OWNER_LOCKED")
    with transaction.atomic():
        role.name = name.strip() or role.name
        role.description = description.strip()
        role.save(update_fields=["name", "description", "updated_at"])
        set_role_permissions(role, codes, actor=actor)
    return role


def delete_role(role_id: int, *, actor=None) -> None:
    if actor is not None:
        actor.require("admin.roles.manage")
    role = Role.objects.filter(pk=role_id).first()
    if role is None:
        raise NotFound(_("Not found."))
    if role.is_system:
        raise ValidationError(_("Built-in roles cannot be deleted."), code="IAM_SYSTEM_ROLE")
    if role.assignments.exists():
        raise ValidationError(_("Remove this role from its members first."),
                              code="IAM_ROLE_IN_USE")
    role.delete()


# --- members ----------------------------------------------------------------------------------

@dataclass(frozen=True)
class RoleAssignmentInput:
    role_id: int
    branch_ids: tuple[int, ...] | None = None  # None = every branch


@dataclass(frozen=True)
class MemberInput:
    username: str
    display_name: str
    email: str = ""
    phone: str = ""
    default_branch_id: int | None = None
    roles: tuple[RoleAssignmentInput, ...] = ()


def _validate_member(data: MemberInput, user: User | None) -> dict:
    from apps.org.models import Branch

    errors: dict[str, list[str]] = {}
    username = data.username.strip()
    try:
        UnicodeUsernameValidator()(username)
    except DjangoValidationError:
        errors["username"] = [_("Letters, digits and @/./+/-/_ only.")]
    if not username:
        errors["username"] = [_("This field is required.")]
    elif Membership.objects.filter(username=username).exclude(user=user).exists():
        errors["username"] = [_("This username is already taken in this workspace.")]
    if not data.display_name.strip():
        errors["display_name"] = [_("This field is required.")]

    email = data.email.strip().lower()
    if email:
        try:
            validate_email(email)
        except DjangoValidationError:
            errors["email"] = [_("Enter a valid email address.")]
        else:
            others = User.objects.filter(email__iexact=email).exclude(pk=getattr(user, "pk", None))
            if others.exists():
                errors["email"] = [_("This email already belongs to another account.")]

    default_branch = None
    if data.default_branch_id is not None:
        default_branch = Branch.objects.filter(pk=data.default_branch_id).first()
        if default_branch is None:
            errors["default_branch"] = [_("Unknown branch.")]

    roles = {r.pk: r for r in Role.objects.filter(pk__in=[a.role_id for a in data.roles])}
    if len(roles) != len({a.role_id for a in data.roles}):
        errors["roles"] = [_("Unknown role.")]
    if not data.roles:
        errors["roles"] = [_("Give the member at least one role.")]

    branch_ids = {b for a in data.roles for b in (a.branch_ids or ())}
    branches = {b.pk: b for b in Branch.objects.filter(pk__in=branch_ids)}
    if len(branches) != len(branch_ids):
        errors["roles"] = [_("Unknown branch.")]

    if errors:
        raise ValidationError(_("Please correct the highlighted fields."), fields=errors)
    return {"username": username, "email": email or None, "default_branch": default_branch,
            "roles": roles, "branches": branches}


def _apply_roles(membership: Membership, data: MemberInput, clean: dict, actor) -> None:
    wanted = {a.role_id: a for a in data.roles}
    for role in clean["roles"].values():
        _ensure_can_grant(actor, _role_codes(role))
    removed = MembershipRole.objects.filter(membership=membership).exclude(role_id__in=wanted)
    for assignment in removed.select_related("role"):
        # Taking a role away is also a grant decision: you can't strip powers you don't hold.
        _ensure_can_grant(actor, _role_codes(assignment.role))
    removed.delete()
    for role_id, assignment in wanted.items():
        branches = (None if assignment.branch_ids is None
                    else [clean["branches"][b] for b in assignment.branch_ids])
        assign_role(membership, clean["roles"][role_id], branches=branches)


def create_member(data: MemberInput, password: str, *, actor=None) -> Membership:
    if actor is not None:
        actor.require("admin.users.manage")
    clean = _validate_member(data, None)
    try:
        validate_password(password)
    except DjangoValidationError as exc:
        raise ValidationError(_("Please correct the highlighted fields."),
                              fields={"password": list(exc.messages)}) from exc
    with transaction.atomic():
        user = User.objects.create_user(email=clean["email"], password=password,
                                        display_name=data.display_name.strip(),
                                        phone=data.phone.strip() or None)
        membership = Membership.objects.create(
            user=user, username=clean["username"], default_branch=clean["default_branch"],
            created_by=getattr(actor, "user", None),
        )
        _apply_roles(membership, data, clean, actor)
    return membership


def update_member(membership_id: int, data: MemberInput, *, actor=None) -> Membership:
    if actor is not None:
        actor.require("admin.users.manage")
    with transaction.atomic():
        membership = (Membership.objects.select_for_update().select_related("user")
                      .filter(pk=membership_id).first())
        if membership is None:
            raise NotFound(_("Not found."))
        clean = _validate_member(data, membership.user)
        user = membership.user
        user.display_name = data.display_name.strip()
        user.email = clean["email"]
        user.phone = data.phone.strip() or None
        user.save(update_fields=["display_name", "email", "phone"])
        membership.username = clean["username"]
        membership.default_branch = clean["default_branch"]
        membership.updated_by = getattr(actor, "user", None)
        membership.save()
        _apply_roles(membership, data, clean, actor)
        _ensure_an_owner_remains()
    return membership


def set_member_status(membership_id: int, status: str, *, actor=None) -> Membership:
    if actor is not None:
        actor.require("admin.users.manage")
    if status not in MembershipStatus.values:
        raise ValidationError(_("Unknown status."))
    with transaction.atomic():
        membership = Membership.objects.select_for_update().filter(pk=membership_id).first()
        if membership is None:
            raise NotFound(_("Not found."))
        if actor is not None and membership.pk == actor.membership.pk:
            raise ValidationError(_("You cannot suspend yourself."), code="IAM_SELF_SUSPEND")
        for assignment in membership.roles.select_related("role"):
            _ensure_can_grant(actor, _role_codes(assignment.role))
        membership.status = status
        membership.save(update_fields=["status", "updated_at"])
        _ensure_an_owner_remains()
    return membership


def set_member_password(membership_id: int, password: str, *, actor=None) -> None:
    """Set a new password (existing sessions of that user end: Django rotates the auth hash)."""
    if actor is not None:
        actor.require("admin.users.manage")
    membership = Membership.objects.select_related("user").filter(pk=membership_id).first()
    if membership is None:
        raise NotFound(_("Not found."))
    for assignment in membership.roles.select_related("role"):
        _ensure_can_grant(actor, _role_codes(assignment.role))
    try:
        validate_password(password, user=membership.user)
    except DjangoValidationError as exc:
        raise ValidationError(_("Please correct the highlighted fields."),
                              fields={"password": list(exc.messages)}) from exc
    membership.user.set_password(password)
    membership.user.save(update_fields=["password"])


def assign_role(membership, role: Role, *, branches=None) -> MembershipRole:
    """Give `membership` the role in all branches (branches=None) or only in `branches`."""
    with transaction.atomic():
        assignment, _created = MembershipRole.objects.update_or_create(
            membership=membership, role=role, defaults={"all_branches": branches is None}
        )
        assignment.branches.all().delete()
        if branches is not None:
            MembershipRoleBranch.objects.bulk_create(
                [MembershipRoleBranch(assignment=assignment, branch=b) for b in branches]
            )
        return assignment
