from django.contrib.auth.forms import AuthenticationForm
from django.utils.translation import gettext_lazy as _


class WorkspaceLoginForm(AuthenticationForm):
    """A workspace's sign-in: username or email. Django's own message names the model field
    ("email"), which reads wrong here and is not translated."""

    error_messages = {
        **AuthenticationForm.error_messages,
        "invalid_login": _(
            "That username or email and password do not match. Check them and try again."),
    }
