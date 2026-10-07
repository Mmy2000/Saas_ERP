"""eDahab (edahabapp.com): gold 24/21/18/14 per gram, "بيع" and "شراء" for each. No silver.
Parsing follows the legacy Gweb reader (Cod/gold_sync/edahab)."""

from __future__ import annotations

import re

from .base import FeedError, Quote, fetch, number

LABEL = "eDahab"
URL = "https://edahabapp.com/"


def _labelled(segment: str, label: str):
    match = re.search(label + r"\s*:\s*<span[^>]*>\s*([\d,.]+)\s*</span>", segment, re.DOTALL)
    return number(match.group(1)) if match else None


def parse(html: str) -> Quote:
    section = re.search(r"أسعار الذهب.*?</section>", html, re.DOTALL)
    if not section:
        raise FeedError("eDahab: no gold price section on the page.")
    quote = Quote()
    # Each karat sits in its own .price-item block: splitting keeps one karat's pair together.
    for segment in re.split(r'class="price-item"', section.group(0))[1:]:
        karat = re.search(r"الذهب\s*عيار\s*(\d+)", segment)
        sell, buy = _labelled(segment, "بيع"), _labelled(segment, "شراء")
        if karat and sell is not None and buy is not None:
            quote.gold[int(karat.group(1))] = (sell, buy)
    return quote.check(LABEL)


def read() -> Quote:
    return parse(fetch(URL))
