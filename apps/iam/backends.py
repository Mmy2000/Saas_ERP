from django.contrib.auth.backends import BaseBackend, ModelBackend

from .models import Membership, MembershipStatus, User


class TenantMembershipBackend(BaseBackend):
    """Tenant hosts: log in with the tenant-local username, or with the email of a user who
    holds an active membership in the host's tenant (§14.1)."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        tenant = getattr(request, "tenant", None)
        if tenant is None or not username or password is None:
            return None

        members = Membership.objects.select_related("user").filter(status=MembershipStatus.ACTIVE)
        membership = members.filter(username=username).first()
        if membership is None and "@" in username:
            membership = members.filter(user__email__iexact=username).first()
        if membership is None:
            User().set_password(password)  # same work as a real check, to blunt timing probes
            return None

        user = membership.user
        if user.is_active and user.check_password(password):
            return user
        return None

    def get_user(self, user_id):
        return User.objects.filter(pk=user_id, is_active=True).first()


class PlatformEmailBackend(ModelBackend):
    """Platform hosts only (no tenant): email + password for platform staff."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        if request is None or getattr(request, "tenant", None) is not None:
            return None
        return super().authenticate(request, username=username, password=password, **kwargs)
