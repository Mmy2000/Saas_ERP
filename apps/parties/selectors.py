from __future__ import annotations

import re

from django.db.models import OuterRef, Q, QuerySet, Subquery

from .models import Party, PartyRole
from .services import national_id_lookup_hash


def parties_with_role(role: str) -> QuerySet[Party]:
    """Parties holding `role`, annotated with their per-role `code`."""
    code = PartyRole.objects.filter(party=OuterRef("pk"), role=role).values("code")[:1]
    return (Party.objects.filter(roles__role=role)
            .annotate(code=Subquery(code))
            .select_related("home_branch"))


def search(queryset: QuerySet[Party], term: str) -> QuerySet[Party]:
    """Match name, phone digits, code, or an exact national ID."""
    term = (term or "").strip()
    if not term:
        return queryset
    condition = Q(name__icontains=term)
    digits = re.sub(r"\D", "", term)
    if len(digits) >= 3:
        # Local "010…" is stored as "+2010…": drop the trunk zero for the contains match.
        condition |= Q(phone__contains=digits)
        if digits.lstrip("0"):
            condition |= Q(phone_e164__contains=digits.lstrip("0"))
        condition |= Q(national_id_hash=national_id_lookup_hash(term))
    if digits and len(digits) <= 9 and digits == term:
        condition |= Q(code=int(digits))
    return queryset.filter(condition)
