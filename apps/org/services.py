"""Branch management (§7.1, §15)."""

from __future__ import annotations

from dataclasses import dataclass

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils.translation import gettext as _

from apps.core.errors import NotFound, ValidationError

from .models import Branch


@dataclass(frozen=True)
class BranchInput:
    code: int
    name: str
    phone: str = ""
    address: str = ""
    is_head_office: bool = False


def _save(branch: Branch, data: BranchInput, actor) -> Branch:
    branch.code = data.code
    branch.name = data.name.strip()
    branch.phone = data.phone.strip()
    branch.address = data.address.strip()
    branch.is_head_office = data.is_head_office
    branch.updated_by = getattr(actor, "user", None)
    branch._stamp_tenant()
    with transaction.atomic():
        if data.is_head_office:
            # Exactly one head office: moving the flag clears it elsewhere first.
            Branch.objects.filter(is_head_office=True).exclude(pk=branch.pk).update(
                is_head_office=False)
        try:
            branch.full_clean()
        except DjangoValidationError as exc:
            raise ValidationError(_("Please correct the highlighted fields."),
                                  fields=exc.message_dict) from exc
        branch.save()
    return branch


def create_branch(data: BranchInput, *, actor=None) -> Branch:
    if actor is not None:
        actor.require("org.branch.manage")
    from apps.platform.tenants.limits import check_branch_limit

    check_branch_limit()
    return _save(Branch(created_by=getattr(actor, "user", None)), data, actor)


def update_branch(branch_id: int, data: BranchInput, *, actor=None) -> Branch:
    if actor is not None:
        actor.require("org.branch.manage")
    branch = Branch.objects.filter(pk=branch_id).first()
    if branch is None:
        raise NotFound(_("Not found."))
    return _save(branch, data, actor)


def set_branch_active(branch_id: int, active: bool, *, actor=None) -> Branch:
    if actor is not None:
        actor.require("org.branch.manage")
    branch = Branch.objects.filter(pk=branch_id).first()
    if branch is None:
        raise NotFound(_("Not found."))
    if not active and branch.is_head_office:
        raise ValidationError(_("The head office cannot be deactivated."),
                              code="ORG_HEAD_OFFICE_ACTIVE")
    # TODO(inventory/treasury): refuse while the branch still holds stock or cash (§6.7).
    branch.is_active = active
    branch.save(update_fields=["is_active", "updated_at"])
    return branch
