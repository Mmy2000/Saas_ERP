"""The drag-and-drop document designer: a design is a list of blocks (JSON), not code.

Each block has a type, its own properties and a common style (spacing on four sides, padding,
background, border, radius, text). Everything coming from the browser goes through
`clean_page` / `clean_blocks`: unknown types and keys are dropped, numbers are clamped, colours
must be #rrggbb and choices must be known. So clients can design safely: the server builds the
HTML and CSS itself, from validated values only.
"""

from __future__ import annotations

import re
import uuid

from django.template.loader import render_to_string
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _

MAX_BLOCKS = 80
MAX_TEXT = 2000
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


# ---- property kinds ------------------------------------------------------------------------

def _num(value, lo, hi, default):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    number = max(lo, min(hi, number))
    return int(number) if float(number).is_integer() else round(number, 2)


def _clean_value(spec, value):
    kind = spec["kind"]
    if kind == "bool":
        return bool(value) if value is not None else spec["default"]
    if kind == "num":
        return _num(value, spec["min"], spec["max"], spec["default"])
    if kind == "color":
        return value if isinstance(value, str) and HEX.match(value) else spec["default"]
    if kind == "choice":
        return value if value in dict(spec["choices"]) else spec["default"]
    if kind == "text":
        return str(value)[:spec.get("max", MAX_TEXT)] if value is not None else spec["default"]
    return spec["default"]


def num(label, default, lo, hi, unit=""):
    return {"kind": "num", "label": label, "default": default, "min": lo, "max": hi,
            "unit": unit}


def color(label, default=""):
    return {"kind": "color", "label": label, "default": default}


def choice(label, default, choices):
    return {"kind": "choice", "label": label, "default": default, "choices": choices}


def boolean(label, default):
    return {"kind": "bool", "label": label, "default": default}


def text(label, default="", multiline=False, max_length=MAX_TEXT):
    return {"kind": "text", "label": label, "default": default, "multiline": multiline,
            "max": max_length}


ALIGN = [("", _("Inherit")), ("start", _("Start")), ("center", _("Centre")), ("end", _("End"))]
WEIGHT = [("", _("Inherit")), ("400", _("Normal")), ("600", _("Semi-bold")), ("700", _("Bold"))]
LINE = [("solid", _("Solid")), ("dashed", _("Dashed")), ("dotted", _("Dotted")),
        ("double", _("Double"))]

# The style every block has. Spacing in millimetres, radius and borders in pixels.
STYLE = {
    "mt": num(_("Space above"), 0, 0, 60, "mm"), "mb": num(_("Space below"), 3, 0, 60, "mm"),
    "ml": num(_("Space left"), 0, 0, 60, "mm"), "mr": num(_("Space right"), 0, 0, 60, "mm"),
    "pt": num(_("Padding top"), 0, 0, 40, "mm"), "pb": num(_("Padding bottom"), 0, 0, 40, "mm"),
    "pl": num(_("Padding left"), 0, 0, 40, "mm"), "pr": num(_("Padding right"), 0, 0, 40, "mm"),
    "bg": color(_("Background")),
    "bw": num(_("Border width"), 0, 0, 6, "px"), "bc": color(_("Border colour"), "#e4e4e7"),
    "bs": choice(_("Border style"), "solid", LINE),
    "radius": num(_("Rounded corners"), 0, 0, 40, "px"),
    "align": choice(_("Alignment"), "", ALIGN),
    "size": num(_("Text size"), 0, 0, 48, "px"),  # 0 = inherit
    "weight": choice(_("Text weight"), "", WEIGHT),
    "color": color(_("Text colour")),
}
STYLE_GROUPS = [
    (_("Spacing"), ["mt", "mb", "ml", "mr"]),
    (_("Padding"), ["pt", "pb", "pl", "pr"]),
    (_("Box"), ["bg", "bw", "bc", "bs", "radius"]),
    (_("Text"), ["align", "size", "weight", "color"]),
]

PAGE = {
    "mt": num(_("Page margin top"), 12, 0, 40, "mm"),
    "mb": num(_("Page margin bottom"), 12, 0, 40, "mm"),
    "ml": num(_("Page margin left"), 12, 0, 40, "mm"),
    "mr": num(_("Page margin right"), 12, 0, 40, "mm"),
    "size": num(_("Base text size"), 12, 7, 20, "px"),
    "color": color(_("Text colour"), "#18181b"),
    "bg": color(_("Page background"), "#ffffff"),
    "accent": color(_("Accent colour")),  # empty = the design / workspace colour
}

TEXT_SOURCES = [("custom", _("My text")), ("header_note", _("Line under the header")),
                ("footer_note", _("Line at the bottom")), ("terms", _("Terms and conditions"))]

BLOCKS = {
    "company": {"label": _("Company"), "icon": "building-2", "props": {
        "logo": boolean(_("Logo"), True), "logo_size": num(_("Logo size"), 60, 16, 200, "px"),
        "logo_round": num(_("Logo corners"), 8, 0, 100, "px"),
        "name_size": num(_("Name size"), 20, 8, 48, "px"),
        "details": boolean(_("Address, phone and tax no."), True),
        "stack": boolean(_("Logo above the name"), False)}},
    "logo": {"label": _("Logo"), "icon": "gem", "props": {
        "size": num(_("Size"), 80, 16, 300, "px"),
        "round": num(_("Corners"), 8, 0, 150, "px")}},
    "title": {"label": _("Title"), "icon": "receipt-text", "props": {
        "text": text(_("Text (empty: the document's name)"), max_length=120),
        "number": boolean(_("Show the number"), True),
        "accent": boolean(_("In the accent colour"), True)}},
    "meta": {"label": _("Document details"), "icon": "list", "props": {
        "inline": boolean(_("On one line"), False),
        "labels": boolean(_("Show labels"), True)}},
    "party": {"label": _("Customer"), "icon": "user-round", "props": {
        "inline": boolean(_("On one line"), True)}},
    "items": {"label": _("Items table"), "icon": "layers", "props": {
        "variant": choice(_("Shape"), "table", [("table", _("Table")),
                                                  ("list", _("List (narrow paper)"))]),
        "index": boolean(_("Row numbers"), True),
        "head_bg": color(_("Header background"), "#18181b"),
        "head_color": color(_("Header text"), "#ffffff"),
        "stripes": boolean(_("Striped rows"), False),
        "stripe_bg": color(_("Stripe colour"), "#fafafa"),
        "lines": choice(_("Lines"), "rows", [("none", _("None")), ("rows", _("Between rows")),
                                             ("grid", _("Grid"))]),
        "line_color": color(_("Line colour"), "#e4e4e7"),
        "cell_pad": num(_("Cell padding"), 7, 0, 20, "px"),
        "font": num(_("Text size"), 12, 7, 20, "px")}},
    "sections": {"label": _("Extra details"), "icon": "coins", "props": {}},
    "totals": {"label": _("Totals"), "icon": "calculator", "props": {
        "width": num(_("Width"), 50, 20, 100, "%"),
        "strong_bg": color(_("Highlight background")),
        "strong_color": color(_("Highlight text")),
        "lines": boolean(_("Lines between rows"), True)}},
    "text": {"label": _("Text"), "icon": "pencil", "props": {
        "source": choice(_("Text"), "custom", TEXT_SOURCES),
        "text": text(_("My text"), multiline=True)}},
    "spacer": {"label": _("Space"), "icon": "chevron-down", "props": {
        "height": num(_("Height"), 8, 1, 120, "mm")}},
    "divider": {"label": _("Line"), "icon": "list-tree", "props": {
        "thickness": num(_("Thickness"), 1, 1, 8, "px"),
        "line_color": color(_("Colour"), "#18181b"),
        "style": choice(_("Style"), "solid", LINE)}},
    "signatures": {"label": _("Signatures"), "icon": "pencil", "props": {
        "gap": num(_("Space above the lines"), 14, 0, 60, "mm")}},
    "columns": {"label": _("Columns"), "icon": "layout-dashboard", "props": {
        "count": num(_("Columns"), 2, 2, 3),
        "gap": num(_("Gap"), 6, 0, 40, "mm"),
        "valign": choice(_("Vertical alignment"), "start",
                         [("start", _("Top")), ("center", _("Middle")), ("end", _("Bottom"))])}},
}


def _new_id() -> str:
    return "b" + uuid.uuid4().hex[:8]


def clean_page(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    return {key: _clean_value(spec, raw.get(key)) for key, spec in PAGE.items()}


def clean_blocks(raw, *, depth: int = 0, budget: list | None = None) -> list[dict]:
    """Validated blocks (see the module docstring). Columns hold one more level of blocks."""
    budget = budget if budget is not None else [MAX_BLOCKS]
    cleaned = []
    for item in raw if isinstance(raw, list) else []:
        if budget[0] <= 0 or not isinstance(item, dict) or item.get("type") not in BLOCKS:
            continue
        if item["type"] == "columns" and depth > 0:
            continue  # no columns inside columns
        budget[0] -= 1
        kind = BLOCKS[item["type"]]
        block_id = item.get("id") if re.fullmatch(r"b[0-9a-f]{8}", str(item.get("id"))) else \
            _new_id()
        props_in = item.get("props") if isinstance(item.get("props"), dict) else {}
        style_in = item.get("style") if isinstance(item.get("style"), dict) else {}
        block = {
            "id": block_id, "type": item["type"],
            "props": {k: _clean_value(s, props_in.get(k)) for k, s in kind["props"].items()},
            "style": {k: _clean_value(s, style_in.get(k)) for k, s in STYLE.items()},
        }
        if item["type"] == "columns":
            count = block["props"]["count"]
            children = item.get("children") if isinstance(item.get("children"), list) else []
            block["children"] = [clean_blocks(children[i] if i < len(children) else [],
                                              depth=depth + 1, budget=budget)
                                 for i in range(int(count))]
        cleaned.append(block)
    return cleaned


# ---- rendering -----------------------------------------------------------------------------

def _style_css(style: dict) -> str:
    css = [f"margin:{style['mt']}mm {style['mr']}mm {style['mb']}mm {style['ml']}mm",
           f"padding:{style['pt']}mm {style['pr']}mm {style['pb']}mm {style['pl']}mm"]
    if style["bg"]:
        css.append(f"background:{style['bg']}")
    if style["bw"]:
        css.append(f"border:{style['bw']}px {style['bs']} {style['bc']}")
    if style["radius"]:
        css.append(f"border-radius:{style['radius']}px;overflow:hidden")
    if style["align"]:
        css.append(f"text-align:{style['align']}")
    if style["size"]:
        css.append(f"font-size:{style['size']}px")
    if style["weight"]:
        css.append(f"font-weight:{style['weight']}")
    if style["color"]:
        css.append(f"color:{style['color']}")
    return ";".join(css)


def _is_empty(block: dict, context: dict) -> bool:
    p = block["props"]
    if block["type"] == "text":
        value = p["text"] if p["source"] == "custom" else context["design"].get(p["source"], "")
        return not str(value).strip()
    if block["type"] == "sections":
        return not context["doc"].sections
    if block["type"] == "items":
        return not context["doc"].columns  # receipts and vouchers have no items
    if block["type"] in ("logo",):
        return not context["doc"].company.get("logo")
    return False


def render_blocks(blocks: list[dict], context: dict, *, designer: bool = False) -> str:
    """HTML for already-cleaned blocks. Values only ever come from clean_* above."""
    parts = []
    for block in blocks:
        empty = _is_empty(block, context)
        if empty and not designer:
            continue  # e.g. a "terms" text when there are no terms
        inner = ""
        if block["type"] == "columns":
            columns = "".join(
                format_html('<div class="bk-col" data-column="{}">{}</div>', i,
                            mark_safe(render_blocks(child, context, designer=designer)))  # noqa: S308
                for i, child in enumerate(block["children"]))
            p = block["props"]
            inner = format_html(
                '<div class="bk-cols" style="grid-template-columns:repeat({},minmax(0,1fr));'
                'gap:{}mm;align-items:{}">{}</div>', int(p["count"]), p["gap"], p["valign"],
                mark_safe(columns))  # noqa: S308
        else:
            inner = render_to_string(f"printing/documents/blocks/{block['type']}.html",
                                     {**context, "p": block["props"]})
        parts.append(format_html(
            '<div class="bk bk-{}{}"{} style="{}">{}</div>', block["type"],
            " bk-empty" if empty else "",
            format_html(' data-block="{}"', block["id"]) if designer else "",
            _style_css(block["style"]), mark_safe(inner)))  # noqa: S308
    return "".join(parts)


def schema() -> dict:
    """What the editor needs: block types, their properties, the style and page fields."""
    def fields(specs):
        out = {}
        for key, spec in specs.items():
            field = {k: (str(v) if k == "label" else v) for k, v in spec.items()
                     if k != "choices"}
            if "choices" in spec:
                field["choices"] = [[value, str(label)] for value, label in spec["choices"]]
            out[key] = field
        return out

    return {
        "blocks": {key: {"label": str(b["label"]), "icon": b["icon"],
                         "props": fields(b["props"])} for key, b in BLOCKS.items()},
        "style": fields(STYLE),
        "style_groups": [[str(title), keys] for title, keys in STYLE_GROUPS],
        "page": fields(PAGE),
    }


def block(kind: str, children=None, **values) -> dict:
    """A block for presets: props and style values in one keyword list."""
    props = {k: v for k, v in values.items() if k in BLOCKS[kind]["props"]}
    style = {k: v for k, v in values.items() if k in STYLE}
    item = {"type": kind, "props": props, "style": style}
    if children is not None:
        item["children"] = children
    return item


PRESETS = {
    "classic": [
        block("columns", [[block("company")],
                          [block("title", align="end", mb=1, number=False),
                           block("meta", align="end")]]),
        block("divider", thickness=2, mb=3),
        block("text", source="header_note", bg="#fafafa", radius=6, pt=2, pb=2, pl=3, pr=3),
        block("party", bw=1, radius=6, pt=2, pb=2, pl=3, pr=3, mb=4),
        block("items"),
        block("sections", mt=3),
        block("columns", [[block("text", source="terms", size=11, color="#71717a")],
                          [block("totals", width=100)]], mt=4),
        block("signatures"),
        block("text", source="footer_note", align="center", size=11, color="#71717a", mt=6),
    ],
    "modern": [
        block("columns", [[block("company", name_size=20, details=False)],
                          [block("title", align="end", accent=False, color="#ffffff",
                                 size=22)]],
              bg="#9a6122", color="#ffffff", pt=5, pb=5, pl=6, pr=6, radius=12, mb=5),
        block("columns", [[block("party", inline=False, bg="#fafafa", radius=10, pt=3, pb=3,
                                 pl=3, pr=3, bw=1)],
                          [block("meta", bg="#fafafa", radius=10, pt=3, pb=3, pl=3, pr=3,
                                 bw=1)]], mb=5),
        block("items", head_bg="#ffffff", head_color="#71717a", stripes=True, radius=10, bw=1),
        block("sections", mt=3),
        block("columns", [[block("text", source="terms", size=11, color="#71717a")],
                          [block("totals", width=100, strong_bg="#9a6122",
                                 strong_color="#ffffff", lines=False)]], mt=5),
        block("text", source="footer_note", align="center", size=11, color="#71717a", mt=8),
    ],
    "thermal": [
        block("company", stack=True, logo_size=48, name_size=15, align="center"),
        block("text", source="header_note", align="center", size=11),
        block("title", align="center", accent=False, size=13, weight="700", mt=1,
              number=False),
        block("divider", style="dashed", mt=1, mb=1),
        block("meta"),
        block("party", inline=False),
        block("divider", style="dashed", mt=1, mb=1),
        block("items", variant="list"),
        block("sections"),
        block("divider", style="dashed", mt=1, mb=1),
        block("totals", width=100, lines=False),
        block("text", source="terms", size=10),
        block("text", source="footer_note", align="center", size=11, mt=2),
    ],
}


def preset(name: str) -> list[dict]:
    return clean_blocks(PRESETS.get(name) or PRESETS["classic"])
