"""Idempotency-Key support for API actions with financial effects (§11.4, §16.3).

    @idempotent
    def create(self, request): ...

The key is reserved (row inserted) before the action runs, inside the request transaction.
A concurrent request with the same key blocks on the unique index until the first one
commits, then replays its stored response. A failed action releases the key so the client
can retry. Keys are per (tenant, user).
"""

from __future__ import annotations

import json
from functools import wraps

from django.db import IntegrityError, transaction
from django.utils.translation import gettext as _
from rest_framework.response import Response
from rest_framework.utils.encoders import JSONEncoder

from apps.core.errors import Conflict, ValidationError
from apps.core.models import IdempotencyRecord

HEADER = "Idempotency-Key"


def _jsonable(data):
    return json.loads(json.dumps(data, cls=JSONEncoder))  # Decimals/dates → JSON-safe values


def idempotent(action):
    @wraps(action)
    def wrapper(self, request, *args, **kwargs):
        key = (request.headers.get(HEADER) or "").strip()
        if not key or len(key) > 64:
            raise ValidationError(_("Send an Idempotency-Key header with this request."),
                                  code="IDEMPOTENCY_KEY_REQUIRED")
        try:
            with transaction.atomic():
                record = IdempotencyRecord.objects.create(user=request.user, key=key,
                                                          request_path=request.path)
        except IntegrityError:
            record = IdempotencyRecord.objects.get(user=request.user, key=key)
            if record.request_path != request.path:
                raise Conflict(_("This Idempotency-Key was used for another request."),
                               code="IDEMPOTENCY_KEY_REUSED") from None
            if record.response_status == 0:
                raise Conflict(_("The same request is still being processed."),
                               code="IDEMPOTENCY_IN_PROGRESS") from None
            return Response(record.response_body, status=record.response_status,
                            headers={"Idempotent-Replay": "true"})
        try:
            response = action(self, request, *args, **kwargs)
        except Exception:
            record.delete()
            raise
        if 200 <= response.status_code < 300:
            record.response_status = response.status_code
            record.response_body = _jsonable(response.data)
            record.save(update_fields=["response_status", "response_body", "updated_at"])
        else:
            record.delete()
        return response

    return wrapper
