"""Server-side PDF: the same HTML the shop prints, turned into a PDF by headless Chromium.

Chromium shapes Arabic exactly like the browser on screen, so a PDF looks like the printed
page. The page is loaded from a made-up origin and every request it makes is answered here:
/static/ from the static files, /media/ from file storage, anything else refused. Nothing
leaves the server, and JavaScript is off (a custom design cannot run code).
"""

from __future__ import annotations

import base64
import logging
import mimetypes
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from django.conf import settings
from django.contrib.staticfiles import finders
from django.contrib.staticfiles.storage import staticfiles_storage
from django.core.files.storage import default_storage
from django.http import HttpResponse
from django.utils.http import content_disposition_header

logger = logging.getLogger(__name__)

ORIGIN = "http://gweb-pdf.invalid"
TIMEOUT_MS = 20_000
# Chromium is heavy: a few renders at a time per process, the rest wait their turn. Each
# worker thread keeps its own browser (Playwright objects belong to the thread that made them).
_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="pdf")
_local = threading.local()


FOOTER = ('<div style="width:100%;font:8px system-ui,sans-serif;color:#71717a;text-align:center">'
          '<span class="pageNumber"></span> / <span class="totalPages"></span></div>')


class PdfUnavailable(Exception):
    """Chromium is not installed or failed to start."""


@dataclass(frozen=True)
class Paper:
    """`width`/`height` override the page's own @page size; a roll (height None) is as long as
    its content."""

    width: str | None = None
    height: str | None = None
    roll: bool = False
    landscape: bool = False
    page_numbers: bool = False  # "2 / 5" at the foot of every page (reports)


A4 = Paper()


def _url_path(prefix: str) -> str:
    prefix = "/" + prefix.lstrip("/")
    return prefix if prefix.endswith("/") else prefix + "/"


def _local_file(path: str) -> tuple[bytes, str] | None:
    """(content, type) for a /static/ or /media/ path, or None."""
    static, media = _url_path(settings.STATIC_URL), _url_path(settings.MEDIA_URL)
    try:
        if path.startswith(static):
            name = path[len(static):]
            found = finders.find(name)
            if found:
                with open(found, "rb") as handle:
                    content = handle.read()
            elif staticfiles_storage.exists(name):
                with staticfiles_storage.open(name) as handle:
                    content = handle.read()
            else:
                return None
        elif path.startswith(media):
            name = path[len(media):]
            if ".." in name.split("/") or not default_storage.exists(name):
                return None
            with default_storage.open(name) as handle:
                content = handle.read()
        else:
            return None
    except (OSError, ValueError):
        return None
    return content, _content_type(path)


# Not every system's mimetypes table knows web fonts (Windows does not).
_TYPES = {".woff2": "font/woff2", ".woff": "font/woff", ".svg": "image/svg+xml",
          ".css": "text/css", ".png": "image/png", ".webp": "image/webp"}


def _content_type(path: str) -> str:
    suffix = path[path.rfind("."):].lower() if "." in path else ""
    return _TYPES.get(suffix) or mimetypes.guess_type(path)[0] or "application/octet-stream"


def _browser():
    browser = getattr(_local, "browser", None)
    if browser is not None and browser.is_connected():
        return browser
    try:
        from playwright.sync_api import sync_playwright

        if getattr(_local, "playwright", None) is None:
            _local.playwright = sync_playwright().start()
        _local.browser = _local.playwright.chromium.launch(args=["--disable-dev-shm-usage"])
    except Exception as exc:
        raise PdfUnavailable(str(exc)) from exc
    return _local.browser


def _render(html: str, paper: Paper) -> bytes:
    from urllib.parse import urlsplit

    browser = _browser()
    context = browser.new_context(java_script_enabled=False)
    try:
        page = context.new_page()
        page.set_default_timeout(TIMEOUT_MS)

        def answer(route):
            url = route.request.url
            if url == f"{ORIGIN}/document":
                return route.fulfill(status=200, body=html.encode("utf-8"),
                                     content_type="text/html; charset=utf-8")
            if url.startswith(ORIGIN + "/"):
                found = _local_file(urlsplit(url).path)
                if found:
                    return route.fulfill(status=200, body=found[0], content_type=found[1])
                return route.fulfill(status=404, body=b"")
            if url.startswith("data:"):
                return route.continue_()
            return route.abort("blockedbyclient")  # no outside requests, ever

        page.route("**/*", answer)
        page.goto(f"{ORIGIN}/document", wait_until="load")
        page.emulate_media(media="print")
        page.evaluate("document.fonts.ready")  # runs in Playwright's world, not the page's
        options = {"print_background": True, "prefer_css_page_size": True}
        if paper.roll:
            # A roll is 80 mm wide and as long as the receipt: measure it in print layout.
            # (Never shorter than it is wide: Chromium would turn the page sideways.)
            height = max(page.locator("body").bounding_box()["height"] + 24, 310)
            options.update(width=paper.width or "80mm", height=f"{int(height)}px",
                           prefer_css_page_size=False)
        elif paper.width and paper.height:
            options.update(width=paper.width, height=paper.height, prefer_css_page_size=False)
        if paper.landscape:
            options["landscape"] = True
        if paper.page_numbers:
            options.update(display_header_footer=True, header_template="<span></span>",
                           footer_template=FOOTER)
        return page.pdf(**options)
    finally:
        context.close()


def _render_or_reset(html: str, paper: Paper) -> bytes:
    try:
        return _render(html, paper)
    except Exception:
        # A broken browser is dropped so the next render starts a fresh one.
        browser, _local.browser = getattr(_local, "browser", None), None
        if browser is not None:
            try:
                browser.close()
            except Exception:  # noqa: BLE001 - it is already gone
                pass
        raise


def html_to_pdf(html: str, paper: Paper = A4) -> bytes:
    """Raises PdfUnavailable when there is no Chromium to render with."""
    future = _POOL.submit(_render_or_reset, html, paper)
    try:
        return future.result(timeout=TIMEOUT_MS / 1000 * 2)
    except PdfUnavailable:
        raise
    except Exception as exc:
        logger.exception("PDF rendering failed")
        raise PdfUnavailable(str(exc)) from exc


def logo_src(profile) -> str:
    """The client's logo for a PDF page: its /media/ address (answered locally), or the image
    itself as a data: URL when it lives in outside storage."""
    logo = getattr(profile, "logo", None)
    if not logo:
        return ""
    url = logo.url
    if not url.startswith(("http://", "https://")):
        return url
    try:
        with logo.open("rb") as handle:
            data = base64.b64encode(handle.read()).decode("ascii")
    except (OSError, ValueError):
        return ""
    return f"data:{_content_type(logo.name)};base64,{data}"


def pdf_response(content: bytes, filename: str, *, inline: bool = False) -> HttpResponse:
    response = HttpResponse(content, content_type="application/pdf")
    response["Content-Disposition"] = content_disposition_header(not inline, filename)
    response["X-Content-Type-Options"] = "nosniff"
    return response


def sheet_pdf(template: str, context: dict, *, title, landscape: bool = False) -> bytes:
    """A page made from printing/sheet.html (reports, statements) as PDF bytes, with page
    numbers. Needs no request (background jobs use it); raises PdfUnavailable."""
    from django.template.loader import render_to_string

    from apps.core.appearance import accent_swatch
    from apps.org.models import TenantProfile

    profile = TenantProfile.objects.first()
    html = render_to_string(template, {
        **context, "sheet_title": title, "profile": profile, "logo": logo_src(profile),
        "landscape": landscape, "accent": accent_swatch(getattr(profile, "accent", "")),
    })
    return html_to_pdf(html, Paper(landscape=landscape, page_numbers=True))


def pdf_page(request, template: str, context: dict, *, title, filename: str,
             landscape: bool = False) -> HttpResponse:
    """`sheet_pdf` as a downloaded file; without Chromium, a page that says so (503)."""
    from django.shortcuts import render

    try:
        content = sheet_pdf(template, context, title=title, landscape=landscape)
    except PdfUnavailable:
        return render(request, "printing/pdf_unavailable.html", status=503)
    return pdf_response(content, filename)
