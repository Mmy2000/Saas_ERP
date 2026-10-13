from django.core.paginator import Paginator
from django.db.models import F
from django.http import Http404
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from apps.core.documents import document_url
from apps.iam.authz import permission_required

from . import usage
from .models import Channel, DigestRecipient, MessageStatus, OutboundMessage
from .services import share_path, shared_message

FILTERS = ("all", "email", "whatsapp", "failed")


@permission_required("messaging.view")
def messages_log(request):
    """Settings → Sent messages: every e-mail and WhatsApp link, and who gets the daily
    reminders."""
    from apps.iam.models import Membership, MembershipStatus

    show = request.GET.get("show", "all")
    show = show if show in FILTERS else "all"
    messages = OutboundMessage.objects.select_related("created_by", "party")
    if show in (Channel.EMAIL, Channel.WHATSAPP):
        messages = messages.filter(channel=show)
    elif show == "failed":
        messages = messages.filter(status=MessageStatus.FAILED)
    page = Paginator(messages.order_by("-created_at", "-id"), 30).get_page(
        request.GET.get("page"))
    rows = [{"message": m, "url": document_url(m.doc_model, m.doc_id) if m.doc_model else None,
             "link": share_path(m) if m.status == MessageStatus.SHARED else ""}
            for m in page.object_list]
    members = None
    if request.actor.can("admin.users.manage"):
        chosen = {r.membership_id: r for r in DigestRecipient.objects.all()}
        members = [{"member": m, "daily": getattr(chosen.get(m.pk), "daily", False),
                    "monthly": getattr(chosen.get(m.pk), "monthly", False)}
                   for m in Membership.objects.filter(status=MembershipStatus.ACTIVE)
                   .select_related("user").order_by("username")]
    return render(request, "messaging/messages.html", {
        "page": page, "rows": rows, "show": show, "filters": FILTERS, "members": members,
        "failed": OutboundMessage.objects.filter(status=MessageStatus.FAILED).count(),
        "wa_now": usage.usage(*usage.month_bounds()),
        "wa_before": usage.usage(*usage.month_bounds(back=1)),
    })


@never_cache
@require_GET
def shared(request, token):
    """A document shared on WhatsApp: the PDF, for whoever has the link, until it ends or the
    shop stops it. No login."""
    from apps.platform.tenants.features import is_enabled
    from apps.printing.pdf import PdfUnavailable, pdf_response

    from .services import message_pdf

    message = shared_message(token)
    if message is None or not is_enabled(message.tenant_id, "messaging"):
        raise Http404
    try:
        content, filename = message_pdf(message)
    except LookupError as exc:
        raise Http404 from exc
    except PdfUnavailable:
        return render(request, "printing/pdf_unavailable.html", status=503)
    OutboundMessage.objects.filter(pk=message.pk).update(
        open_count=F("open_count") + 1, opened_at=message.opened_at or timezone.now())
    response = pdf_response(content, filename, inline=True)
    response["X-Robots-Tag"] = "noindex, nofollow"
    response["Referrer-Policy"] = "no-referrer"
    return response


@permission_required("messaging.whatsapp.link")
def whatsapp_settings(request):
    """Settings → WhatsApp: link the shop's own number (scan the QR) and send a test."""
    from . import gateways
    from .services import whatsapp_link

    return render(request, "messaging/whatsapp.html", {
        "link": whatsapp_link(), "gateway_ready": gateways.own_ready(),
        "simulated": gateways.console(),
        "hosted": request.actor.can("messaging.whatsapp.api"),
        "hosted_ready": gateways.hosted_ready(),
    })


@permission_required("messaging.campaign.manage")
def campaigns(request):
    """Customers → WhatsApp campaigns: past and current campaigns, and the opt-out list."""
    from .models import Campaign, OptOut
    from .services import whatsapp_link

    return render(request, "messaging/campaigns.html", {
        "campaigns": Campaign.objects.select_related("created_by")[:50],
        "opt_outs": OptOut.objects.all()[:500], "link": whatsapp_link(),
    })


@permission_required("messaging.campaign.manage")
def campaign_new(request):
    from apps.org.models import Branch

    from .campaigns import MIN_GAP
    from .services import whatsapp_link

    return render(request, "messaging/campaign_form.html", {
        "branches": Branch.objects.filter(is_active=True).order_by("code"),
        "link": whatsapp_link(), "min_gap": MIN_GAP,
    })


@permission_required("messaging.campaign.manage")
def campaign(request, pk):
    from .models import Campaign, RecipientStatus

    found = Campaign.objects.select_related("created_by").filter(pk=pk).first()
    if found is None:
        raise Http404
    show = request.GET.get("show", "")
    people = found.recipients.select_related("party").order_by("id")
    if show in RecipientStatus.values:
        people = people.filter(status=show)
    page = Paginator(people, 50).get_page(request.GET.get("page"))
    return render(request, "messaging/campaign.html", {
        "campaign": found, "page": page, "show": show, "statuses": RecipientStatus.choices,
    })
