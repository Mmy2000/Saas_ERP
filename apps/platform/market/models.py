"""Market gold prices, read once for the whole platform (no client data here).

The worker polls every enabled source every few minutes. Each source keeps its health (last
success, last error, failures in a row, how long it took); a quote is stored only when its
prices change, so the history stays small and every row is a real price move.
"""

from django.db import models


class MarketSource(models.Model):
    key = models.CharField(max_length=30, unique=True)  # apps.platform.market.sources.SOURCES
    is_enabled = models.BooleanField(default=True)
    priority = models.PositiveSmallIntegerField(default=10)  # lower = preferred
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_ok_at = models.DateTimeField(null=True, blank=True)
    last_error_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    failures = models.PositiveIntegerField(default=0)  # in a row
    last_latency_ms = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["priority", "key"]

    def __str__(self):
        return self.key


class MarketQuote(models.Model):
    source = models.ForeignKey(MarketSource, on_delete=models.CASCADE, related_name="quotes")
    fetched_at = models.DateTimeField()
    currency = models.CharField(max_length=3, default="EGP")
    prices = models.JSONField()  # Quote.as_json(): {"gold": {"21": {"sell", "buy"}…}, "silver": …}
    gold21_sell = models.DecimalField(max_digits=12, decimal_places=2)
    gold21_buy = models.DecimalField(max_digits=12, decimal_places=2)
    digest = models.CharField(max_length=64)

    class Meta:
        indexes = [models.Index(fields=["source", "-fetched_at"], name="market_quote_latest_idx"),
                   models.Index(fields=["-fetched_at"], name="market_quote_time_idx")]

    def __str__(self):
        return f"{self.source_id} 21K {self.gold21_sell}/{self.gold21_buy} @ {self.fetched_at}"
