from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRS = ("apps", "config", "templates", "static", "assets", "ops", "tests", "locale")
SUFFIXES = {".py", ".html", ".css", ".js", ".mjs", ".json", ".po", ".sql", ".toml"}


def test_no_byte_order_marks():
    """A UTF-8 BOM before <!doctype> puts browsers in quirks mode (it happened: PowerShell
    5.1's `Set-Content -Encoding utf8` writes one). Keep every source file BOM-free."""
    offenders = [
        str(path.relative_to(ROOT))
        for base in SOURCE_DIRS
        for path in (ROOT / base).rglob("*")
        if path.suffix in SUFFIXES and b"\xef\xbb\xbf" in path.read_bytes()
    ]
    assert offenders == []
