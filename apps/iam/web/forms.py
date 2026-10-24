from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.utils.translation import gettext_lazy as _

from apps.iam import throttle


class ThrottledLoginMixin:
    """Says why signing in is refused after too many wrong passwords (apps.iam.throttle; the
    backends refuse too), and lets the account start afresh after the right one."""

    def clean(self):
        username = self.cleaned_data.get("username") or ""
        if throttle.locked_for(self.request, username):
            raise forms.ValidationError(
                _("Too many wrong passwords. Wait a few minutes, then try again."),
                code="locked")
        cleaned = super().clean()
        throttle.succeeded(self.request, username)
        return cleaned


class WorkspaceLoginForm(ThrottledLoginMixin, AuthenticationForm):
    """A workspace's sign-in: username or email. Django's own message names the model field
    ("email"), which reads wrong here and is not translated."""

    error_messages = {
        **AuthenticationForm.error_messages,
        "invalid_login": _(
            "That username or email and password do not match. Check them and try again."),
    }
