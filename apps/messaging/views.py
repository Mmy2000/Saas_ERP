from django.core.paginator import Paginator
from django.db.models import F
from django.http import Http404
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from apps.core.documents import document_url
from apps.iam.authz import permission_required

from .models import Channel, DigestRecipient, MessageStatus, OutboundMessage
from .services import share_path, shared_message

FILTERS = ("all", "email", "whatsapp", "failed")


@permission_required("messaging.view")
def messages_log(request):
    """Settings → Sent messages: every e-mail and WhatsApp link, and who gets the daily
    reminders."""
    from apps.iam.models import Membership, MembershipStatus

    chosen = request.GET.get("show", "all")
    chosen = chosen if chosen in FILTERS else "all"
    messages = OutboundMessage.objects.select_related("created_by", "party")
    if chosen in (Channel.EMAIL, Channel.WHATSAPP):
        messages = messages.filter(channel=chosen)
    elif chosen == "failed":
        messages = messages.filter(status=MessageStatus.FAILED)
    page = Paginator(messages.order_by("-created_at", "-id"), 30).get_page(
        request.GET.get("page"))
    rows = [{"message": m, "url": document_url(m.doc_model, m.doc_id) if m.doc_model else None,
             "link": share_path(m) if m.status == MessageStatus.SHARED else ""}
            for m in page.object_list]
    members = None
    if request.actor.can("admin.users.manage"):
        chosen_members = set(DigestRecipient.objects.values_list("membership_id", flat=True))
        members = [{"member": m, "on": m.pk in chosen_members}
                   for m in Membership.objects.filter(status=MembershipStatus.ACTIVE)
                   .select_related("user").order_by("username")]
    return render(request, "messaging/messages.html", {
        "page": page, "rows": rows, "show": chosen, "filters": FILTERS, "members": members,
        "failed": OutboundMessage.objects.filter(status=MessageStatus.FAILED).count(),
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
