import pytest

from apps.core.sequences import allocate_number
from apps.core.tenancy import tenant_context
from apps.org.models import Branch

pytestmark = pytest.mark.django_db


def test_numbers_are_consecutive_per_branch_and_type(tenant_a):
    with tenant_context(tenant_a.id):
        head = Branch.objects.get(code=1)
        second = Branch.objects.create(code=2, name="Second")
        assert allocate_number("SI", branch=head, fiscal_year=2026) == "01-SI-2026-000001"
        assert allocate_number("SI", branch=head, fiscal_year=2026) == "01-SI-2026-000002"
        assert allocate_number("SI", branch=second, fiscal_year=2026) == "02-SI-2026-000001"
        assert allocate_number("SR", branch=head, fiscal_year=2026) == "01-SR-2026-000001"
        assert allocate_number("SI", branch=head, fiscal_year=2027) == "01-SI-2027-000001"


def test_tenant_wide_sequence_without_branch(tenant_a):
    with tenant_context(tenant_a.id):
        assert allocate_number("JV", branch=None, fiscal_year=2026) == "JV-2026-000001"
        assert allocate_number("JV", branch=None, fiscal_year=2026) == "JV-2026-000002"


def test_sequences_are_independent_per_tenant(tenant_a, tenant_b):
    with tenant_context(tenant_a.id):
        head_a = Branch.objects.get(code=1)
        allocate_number("SI", branch=head_a, fiscal_year=2026)
        allocate_number("SI", branch=head_a, fiscal_year=2026)
    with tenant_context(tenant_b.id):
        head_b = Branch.objects.get(code=1)
        assert allocate_number("SI", branch=head_b, fiscal_year=2026) == "01-SI-2026-000001"
