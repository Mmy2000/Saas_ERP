"""The market price sources, by key. Adding one: a module with LABEL, URL, parse() and read()
returning a Quote, listed here; the console then shows it and can switch it on."""

from . import edahab, goldbullion, isagha
from .base import FeedError, Quote

SOURCES = {
    "isagha": isagha,
    "goldbullion": goldbullion,
    "edahab": edahab,
}

__all__ = ["SOURCES", "FeedError", "Quote"]
