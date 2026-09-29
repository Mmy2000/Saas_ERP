import pytest
from django.db import connection

from apps.core.errors import ValidationError
from apps.core.tenancy import tenant_context
from apps.parties.domain import InvalidPhone, normalize_national_id, normalize_phone
from apps.parties.models import Party
from apps.parties.selectors import parties_with_role, search
from apps.parties.services import (
    CustomerData,
    PartyData,
    create_customer,
    create_supplier,
    set_party_active,
    update_customer,
)
from conftest import login


class TestNormalization:
    @pytest.mark.parametrize("raw", ["01012345678", "+201012345678", "00201012345678",
                                     "010 1234 5678"])
    def test_egyptian_mobile(self, raw):
        assert normalize_phone(raw, "EG") == "+201012345678"

    def test_uae(self):
        assert normalize_phone("050 123 4567", "AE") == "+971501234567"

    def test_blank_and_invalid(self):
        assert normalize_phone("  ", "EG") == ""
        with pytest.raises(InvalidPhone):
            normalize_phone("12", "EG")

    def test_national_id(self):
        assert normalize_national_id(" 2900-101 01234.56 ") == "29001010123456"


@pytest.mark.django_db
class TestCustomers:
    def test_create_assigns_code_and_normalizes(self, tenant_a):
        with tenant_context(tenant_a.id):
            first = create_customer(PartyData(name="Mona Ali", phone="01012345678"))
            second = create_customer(PartyData(name="Omar"))
            rows = {p.pk: p.code for p in parties_with_role("customer")}
            assert (rows[first.pk], rows[second.pk]) == (1, 2)
            assert first.phone_e164 == "+201012345678"
            assert first.customer_profile.phone_e164 == "+201012345678"

    def test_phone_identifies_one_customer(self, tenant_a):
        with tenant_context(tenant_a.id):
            create_customer(PartyData(name="Mona", phone="01012345678"))
            with pytest.raises(ValidationError) as exc:
                create_customer(PartyData(name="Someone else", phone="+20 10 1234 5678"))
            assert exc.value.code == "PARTIES_DUPLICATE_PHONE"
            # A supplier may share a phone with a customer.
            create_supplier(PartyData(name="Workshop Co", phone="01012345678"))

    def test_same_phone_in_another_tenant_is_fine(self, tenant_a, tenant_b):
        with tenant_context(tenant_a.id):
            create_customer(PartyData(name="Mona", phone="01012345678"))
        with tenant_context(tenant_b.id):
            create_customer(PartyData(name="Mona B", phone="01012345678"))

    def test_invalid_phone_is_a_field_error(self, tenant_a):
        with tenant_context(tenant_a.id), pytest.raises(ValidationError) as exc:
            create_customer(PartyData(name="X", phone="123"))
        assert "phone" in exc.value.fields

    def test_national_id_encrypted_and_searchable(self, tenant_a):
        with tenant_context(tenant_a.id):
            party = create_customer(PartyData(name="Hana", national_id="29001010123456"))
            with connection.cursor() as cursor:
                cursor.execute("SELECT national_id_encrypted FROM parties_party WHERE id = %s",
                               [party.pk])
                stored = cursor.fetchone()[0]
            assert "29001010123456" not in stored
            assert Party.objects.get(pk=party.pk).national_id == "29001010123456"
            found = search(parties_with_role("customer"), "2900-1010-1234-56")
            assert [p.pk for p in found] == [party.pk]

    def test_update_keeps_national_id_when_not_given(self, tenant_a):
        with tenant_context(tenant_a.id):
            party = create_customer(PartyData(name="Hana", national_id="123"))
            update_customer(party.pk, PartyData(name="Hana M."), CustomerData(ring_size="16"))
            party.refresh_from_db()
            assert (party.name, party.national_id) == ("Hana M.", "123")
            assert party.customer_profile.ring_size == "16"

    def test_search_by_code_phone_and_name(self, tenant_a):
        with tenant_context(tenant_a.id):
            mona = create_customer(PartyData(name="Mona Ali", phone="01012345678"))
            create_customer(PartyData(name="Omar Saad", phone="01198765432"))
            base = parties_with_role("customer")
            assert [p.pk for p in search(base, "1")] == [mona.pk]  # code 1
            assert [p.pk for p in search(base, "0101234")] == [mona.pk]
            assert [p.pk for p in search(base, "mona")] == [mona.pk]

    def test_deactivate(self, tenant_a):
        with tenant_context(tenant_a.id):
            party = create_customer(PartyData(name="Old"))
            set_party_active(party.pk, "customer", False)
            party.refresh_from_db()
            assert not party.is_active


@pytest.mark.django_db
class TestApi:
    def test_create_list_and_patch(self, tenant_a):
        client = login(tenant_a)
        created = client.post("/api/v1/parties/customers/",
                              {"name": "Mona", "phone": "01012345678", "national_id": "290",
                               "birth_date": None, "home_branch": None},
                              content_type="application/json")
        assert created.status_code == 201, created.content
        body = created.json()
        assert (body["code"], body["phone_e164"], body["national_id"]) == (
            1, "+201012345678", "290")

        listed = client.get("/api/v1/parties/customers/?q=mona").json()
        assert [c["id"] for c in listed["results"]] == [body["id"]]

        patched = client.patch(f"/api/v1/parties/customers/{body['id']}/", {"notes": "VIP"},
                               content_type="application/json")
        assert patched.status_code == 200
        assert (patched.json()["notes"], patched.json()["phone"]) == ("VIP", "01012345678")

    def test_field_errors_use_the_envelope(self, tenant_a):
        client = login(tenant_a)
        response = client.post("/api/v1/parties/customers/", {"name": "X", "phone": "12"},
                               content_type="application/json")
        assert response.status_code == 400
        assert "phone" in response.json()["error"]["fields"]

    def test_national_id_hidden_without_pii_permission(self, tenant_a, make_member):
        with tenant_context(tenant_a.id):
            party = create_customer(PartyData(name="Hana", national_id="290"))
        make_member(tenant_a, "mgr", "manager")
        make_member(tenant_a, "val", "viewer")
        assert login(tenant_a, "mgr").get(
            f"/api/v1/parties/customers/{party.pk}/").json()["national_id"] == "290"
        assert "national_id" not in login(tenant_a, "val").get(
            f"/api/v1/parties/customers/{party.pk}/").json()

    def test_suppliers_default_to_companies(self, tenant_a):
        client = login(tenant_a)
        body = client.post("/api/v1/parties/suppliers/", {"name": "Cairo Gold Factory"},
                           content_type="application/json").json()
        assert body["kind"] == "organization"
        assert client.get(f"/api/v1/parties/customers/{body['id']}/").status_code == 404
