"""Tell the database who is making the request's changes, for the audit trigger."""

from django.db import connection


class AuditUserMiddleware:
    """After authentication: SET LOCAL app.user_id inside the request's tenant transaction."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if getattr(request, "tenant", None) is not None and user is not None \
                and user.is_authenticated:
            with connection.cursor() as cursor:
                cursor.execute("SELECT set_config('app.user_id', %s, true)", [str(user.pk)])
        return self.get_response(request)
