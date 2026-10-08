"""What every mobile-app endpoint shares: Bearer-token sign-in, the member's permissions, and
answers in the shape the app was built for (Gweb's Django REST framework defaults: errors as
{"detail": …} or {"field": [message]}, not this platform's {"error": …} envelope)."""

from __future__ import annotations

from django.db import connection
from django.utils import timezone
from django.utils.translation import gettext as _
from rest_framework import exceptions as drf
from rest_framework import status
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.permissions import BasePermission
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.views import exception_handler as drf_handler

from apps.core.errors import AppError
from apps.core.errors import ValidationError as AppValidationError

from . import tokens
from .models import MobileDevice


class TokenInvalid(drf.AuthenticationFailed):
    def __init__(self):
        super().__init__({"detail": _("Your sign-in has expired or is not valid."),
                          "code": "token_not_valid", "messages": []})


class MobileTokenAuthentication(BaseAuthentication):
    """"Authorization: Bearer <access>": the member of this client, on a device still signed in."""

    keyword = b"bearer"

    def authenticate(self, request):
        parts = get_authorization_header(request).split()
        if not parts or parts[0].lower() != self.keyword:
            return None
        if len(parts) != 2:
            raise TokenInvalid
        try:
            claims = tokens.decode(parts[1].decode("ascii", "ignore"))
        except tokens.TokenError:
            raise TokenInvalid from None
        tenant = getattr(request._request, "tenant", None)
        if tenant is None or claims.get("tid") != tenant.id:
            raise TokenInvalid
        device = (MobileDevice.objects.select_related("membership__user",
                                                      "membership__default_branch")
                  .filter(pk=claims.get("sid")).first())
        if device is None or not device.active:
            raise TokenInvalid
        membership = device.membership
        if not membership.is_active or not membership.user.is_active:
            raise TokenInvalid
        from apps.iam.authz import build_actor

        request._request.membership = membership
        request._request.actor = build_actor(membership)
        request._request.mobile_device = device
        # The audit trail names whoever made the changes (the web gets this from middleware).
        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.user_id', %s, true)",
                           [str(membership.user_id)])
        if (timezone.now() - device.last_seen_at).total_seconds() > 300:
            MobileDevice.objects.filter(pk=device.pk).update(last_seen_at=timezone.now())
        return membership.user, claims

    def authenticate_header(self, request):
        return 'Bearer realm="api"'


class CanUseApp(BasePermission):
    """Signed in, with the mobile app switched on for the client and allowed for the member."""

    message = _("You do not have permission to use the mobile app.")

    def has_permission(self, request, view):
        actor = getattr(request._request, "actor", None)
        if not request.user or not request.user.is_authenticated or actor is None:
            return False
        needed = (getattr(view, "required_permissions", None) or {}).get(request.method,
                                                                          "mobile.signin")
        return actor.can("mobile.signin") and actor.can(needed)


def legacy_errors(exc, context):
    """Errors as Gweb's API gave them."""
    if isinstance(exc, AppValidationError) and exc.fields:
        data = {key: value if isinstance(value, list) else [str(value)]
                for key, value in exc.fields.items()}
        return Response(data, status=status.HTTP_400_BAD_REQUEST)
    if isinstance(exc, AppError):
        return Response({"detail": exc.message, "code": exc.code.lower()}, status=exc.status)
    if isinstance(exc, drf.NotAuthenticated):
        return Response({"detail": _("Sign in first.")}, status=status.HTTP_401_UNAUTHORIZED,
                        headers={"WWW-Authenticate": 'Bearer realm="api"'})
    response = drf_handler(exc, context)
    if response is not None and isinstance(exc, drf.AuthenticationFailed) \
            and isinstance(response.data, dict) and "detail" in response.data \
            and isinstance(response.data["detail"], dict):
        response.data = response.data["detail"]
    return response


class LegacyView(APIView):
    authentication_classes = [MobileTokenAuthentication]
    permission_classes = [CanUseApp]
    renderer_classes = [JSONRenderer]
    # Per method, besides mobile.signin (which every endpoint needs): e.g. inventory.stock.view.
    required_permissions = {"GET": "mobile.signin", "POST": "mobile.signin"}

    def get_exception_handler(self):
        return legacy_errors


def detail(message: str, code: int, **extra) -> Response:
    return Response({"detail": message, **extra}, status=code)
