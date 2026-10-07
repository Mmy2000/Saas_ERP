"""iSagha (market.isagha.com): gold 24/22/21/18 and silver 999, sell and buy per gram.
Parsing follows the legacy Gweb reader (Cod/gold_sync/isagha)."""

from __future__ import annotations

import re

from .base import FeedError, Quote, fetch, number

LABEL = "iSagha"
URL = "https://market.isagha.com/prices/egypt"
SILVER_URL = "https://market.isagha.com/prices/silver"
GOLD_LABELS = {24: "عيار 24", 22: "عيار 22", 21: "عيار 21", 18: "عيار 18"}
SILVER_LABEL = "عيار 999"


def _egp(cell: str):
    match = re.search(r"([\d.,]+)\s*ج\.م", cell)
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


def _tables(html: str) -> list[str]:
    return re.findall(r'<div class="desktop-table"[^>]*>(.*?)</div>\s*(?=<div class="(?:'
                      r'desktop-table|mobile-cards)")', html, re.DOTALL | re.IGNORECASE) or \
        re.findall(r'<div class="desktop-table"[^>]*>(.*)', html, re.DOTALL | re.IGNORECASE)


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
        for table in _tables(page or ""):
            row = _rows(table).get(SILVER_LABEL)
            if row:
                quote.silver[999] = row
                break
        if quote.silver:
            break
    return quote.check(LABEL)


def read() -> Quote:
    html = fetch(URL)
    silver = "" if SILVER_LABEL in html else fetch(SILVER_URL)
    return parse(html, silver)
