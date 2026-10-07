"""Gold Bullion (goldbullioneg.com): gold 24/22/21/18/14 per gram. Its first price column is
what the market sells at, the second what it buys at. Parsing follows the legacy Gweb reader
(Cod/gold_sync/goldbullioneg)."""

from __future__ import annotations

import re

from .base import FeedError, Quote, fetch, number

LABEL = "Gold Bullion"
URL = "https://goldbullioneg.com/%d8%a3%d8%b3%d8%b9%d8%a7%d8%b1-%d8%a7%d9%84%d8%b0%d9%87%d8%a8/"


def _table(html: str) -> str:
    match = re.search(r'<div class="prices-table"[^>]*>(.*?)</table>', html,
                      re.DOTALL | re.IGNORECASE)
    return match.group(1) if match else html


def parse(html: str) -> Quote:
    quote = Quote()
    for row in re.finditer(r"<tr[^>]*>(.*?)</tr>", _table(html), re.DOTALL | re.IGNORECASE):
        cells = row.group(1)
        karat = re.search(r"عيار\s*(\d+)", cells)
        if not karat or "جرام" not in cells:
            continue
        values = [number(v) for v in re.findall(r'class="num[^"]*" data-val="([\d.,]+)"', cells)]
        if len(values) >= 2 and None not in values[:2]:
            quote.gold[int(karat.group(1))] = (values[0], values[1])
    if not quote.gold:
        raise FeedError("Gold Bullion: no price table on the page.")
    return quote.check(LABEL)


def read() -> Quote:
    return parse(fetch(URL))
