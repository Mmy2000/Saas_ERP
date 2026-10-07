"""What every market price source returns, and fetching a page politely (stdlib only)."""

from __future__ import annotations

import gzip
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0 Safari/537.36")
TIMEOUT_SECONDS = 15
MAX_BYTES = 3 * 1024 * 1024
GOLD_KARATS = (24, 22, 21, 18, 14)


class FeedError(Exception):
    """The source could not be read, or what it showed does not look like prices."""


@dataclass
class Quote:
    """Per-gram prices in Egyptian pounds as the source shows them: what the market sells at
    (`sell`) and buys at (`buy`), per gold karat and per silver fineness."""

    gold: dict[int, tuple[Decimal, Decimal]] = field(default_factory=dict)
    silver: dict[int, tuple[Decimal, Decimal]] = field(default_factory=dict)
    currency: str = "EGP"

    def as_json(self) -> dict:
        return {
            "gold": {str(k): {"sell": str(s), "buy": str(b)} for k, (s, b) in self.gold.items()},
            "silver": {str(k): {"sell": str(s), "buy": str(b)}
                       for k, (s, b) in self.silver.items()},
        }

    def check(self, source: str) -> Quote:
        """Refuse what cannot be right: no 21K, a buy above its sell, karats out of order, or a
        21K price far outside any plausible range."""
        if 21 not in self.gold:
            raise FeedError(f"{source}: no 21K price on the page.")
        for karat, (sell, buy) in self.gold.items():
            if sell <= 0 or buy <= 0 or buy > sell:
                raise FeedError(f"{source}: {karat}K prices look wrong ({sell}/{buy}).")
        sells = [self.gold[k][0] for k in GOLD_KARATS if k in self.gold]
        if sells != sorted(sells, reverse=True):
            raise FeedError(f"{source}: karat prices are out of order.")
        if not Decimal(500) <= self.gold[21][0] <= Decimal(100000):
            raise FeedError(f"{source}: 21K at {self.gold[21][0]} is not a plausible price.")
        return self


def number(text: str) -> Decimal | None:
    try:
        return Decimal(text.replace(",", "").strip())
    except (InvalidOperation, AttributeError):
        return None


def fetch(url: str, *, data: bytes | None = None, headers: dict | None = None) -> str:
    """The page as text. Raises FeedError on any network or HTTP problem."""
    request = urllib.request.Request(url, data=data, headers={
        "User-Agent": USER_AGENT, "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
        "Accept-Encoding": "gzip", "Accept-Language": "ar,en;q=0.8", **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
            body = response.read(MAX_BYTES + 1)
            if response.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise FeedError(f"{url}: {exc}") from exc
    if len(body) > MAX_BYTES:
        raise FeedError(f"{url}: the page is too large.")
    return body.decode("utf-8", errors="replace")


def strip_tags(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def timed(fn):
    """(result, milliseconds) of calling fn."""
    started = time.monotonic()
    result = fn()
    return result, int((time.monotonic() - started) * 1000)
