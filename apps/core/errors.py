"""Application errors (§9.5). Services raise these; the API exception handler maps them to the
error envelope (§11.5) and template views turn them into messages."""

from __future__ import annotations


class AppError(Exception):
    code = "ERROR"
    status = 400

    def __init__(self, message: str = "", *, code: str | None = None,
                 fields: dict[str, list[str]] | None = None):
        super().__init__(message or self.__class__.__name__)
        self.message = message or self.__class__.__name__
        if code:
            self.code = code
        self.fields = fields or {}


class ValidationError(AppError):
    code = "VALIDATION_ERROR"
    status = 400


class PermissionDenied(AppError):
    code = "PERMISSION_DENIED"
    status = 403


class NotFound(AppError):
    code = "NOT_FOUND"
    status = 404


class Conflict(AppError):
    """Stale version, idempotency replay, or an illegal state transition."""

    code = "CONFLICT"
    status = 409


class DomainError(AppError):
    """A business rule was violated. Give it a specific code, e.g. SALES_DISCOUNT_LIMIT."""

    code = "DOMAIN_RULE"
    status = 422
