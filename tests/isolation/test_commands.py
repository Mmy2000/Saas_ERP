import pytest
from django.core.management import CommandError, call_command

pytestmark = pytest.mark.django_db


def test_tenant_command_requires_explicit_target(tenant_a):
    with pytest.raises(CommandError):
        call_command("tenant_info")


def test_tenant_command_runs_in_the_tenant(tenant_a, tenant_b, capsys):
    call_command("tenant_info", "--tenant", "alpha")
    out = capsys.readouterr().out
    assert out.startswith("[alpha] ")
    assert "branches=1 members=1" in out


def test_all_tenants_is_explicit(tenant_a, tenant_b, capsys):
    call_command("tenant_info", "--all-tenants")
    out = capsys.readouterr().out
    assert "[alpha]" in out and "[bravo]" in out
