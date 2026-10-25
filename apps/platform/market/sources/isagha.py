"""iSagha (market.isagha.com): gold 24/22/21/18 and silver 999, sell and buy per gram.
Parsing follows the legacy Gweb reader (Cod/gold_sync/isagha).

The site restyles its page now and then. In October 2026 the div around the table gained extra
classes (`class="desktop-table mb-8 …"`) and each price moved into its own tag
(`<span …>5400</span> ج.م`), so class names are matched as words and cells are read without
their tags. Both layouts are tested (tests/fixtures/isagha*.html.gz)."""

from __future__ import annotations

import re

from .base import FeedError, Quote, fetch, number

LABEL = "iSagha"
URL = "https://market.isagha.com/prices/egypt"
SILVER_URL = "https://market.isagha.com/prices/silver"
GOLD_LABELS = {24: "عيار 24", 22: "عيار 22", 21: "عيار 21", 18: "عيار 18"}
SILVER_LABEL = "عيار 999"


def _egp(cell: str):
    """The amount in pounds in a cell, its tags aside: "5400 ج.م" or "<span>5400</span> ج.م"."""
    match = re.search(r"([\d.,]+)\s*ج\.م", re.sub(r"<[^>]+>", " ", cell))
    return number(match.group(1)) if match else None


def _rows(table: str) -> dict[str, tuple]:
    rows = {}
    for row in re.finditer(r"<tr[^>]*>(.*?)</tr>", table, re.DOTALL | re.IGNORECASE):
        html = row.group(1)
        label = re.search(r"purity-cell.*?<span>([^<]+)</span>", html, re.DOTALL | re.IGNORECASE)
        if not label:
            continue
        prices = []
        for cell in re.finditer(r"<td([^>]*)>(.*?)</td>", html, re.DOTALL | re.IGNORECASE):
            attrs, content = cell.groups()
            if "gap-" in attrs or "ج.م" not in content or "$" in content:
                continue
            value = _egp(content)
            if value is not None:
                prices.append(value)
            if len(prices) == 2:
                break
        if len(prices) == 2:
            rows[label.group(1).strip()] = (prices[0], prices[1])
    return rows


# A div whose classes include the word: `class="desktop-table"`, `class="desktop-table mb-8 …"`.
_DESKTOP = r'<div class="(?:[^"]*\s)?desktop-table(?:\s[^"]*)?"[^>]*>'
_NEXT = r'<div class="(?:[^"]*\s)?(?:desktop-table|mobile-cards)(?:\s[^"]*)?"'


def _tables(html: str) -> list[str]:
    """Each price table: from its div to the next table or the mobile cards."""
    return re.findall(_DESKTOP + r"(.*?)(?=" + _NEXT + ")", html, re.DOTALL | re.IGNORECASE) \
        or re.findall(_DESKTOP + r"(.*)", html, re.DOTALL | re.IGNORECASE)


def parse(html: str, silver_html: str = "") -> Quote:
    tables = _tables(html)
    if not tables:
        raise FeedError("iSagha: no price table on the page.")
    gold_rows = _rows(tables[0])
    quote = Quote()
    for karat, label in GOLD_LABELS.items():
        if label in gold_rows:
            quote.gold[karat] = gold_rows[label]
    for page in (html, silver_html):
        row = _silver_row(page)
        if row:
            quote.silver[999] = row
            break
    return quote.check(LABEL)


def _silver_row(page: str):
    """Silver 999's (sell, buy) in the page's price tables, or None."""
    for table in _tables(page or ""):
        row = _rows(table).get(SILVER_LABEL)
        if row:
            return row
    return None


def read() -> Quote:
    html = fetch(URL)
    # Only a silver row in the price tables counts: the gold page also names "فضة عيار 999"
    # in a ticker, which once stopped the silver page from being read at all.
    silver = "" if _silver_row(html) else fetch(SILVER_URL)
    return parse(html, silver)
