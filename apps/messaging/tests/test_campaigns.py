"""WhatsApp campaigns (legacy whatsapp_gateway bulk sending): who gets them, the pace, pausing,
the opt-out list, and the pages."""

from datetime import datetime, time

import pytest
from django.utils import timezone, translation

from apps.core.errors import DomainError, ValidationError
from apps.core.tenancy import tenant_context
from apps.messaging import campaigns, gateways
from apps.messaging.campaigns import Audience, CampaignInput
from apps.messaging.models import (
    Campaign,
    CampaignStatus,
    OptOut,
    RecipientStatus,
    WhatsAppLink,
)
from apps.messaging.tests.test_whatsapp import switch
from apps.parties.services import PartyData, create_customer
from apps.platform.jobs.models import Job, JobStatus
from apps.platform.jobs.worker import run_pending
from conftest import login

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _all_day(monkeypatch):
    gateways.OUTBOX.clear()
    monkeypatch.setattr(campaigns, "SEND_FROM", time(0, 0))
    monkeypatch.setattr(campaigns, "SEND_UNTIL", time(23, 59, 59))
    with translation.override("en"):
        yield


@pytest.fixture
def shop(tenant_a):
    switch(tenant_a, whatsapp_own=True)
    with tenant_context(tenant_a.id):
        WhatsAppLink.objects.create(session_id="gweb-alpha-1", status="connected",
                                    phone="201000000000")
        gateways._CONSOLE_SESSIONS["gweb-alpha-1"] = "connected"
        for name, phone in (("Mona", "01001234567"), ("Hany", "01009990001"),
                            ("Sara", "01112223334"), ("Nour", ""), ("Omar", "01223334445")):
            create_customer(PartyData(name=name, phone=phone))
        OptOut.objects.create(phone="201223334445")
        yield tenant_a


def drain(tenant, limit=50):
    """Run the campaign's jobs now, however far ahead they were queued."""
    for _round in range(limit):
        Job.objects.filter(status=JobStatus.QUEUED, name="messaging.campaign_next").update(
            run_at=timezone.now())
        if not run_pending():
            return


def start(**kw):
    data = {"name": "Eid offer", "message": "Hello {name}, 10% off at {company} {oops",
            "audience": Audience("all"), **kw}
    return campaigns.start(CampaignInput(**data))


def test_who_gets_it(shop):
    with tenant_context(shop.id):
        found = campaigns.preview(Audience("all"))
        assert found["count"] == 3 and set(found["sample"]) == {"Hany", "Mona", "Sara"}
        reasons = {row["reason"]: row["count"] for row in found["skipped"]}
        assert reasons == {"Asked not to get campaigns": 1}  # Nour has no phone at all
        pasted = campaigns.preview(Audience("list", numbers="Ali, 01009998887\n+20 100 999 8887\n"
                                                           "0111 222 3334\nWrong, 12"))
        assert pasted["count"] == 2 and pasted["sample"][0] == "Ali"  # the second line repeats
        assert pasted["skipped"] == [{"reason": "No valid number", "count": 1},
                                     {"reason": "The same number twice", "count": 1}]
        assert campaigns.preview(Audience("owing"))["count"] == 0
        with pytest.raises(ValidationError):
            campaigns.preview(Audience("bought_since"))  # needs the date
        with pytest.raises(ValidationError):
            campaigns.preview(Audience("everyone"))


def test_sent_one_by_one_and_finished(shop):
    with tenant_context(shop.id):
        campaign = start()
        assert (campaign.total, campaign.skipped, campaign.status) == (4, 1, CampaignStatus.RUNNING)
    drain(shop)
    with tenant_context(shop.id):
        campaign.refresh_from_db()
        assert (campaign.sent, campaign.left, campaign.status) == (3, 0, CampaignStatus.DONE)
        texts = sorted(out["text"] for out in gateways.OUTBOX)
        assert texts[0].startswith("Hello ") and "Alpha" in texts[0] and texts[0].endswith("{oops")
        assert {out["phone"] for out in gateways.OUTBOX} == {"201001234567", "201009990001",
                                                            "201112223334"}
        with pytest.raises(DomainError):  # nothing to resume
            campaigns.resume(campaign.pk)


def test_one_at_a_time_and_a_resumed_campaign_never_sends_twice(shop):
    with tenant_context(shop.id):
        first = start()
        with pytest.raises(DomainError):
            start(name="Another")
        campaigns.pause(first.pk)
        campaigns.resume(first.pk)  # the old queued send is now stale
    drain(shop)
    assert len(gateways.OUTBOX) == 3  # each person once


def test_the_daily_cap_and_the_hours(shop, monkeypatch):
    with tenant_context(shop.id):
        campaign = start(daily_cap=1)
    drain(shop)
    with tenant_context(shop.id):
        campaign.refresh_from_db()
        assert (campaign.sent, campaign.status) == (1, CampaignStatus.RUNNING)
        waiting = Job.objects.get(status=JobStatus.QUEUED, name="messaging.campaign_next")
        assert timezone.localtime(waiting.run_at).date() > timezone.localdate()  # tomorrow
    monkeypatch.setattr(campaigns, "SEND_FROM", time(9, 0))
    monkeypatch.setattr(campaigns, "SEND_UNTIL", time(21, 0))
    night = timezone.make_aware(datetime(2026, 10, 8, 22, 30))
    assert timezone.localtime(campaigns._send_time(night)) == timezone.make_aware(
        datetime(2026, 10, 9, 9, 0))
    early = timezone.make_aware(datetime(2026, 10, 8, 6, 0))
    assert timezone.localtime(campaigns._send_time(early)).hour == 9
    noon = timezone.make_aware(datetime(2026, 10, 8, 12, 0))
    assert campaigns._send_time(noon) == noon


def test_failures_pause_it_and_so_does_an_unlinked_number(shop, monkeypatch):
    monkeypatch.setattr(campaigns, "FAILURES_TO_PAUSE", 2)
    monkeypatch.setattr(gateways, "own_send",
                        lambda *a, **k: gateways.Result(False, "This number is not on WhatsApp."))
    with tenant_context(shop.id):
        campaign = start()
    drain(shop)
    with tenant_context(shop.id):
        campaign.refresh_from_db()
        assert (campaign.failed, campaign.status) == (2, CampaignStatus.PAUSED)
        assert "not on WhatsApp" in campaign.note
        assert campaign.recipients.filter(status=RecipientStatus.FAILED).first().reason
    monkeypatch.undo()
    monkeypatch.setattr(campaigns, "SEND_FROM", time(0, 0))
    monkeypatch.setattr(campaigns, "SEND_UNTIL", time(23, 59, 59))
    with tenant_context(shop.id):
        WhatsAppLink.objects.update(status="disconnected")
        with pytest.raises(DomainError):  # cannot carry on without the number
            campaigns.resume(campaign.pk)
        campaigns.cancel(campaign.pk)
        WhatsAppLink.objects.update(status="connected")
        again = start(name="Second try")
        WhatsAppLink.objects.update(status="disconnected")
    drain(shop)
    with tenant_context(shop.id):
        again.refresh_from_db()
        assert again.status == CampaignStatus.PAUSED and "not linked" in again.note


def test_an_opt_out_during_the_campaign(shop):
    with tenant_context(shop.id):
        campaign = start()
        OptOut.objects.create(phone="201112223334")
    drain(shop)
    with tenant_context(shop.id):
        campaign.refresh_from_db()
        assert (campaign.sent, campaign.skipped) == (2, 2)


def test_pages_and_api(shop, tenant_a):
    client = login(tenant_a, language="en")
    assert "Numbers that do not want campaigns" in client.get(
        "/customers/campaigns/").content.decode()
    assert "Who gets it" in client.get("/customers/campaigns/new/").content.decode()
    preview = client.post("/api/v1/messages/campaigns/preview/", {"kind": "all"},
                          content_type="application/json")
    assert preview.json()["count"] == 3
    made = client.post("/api/v1/messages/campaigns/", {
        "name": "Eid", "message": "Hi {name}", "audience": {"kind": "all"}},
        content_type="application/json", HTTP_IDEMPOTENCY_KEY="c1")
    assert made.status_code == 201, made.content
    pk = made.json()["id"]
    page = client.get(f"/customers/campaigns/{pk}/").content.decode()
    assert "Hi {name}" in page and "Asked not to get campaigns" in page
    paused = client.post(f"/api/v1/messages/campaigns/{pk}/pause/", HTTP_IDEMPOTENCY_KEY="c2")
    assert paused.json()["status"] == "paused"
    assert client.get(f"/api/v1/messages/campaigns/{pk}/").json()["left"] == 3

    added = client.post("/api/v1/messages/opt-outs/", {"phone": "0100 555 6667"},
                        content_type="application/json")
    assert added.json()["phone"] == "201005556667"
    assert client.post("/api/v1/messages/opt-outs/", {"phone": "1"},
                       content_type="application/json").status_code == 400
    assert client.delete(f"/api/v1/messages/opt-outs/?id={added.json()['id']}").status_code == 204

    switch(tenant_a, whatsapp_own=False)
    assert client.get("/customers/campaigns/").status_code == 403
    with tenant_context(tenant_a.id):
        assert Campaign.objects.count() == 1
