"""DRF exception handler producing the error envelope (§11.5):

    {"error": {"code": ..., "message": ..., "fields": {...}, "request_id": ...}}
"""

from __future__ import annotations

from rest_framework import exceptions as drf_exceptions
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from apps.core.errors import AppError

_DRF_CODES = {
    400: "VALIDATION_ERROR",
    401: "NOT_AUTHENTICATED",
    403: "PERMISSION_DENIED",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    429: "THROTTLED",
}


def _plain(value):
    """ErrorDetail trees → plain JSON, keeping nesting for nested serializers
    ({"reference": {"sell_price_per_g": ["…"]}}, {"overrides": [{}, {"karat": ["…"]}]})."""
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        if all(not isinstance(v, (dict, list)) for v in value):
            return [str(v) for v in value]
        return [_plain(v) for v in value]
    return [str(value)]


def _envelope(code, message, fields, context, status, headers=None):
    request = context.get("request")
    body = {
        "error": {
            "code": code,
            "message": message,
            "fields": fields,
            "request_id": getattr(request, "request_id", None),
        }
    }
    return Response(body, status=status, headers=headers)


def exception_handler(exc, context):
    if isinstance(exc, AppError):
        return _envelope(exc.code, exc.message, exc.fields, context, exc.status)

    response = drf_exception_handler(exc, context)
    if response is None:
        return None  # unhandled → 500 via Django

    fields = {}
    message = ""
    if isinstance(exc, drf_exceptions.ValidationError) and isinstance(response.data, dict):
        fields = {k: _plain(v) for k, v in response.data.items() if k != "non_field_errors"}
        message = " ".join(str(m) for m in response.data.get("non_field_errors", []))
    elif isinstance(response.data, dict):
        message = str(response.data.get("detail", ""))

    if isinstance(exc, drf_exceptions.ValidationError):
        code = "VALIDATION_ERROR"
    elif isinstance(exc, drf_exceptions.APIException):
        code = str(exc.default_code).upper()
    else:  # Django's Http404 / PermissionDenied
        code = _DRF_CODES.get(response.status_code, "ERROR")
    headers = {k: response[k] for k in ("WWW-Authenticate", "Retry-After") if k in response}
    return _envelope(code, message, fields, context, response.status_code, headers)
