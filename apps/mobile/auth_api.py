"""/api/auth/*: signing the mobile app in and out (Gweb's Main/api, unchanged for the app)."""

from __future__ import annotations

from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from django.utils.translation import gettext as _
from rest_framework import status
from rest_framework.response import Response

from apps.iam.authz import build_actor

from . import shapes, tokens
from .legacy import LegacyView, detail


def _client(request) -> tuple[str, str | None]:
    from apps.iam.signals import client_ip

    return request.META.get("HTTP_USER_AGENT", "")[:300], client_ip(request)


class LoginView(LegacyView):
    authentication_classes = []
    permission_classes = []  # the sign-in itself: anyone may try (checked inside)
    required_permissions = {"POST": "authenticated"}

    def post(self, request):
        username = str(request.data.get("username") or "").strip()
        password = str(request.data.get("password") or "")
        missing = {name: [_("This field is required.")]
                   for name, value in (("username", username), ("password", password))
                   if not value}
        if missing:
            return Response(missing, status=status.HTTP_400_BAD_REQUEST)
        from apps.iam import throttle

        wait = throttle.locked_for(request._request, username)
        if wait:  # too many wrong passwords (the backend would refuse too, without saying why)
            response = detail(_("Too many wrong passwords. Wait a few minutes, then try again."),
                              status.HTTP_429_TOO_MANY_REQUESTS)
            response["Retry-After"] = str(wait)
            return response
        account = authenticate(request._request, username=username, password=password)
        if account is None:  # recorded as a failed sign-in by apps.iam.signals
            return detail(_("Wrong name or password."), status.HTTP_401_UNAUTHORIZED)
        from apps.iam.models import Membership, MembershipStatus, SignInOutcome
        from apps.iam.signals import _record

        membership = (Membership.objects.select_related("user", "default_branch")
                      .filter(user=account, status=MembershipStatus.ACTIVE).first())
        actor = build_actor(membership)
        if not actor.can("mobile.signin"):
            return detail(_("The mobile app is not available for this account."),
                          status.HTTP_403_FORBIDDEN)
        _record(request._request, SignInOutcome.SIGNED_IN, membership=membership)
        throttle.succeeded(request._request, username)
        account.last_login = timezone.now()
        account.save(update_fields=["last_login"])
        agent, ip = _client(request._request)
        pair = tokens.sign_in(membership, user_agent=agent, ip=ip)
        return Response({"access": pair.access, "refresh": pair.refresh,
                         "user": shapes.user(membership, actor)})


class RefreshView(LegacyView):
    authentication_classes = []
    permission_classes = []  # the refresh token is the proof
    required_permissions = {"POST": "authenticated"}

    def post(self, request):
        refresh = str(request.data.get("refresh") or "")
        if not refresh:
            return Response({"refresh": [_("This field is required.")]},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            pair = tokens.rotate(refresh)
        except tokens.TokenError:
            return Response({"detail": _("Your sign-in has expired or is not valid."),
                             "code": "token_not_valid"}, status=status.HTTP_401_UNAUTHORIZED)
        return Response({"access": pair.access, "refresh": pair.refresh})


class LogoutView(LegacyView):
    def post(self, request):
        refresh = str(request.data.get("refresh") or "")
        if not refresh:
            return Response({"refresh": [_("This field is required.")]},
                            status=status.HTTP_400_BAD_REQUEST)
        if not tokens.sign_out(refresh, membership=request.membership):
            return detail(_("This sign-in belongs to someone else."), status.HTTP_403_FORBIDDEN)
        # 205 carries no body (HTTP says so; Android's client refuses one). Gweb sent one, which
        # the app ignored.
        return Response(status=status.HTTP_205_RESET_CONTENT)


class MeView(LegacyView):
    def get(self, request):
        return Response(shapes.user(request.membership, request.actor))


class ChangePasswordView(LegacyView):
    """Changes the person's password (for every workspace they belong to), signs out all their
    phones here and gives this one fresh tokens."""

    def post(self, request):
        old = str(request.data.get("old_password") or "")
        new = str(request.data.get("new_password") or "")
        account = request.membership.user
        if not old or not account.check_password(old):
            return Response({"old_password": [_("The current password is not right.")]},
                            status=status.HTTP_400_BAD_REQUEST)
        if not new:
            return Response({"new_password": [_("This field is required.")]},
                            status=status.HTTP_400_BAD_REQUEST)
        if new == old:
            return Response({"new_password": [_("The new password is the same as the current "
                                                "one.")]}, status=status.HTTP_400_BAD_REQUEST)
        try:
            validate_password(new, account)
        except DjangoValidationError as exc:
            return Response({"new_password": list(exc.messages)},
                            status=status.HTTP_400_BAD_REQUEST)
        account.set_password(new)
        account.save(update_fields=["password"])
        tokens.revoke_all(request.membership)
        agent, ip = _client(request._request)
        pair = tokens.sign_in(request.membership, user_agent=agent, ip=ip)
        return Response({"detail": _("Password changed."), "access": pair.access,
                         "refresh": pair.refresh})
