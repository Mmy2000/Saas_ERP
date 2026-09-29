"""Translation catalogue tooling that needs no GNU gettext (not installed on Windows dev boxes).

    python ops/i18n/messages.py extract   # add new strings to locale/ar/.../django.po
    python ops/i18n/messages.py compile   # write django.mo next to it
    python ops/i18n/messages.py check     # exit 1 if any string is missing or untranslated

Source strings are English; Arabic is the translation. The extractor understands the forms
this codebase uses: {% translate %}, {% blocktranslate [trimmed] … %}…{% plural %}…, and
_( ) / gettext( ) / gettext_lazy( ) in Python and template filter arguments.
"""

from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import polib

ROOT = Path(__file__).resolve().parents[2]
PO_PATH = ROOT / "locale" / "ar" / "LC_MESSAGES" / "django.po"
MO_PATH = PO_PATH.with_suffix(".mo")
ARABIC_PLURALS = ("nplurals=6; plural=n==0 ? 0 : n==1 ? 1 : n==2 ? 2 : "
                  "n%100>=3 && n%100<=10 ? 3 : n%100>=11 ? 4 : 5;")

_STRING = r"""("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')"""
PY_FUNCS = {"_", "gettext", "gettext_lazy"}
TPL_TRANSLATE = re.compile(r"{%\s*(?:translate|trans)\s+" + _STRING)
TPL_UNDERSCORE = re.compile(r"_\(" + _STRING + r"\)")
TPL_BLOCK = re.compile(
    r"{%\s*blocktrans(?:late)?\b(?P<args>[^%]*)%}(?P<body>.*?)"
    r"(?:{%\s*plural\s*%}(?P<plural>.*?))?{%\s*endblocktrans(?:late)?\s*%}",
    re.S,
)
TPL_VAR = re.compile(r"{{\s*(\w+)\s*}}")


@dataclass(frozen=True)
class Message:
    msgid: str
    plural: str = ""


def _literal(token: str) -> str:
    return ast.literal_eval(token)


def _block_text(text: str, trimmed: bool) -> str:
    # Django escapes literal "%" in blocktranslate bodies before substituting variables.
    text = TPL_VAR.sub(r"%(\1)s", text.replace("%", "%%"))
    return re.sub(r"\s*\n\s*", " ", text.strip()) if trimmed else text


def _from_template(source: str):
    # Template strings are looked up with "%" doubled (Django restores it after translating).
    for match in TPL_TRANSLATE.finditer(source):
        yield Message(_literal(match.group(1)).replace("%", "%%"))
    for match in TPL_UNDERSCORE.finditer(source):
        yield Message(_literal(match.group(1)).replace("%", "%%"))
    for match in TPL_BLOCK.finditer(source):
        trimmed = "trimmed" in match.group("args").split()
        plural = match.group("plural")
        yield Message(_block_text(match.group("body"), trimmed),
                      _block_text(plural, trimmed) if plural is not None else "")


def _from_python(source: str):
    # Parsed, not pattern-matched: messages split over adjacent literals ("a " "b") arrive
    # joined, exactly as Python sees them at runtime.
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in PY_FUNCS and node.args
                and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
            yield Message(node.args[0].value)


def _sources():
    for path in sorted(ROOT.glob("templates/**/*.html")) + sorted(ROOT.glob("apps/**/*.html")):
        yield path, _from_template
    for base in ("apps", "config"):
        for path in sorted((ROOT / base).rglob("*.py")):
            parts = set(path.parts)
            if "migrations" in parts or "tests" in parts:
                continue
            yield path, _from_python


def extract() -> dict[str, Message]:
    found: dict[str, Message] = {}
    for path, reader in _sources():
        for message in reader(path.read_text(encoding="utf-8")):
            if message.msgid:
                found.setdefault(message.msgid, message)
    return found


def _load() -> polib.POFile:
    if PO_PATH.exists():
        return polib.pofile(str(PO_PATH), wrapwidth=0)
    catalogue = polib.POFile(wrapwidth=0)
    catalogue.metadata = {
        "Project-Id-Version": "gweb-platform",
        "Language": "ar",
        "MIME-Version": "1.0",
        "Content-Type": "text/plain; charset=UTF-8",
        "Content-Transfer-Encoding": "8bit",
        "Plural-Forms": ARABIC_PLURALS,
    }
    return catalogue


def cmd_extract() -> int:
    found = extract()
    catalogue = _load()
    existing = {entry.msgid: entry for entry in catalogue}
    added = 0
    for msgid, message in found.items():
        if msgid in existing:
            continue
        if message.plural:
            entry = polib.POEntry(msgid=msgid, msgid_plural=message.plural,
                                  msgstr_plural={i: "" for i in range(6)})
        else:
            entry = polib.POEntry(msgid=msgid, msgstr="")
        catalogue.append(entry)
        added += 1
    stale = [entry for entry in catalogue if entry.msgid not in found]
    for entry in stale:
        catalogue.remove(entry)
    catalogue.sort(key=lambda entry: entry.msgid.lower())
    PO_PATH.parent.mkdir(parents=True, exist_ok=True)
    catalogue.save(str(PO_PATH))
    target = PO_PATH.relative_to(ROOT)
    print(f"{len(found)} strings; {added} added, {len(stale)} removed -> {target}")
    return 0


def cmd_compile() -> int:
    _load().save_as_mofile(str(MO_PATH))
    print(f"compiled -> {MO_PATH.relative_to(ROOT)}")
    return 0


def problems() -> list[str]:
    found = extract()
    catalogue = _load()
    entries = {entry.msgid: entry for entry in catalogue}
    issues = [f"missing: {msgid!r}" for msgid in found if msgid not in entries]
    for entry in catalogue:
        if entry.msgid not in found:
            issues.append(f"stale: {entry.msgid!r}")
        elif entry.msgid_plural:
            if not all(entry.msgstr_plural.values()):
                issues.append(f"untranslated plural: {entry.msgid!r}")
        elif not entry.msgstr or "fuzzy" in entry.flags:
            issues.append(f"untranslated: {entry.msgid!r}")
    return issues


def cmd_check() -> int:
    issues = problems()
    for issue in issues:
        print(issue)
    return 1 if issues else 0


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "check"
    sys.exit({"extract": cmd_extract, "compile": cmd_compile, "check": cmd_check}[command]())
