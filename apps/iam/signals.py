from django.conf import settings
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from apps.core.tenancy.context import get_current_tenant_id

from .middleware import SESSION_TENANT_KEY


@receiver(user_logged_in)
def bind_session_to_tenant(sender, request, user, **kwargs):
    tenant = getattr(request, "tenant", None)
    if tenant is not None:
        request.session[SESSION_TENANT_KEY] = tenant.id


def client_ip(request) -> str | None:
    """The visitor's address. Behind our reverse proxy (SIGNIN_IP_HEADER set) it is the last
    address the proxy appended to that header: earlier ones come from the visitor and can be
    anything."""
    header = getattr(settings, "SIGNIN_IP_HEADER", "")
    value = request.META.get(header, "") if header else ""
    address = (value.split(",")[-1].strip() if value else request.META.get("REMOTE_ADDR", ""))
    return address[:45] or None


def _record(request, outcome, *, membership=None, username=""):
    """Only on a workspace (tenant hosts): platform-staff sign-ins are not a client's."""
    if not _on_workspace(request):
        return
    from django.core.exceptions import ValidationError
    from django.core.validators import validate_ipv46_address

    from .models import SignInEvent

    ip = client_ip(request)
    try:
        validate_ipv46_address(ip or "")
    except ValidationError:
        ip = None
    SignInEvent.objects.create(
        membership=membership, username=(username or getattr(membership, "username", ""))[:150],
        outcome=outcome, ip=ip, user_agent=request.META.get("HTTP_USER_AGENT", "")[:300])


def _on_workspace(request) -> bool:
    """A tenant host, inside its tenant context (not the platform console)."""
    tenant = getattr(request, "tenant", None) if request is not None else None
    return tenant is not None and get_current_tenant_id() == tenant.id


def _membership(user):
    from .models import Membership

    return Membership.objects.filter(user=user).first() if user is not None else None


@receiver(user_logged_in)
def record_sign_in(sender, request, user, **kwargs):
    from .models import SignInOutcome

    if _on_workspace(request):
        _record(request, SignInOutcome.SIGNED_IN, membership=_membership(user))


@receiver(user_login_failed)
def record_failed_sign_in(sender, credentials, request=None, **kwargs):
    from .models import Membership, SignInOutcome

    typed = str(credentials.get("username", ""))[:150]  # never the password
    from . import throttle

    throttle.failed(request, typed)  # every host: workspaces, console, Django admin, the app
    if not _on_workspace(request):
        return
    members = Membership.objects.all()
    membership = members.filter(username=typed).first() if typed else None
    if membership is None and "@" in typed:
        membership = members.filter(user__email__iexact=typed).first()
    _record(request, SignInOutcome.FAILED, membership=membership, username=typed)


@receiver(user_logged_out)
def record_sign_out(sender, request, user, **kwargs):
    from .models import SignInOutcome

    if not _on_workspace(request):
        return
    membership = _membership(user)
    if membership is not None:  # not a session thrown out for belonging to another workspace
        _record(request, SignInOutcome.SIGNED_OUT, membership=membership)
