"""The colour, clarity and cut lists stones are graded with (legacy GHZ12-GHZ14).

Each business keeps its own lists, starting from the usual GIA scales. Entering a stone offers
the active codes; a value typed in another case is stored as the listed code ("vs1" → "VS1"),
and a value not in the list is kept as typed, so stones from Excel or old records still load.
"""

from __future__ import annotations

from django.db import IntegrityError, transaction
from django.utils.translation import gettext as _

from apps.core.errors import DomainError, NotFound, ValidationError

from .models import GradeField, ItemStone, StoneGrade

DEFAULTS = {
    GradeField.COLOR: ["D", "E", "F", "G", "H", "I", "J", "K", "L", "M"],
    GradeField.CLARITY: ["FL", "IF", "VVS1", "VVS2", "VS1", "VS2", "SI1", "SI2", "I1", "I2",
                         "I3"],
    GradeField.CUT: ["Excellent", "Very Good", "Good", "Fair", "Poor"],
}
MANAGE = "diamonds.manage"


def ensure_defaults() -> None:
    """Start a business on the usual scales, the first time its lists are needed."""
    if StoneGrade.objects.exists():
        return
    StoneGrade.objects.bulk_create([
        StoneGrade(field=field, code=code, position=position)
        for field, codes in DEFAULTS.items() for position, code in enumerate(codes)])


def choices() -> dict[str, list[str]]:
    """Active codes per field, in order: {"color": ["D", …], …}."""
    ensure_defaults()
    found: dict[str, list[str]] = {field: [] for field in GradeField.values}
    for field, code in StoneGrade.objects.filter(is_active=True).values_list("field", "code"):
        found[field].append(code)
    return found


def normalizer():
    """A function (field, value) → the listed code matching `value` in any case, or `value`."""
    ensure_defaults()
    known: dict[tuple[str, str], str] = {
        (field, code.casefold()): code
        for field, code in StoneGrade.objects.values_list("field", "code")}

    def normalize(field: str, value: str) -> str:
        return known.get((field, value.casefold()), value) if value else value

    return normalize


def _error(field: str, message: str) -> ValidationError:
    return ValidationError(message, fields={field: [message]})


def add_grade(field: str, code: str, *, actor=None) -> StoneGrade:
    if actor is not None:
        actor.require(MANAGE)
    if field not in GradeField.values:
        raise _error("field", _("Choose colour, clarity or cut."))
    code = code.strip()[:20]
    if not code:
        raise _error("code", _("This field is required."))
    if StoneGrade.objects.filter(field=field, code__iexact=code).exists():
        raise _error("code", _("Already in the list."))
    last = StoneGrade.objects.filter(field=field).order_by("-position").first()
    try:
        with transaction.atomic():
            return StoneGrade.objects.create(field=field, code=code,
                                             position=(last.position + 1) if last else 0)
    except IntegrityError:
        raise _error("code", _("Already in the list.")) from None


def used(grade: StoneGrade) -> bool:
    return ItemStone.objects.filter(**{f"{grade.field}__iexact": grade.code}).exists()


def update_grade(grade_id: int, *, code: str | None = None, is_active: bool | None = None,
                 move: int = 0, actor=None) -> StoneGrade:
    """Rename (unused codes only: stones keep the text they were saved with), switch on or off,
    or move one place up (-1) or down (+1)."""
    if actor is not None:
        actor.require(MANAGE)
    with transaction.atomic():
        grade = StoneGrade.objects.select_for_update().filter(pk=grade_id).first()
        if grade is None:
            raise NotFound(_("Not found."))
        if code is not None and code.strip() != grade.code:
            code = code.strip()[:20]
            if not code:
                raise _error("code", _("This field is required."))
            if used(grade):
                raise _error("code", _("Stones are graded with it: switch it off and add the "
                                       "new one instead."))
            if StoneGrade.objects.filter(field=grade.field, code__iexact=code).exclude(
                    pk=grade.pk).exists():
                raise _error("code", _("Already in the list."))
            grade.code = code
        if is_active is not None:
            grade.is_active = is_active
        if move:
            siblings = list(StoneGrade.objects.select_for_update().filter(field=grade.field)
                            .order_by("position", "id"))
            index = next(i for i, g in enumerate(siblings) if g.pk == grade.pk)
            other = index + (1 if move > 0 else -1)
            if 0 <= other < len(siblings):
                siblings[index], siblings[other] = siblings[other], siblings[index]
                for position, sibling in enumerate(siblings):
                    if sibling.position != position:
                        sibling.position = position
                        if sibling.pk != grade.pk:
                            sibling.save(update_fields=["position", "updated_at"])
                grade.position = next(g.position for g in siblings if g.pk == grade.pk)
        grade.save()
    return grade


def delete_grade(grade_id: int, *, actor=None) -> None:
    if actor is not None:
        actor.require(MANAGE)
    grade = StoneGrade.objects.filter(pk=grade_id).first()
    if grade is None:
        raise NotFound(_("Not found."))
    if used(grade):
        raise DomainError(_("Stones are graded with it: switch it off instead."),
                          code="DIAMONDS_GRADE_USED")
    grade.delete()
