from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver

from .middleware import SESSION_TENANT_KEY


@receiver(user_logged_in)
def bind_session_to_tenant(sender, request, user, **kwargs):
    tenant = getattr(request, "tenant", None)
    if tenant is not None:
        request.session[SESSION_TENANT_KEY] = tenant.id
