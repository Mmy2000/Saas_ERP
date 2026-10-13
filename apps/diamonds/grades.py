"""The colour, clarity and cut lists stones are graded with (legacy GHZ12-GHZ14), the setting
types they are held with (GHD13a) and the model classes pieces are filed under (GHD9).

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
    GradeField.SETTING: ["Prong", "Bezel", "Pave", "Micro pave", "Channel", "Invisible",
                         "Tension", "Halo"],
}
MANAGE = "diamonds.manage"


def ensure_defaults() -> None:
    """Start each list on the usual scale the first time it is needed (model classes start
    empty: each business files its pieces its own way)."""
    present = set(StoneGrade.objects.values_list("field", flat=True).distinct())
    missing = [field for field in DEFAULTS if field not in present]
    if missing:
        StoneGrade.objects.bulk_create([
            StoneGrade(field=field, code=code, position=position)
            for field in missing for position, code in enumerate(DEFAULTS[field])])


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
        raise _error("field", _("Choose a list."))
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
    if grade.field == GradeField.MODEL:
        from apps.inventory.models import Item

        return Item.objects.filter(model_classes__contains=[grade.code]).exists()
    column = "setting_type" if grade.field == GradeField.SETTING else grade.field
    return ItemStone.objects.filter(**{f"{column}__iexact": grade.code}).exists()


def clean_model_classes(values, field: str = "model_classes") -> list[str]:
    """Model classes as listed (any case typed → the listed code); unknown ones are refused."""
    known = {code.casefold(): code for code in StoneGrade.objects.filter(
        field=GradeField.MODEL).values_list("code", flat=True)}
    cleaned = []
    for value in values or ():
        value = str(value).strip()
        if not value:
            continue
        code = known.get(value.casefold())
        if code is None:
            raise _error(field, _("%(name)s is not in the model classes list.") % {"name": value})
        if code not in cleaned:
            cleaned.append(code)
    return cleaned


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
                raise _error("code", _("Stones or pieces use it: switch it off and add the "
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
        raise DomainError(_("Stones or pieces use it: switch it off instead."),
                          code="DIAMONDS_GRADE_USED")
    grade.delete()
