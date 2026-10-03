from rest_framework.permissions import BasePermission


def required_permission(view, request) -> str | None:
    """What `view` declares for this request.

    ViewSets declare `required_permissions = {"list": "...", "create": "...", ...}` by action;
    plain APIViews declare it by HTTP method (`{"GET": "..."}`). Use
    `apps.iam.catalog.AUTHENTICATED` for endpoints any active member may call.
    """
    declared = getattr(view, "required_permissions", None) or {}
    key = getattr(view, "action", None) or request.method
    return declared.get(key)


class HasTenantPermission(BasePermission):
    """Default for every API view (§11.6, §14.3): an authenticated member of the host's tenant
    holding the permission the view declares for this action. Undeclared → denied."""

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        actor = getattr(request, "actor", None)
        if getattr(request, "tenant", None) is None or actor is None:
            return False
        code = required_permission(view, request)
        if code is not None and actor.feature_off(code):
            from django.utils.translation import gettext

            self.message = gettext("This feature is not available in your plan.")
            return False
        return code is not None and actor.can(code)
