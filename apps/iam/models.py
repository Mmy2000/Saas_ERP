"""Identity (§7.2, ADR-004): a global User with no username, and a per-tenant Membership that
carries the tenant-local username (so legacy usernames such as "Admin" keep working in every
tenant without colliding)."""

from __future__ import annotations

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TenantScopedModel


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email=None, password=None, **extra):
        email = self.normalize_email(email) or None
        user = self.model(email=email, **extra)
        user.set_password(password)  # None → unusable password
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password, **extra):
        """Platform staff for the platform admin host. Never a tenant role (§14.4)."""
        extra.setdefault("is_platform_staff", True)
        extra.setdefault("is_superuser", True)
        return self.create_user(email=email, password=password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    """Global identity. Not tenant-scoped: one person can be a member of several tenants.

    TODO(phase-2 spike): the special RLS policy of §4.3 (visible when self or when a member of
    the current tenant) needs the platform DB role wired in first; until then isolation of
    users relies on reaching them only through Membership.
    """

    email = models.EmailField(unique=True, null=True, blank=True)
    phone = models.CharField(max_length=20, unique=True, null=True, blank=True)
    display_name = models.CharField(max_length=150)
    is_active = models.BooleanField(default=True)
    is_platform_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(default=timezone.now)

    objects = UserManager()

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS = ["display_name"]

    @property
    def is_staff(self) -> bool:  # what django.contrib.admin checks
        return self.is_platform_staff

    def __str__(self):
        return self.display_name or self.email or f"user #{self.pk}"


class MembershipStatus(models.TextChoices):
    ACTIVE = "active", _("Active")
    SUSPENDED = "suspended", _("Suspended")


class Membership(TenantScopedModel):
    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="memberships")
    username = models.CharField(max_length=150)
    status = models.CharField(
        max_length=16, choices=MembershipStatus.choices, default=MembershipStatus.ACTIVE
    )
    default_branch = models.ForeignKey(
        "org.Branch", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    legacy_usercode = models.IntegerField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "username"],
                                    name="iam_membership_username_uniq"),
            models.UniqueConstraint(fields=["tenant", "user"], name="iam_membership_user_uniq"),
        ]

    def __str__(self):
        return self.username

    @property
    def is_active(self) -> bool:
        return self.status == MembershipStatus.ACTIVE


#: Names of the roles seeded at provisioning, translated at display time.
SYSTEM_ROLE_NAMES = {
    "owner": (_("Owner"), _("Full access to everything, including users and roles.")),
    "manager": (_("Manager"), _("Day-to-day operations in every module.")),
    "viewer": (_("Viewer"), _("Read-only access.")),
}


class Role(TenantScopedModel):
    """A tenant-defined bundle of permissions (§14.2). System roles are seeded at provisioning;
    the Owner role holds the wildcard and cannot be edited."""

    code = models.SlugField(max_length=50)
    name = models.CharField(max_length=100)
    description = models.CharField(max_length=300, blank=True)
    is_system = models.BooleanField(default=False)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "code"], name="iam_role_code_uniq"),
        ]

    def __str__(self):
        return self.label

    @property
    def label(self) -> str:
        """Built-in roles show their name in the viewer's language; others their own name."""
        if self.is_system and self.code in SYSTEM_ROLE_NAMES:
            return str(SYSTEM_ROLE_NAMES[self.code][0])
        return self.name

    @property
    def label_description(self) -> str:
        if self.is_system and self.code in SYSTEM_ROLE_NAMES:
            return str(SYSTEM_ROLE_NAMES[self.code][1])
        return self.description


class RolePermission(TenantScopedModel):
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="grants")
    permission = models.CharField(max_length=100)  # a catalog code, or "*"

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "role", "permission"],
                                    name="iam_rolepermission_uniq"),
        ]

    def __str__(self):
        return self.permission


class MembershipRole(TenantScopedModel):
    """A role held by a member, in all branches or in the listed ones."""

    membership = models.ForeignKey(Membership, on_delete=models.CASCADE, related_name="roles")
    role = models.ForeignKey(Role, on_delete=models.PROTECT, related_name="assignments")
    all_branches = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "membership", "role"],
                                    name="iam_membershiprole_uniq"),
        ]

    def __str__(self):
        return f"{self.membership_id}:{self.role_id}"


class MembershipRoleBranch(TenantScopedModel):
    assignment = models.ForeignKey(MembershipRole, on_delete=models.CASCADE,
                                   related_name="branches")
    branch = models.ForeignKey("org.Branch", on_delete=models.CASCADE, related_name="+")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "assignment", "branch"],
                                    name="iam_membershiprolebranch_uniq"),
        ]


class MembershipLimit(TenantScopedModel):
    """Numeric limits per member (legacy `pr_gold/pr_dim/pr_stone`), keyed by the catalog."""

    membership = models.ForeignKey(Membership, on_delete=models.CASCADE, related_name="limits")
    key = models.CharField(max_length=100)
    value = models.DecimalField(max_digits=18, decimal_places=6)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "membership", "key"],
                                    name="iam_membershiplimit_uniq"),
        ]
