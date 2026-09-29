from django.contrib.auth import logout
from django.core.exceptions import PermissionDenied
from django.utils.functional import SimpleLazyObject

from .authz import build_actor
from .models import Membership, MembershipStatus

SESSION_TENANT_KEY = "_tenant_id"


class TenantMembershipMiddleware:
    """Sets request.membership and request.actor. An authenticated user must hold an active
    membership in the host's tenant, and the session must have been created on this tenant
    (§5.4). The actor's permission map is built lazily, on first use."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.membership = None
        request.actor = None
        tenant = getattr(request, "tenant", None)

        if tenant is not None and request.user.is_authenticated:
            if request.session.get(SESSION_TENANT_KEY) != tenant.id:
                logout(request)  # a session minted for another tenant: treat as anonymous
            else:
                membership = (
                    Membership.objects.select_related("default_branch", "user")
                    .filter(user=request.user, status=MembershipStatus.ACTIVE)
                    .first()
                )
                if membership is None:
                    logout(request)
                    raise PermissionDenied("No active membership in this workspace.")
                request.membership = membership
                request.actor = SimpleLazyObject(lambda: build_actor(membership))

        return self.get_response(request)
