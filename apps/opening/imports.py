"""Opening stock from a spreadsheet: one row per barcoded piece (Excel .xlsx or .csv).

Columns are found by their header, in English or Arabic, in any order; only category, karat
(for gold) and weight are required. Categories and suppliers are matched by code or name, karats
by number ("21", "21K"). Every row is checked before anything is written, and the errors come
back by row and column, so the shop fixes the file and tries again.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass

from django.utils.translation import gettext as _

from apps.catalog.models import ItemCategory, Karat, Tracking

from .services import PieceBalance

MAX_ROWS = 5000

# column key → header names it may have (lower case; Arabic as in the legacy screens)
COLUMNS = {
    "barcode": ("barcode", "code", "الباركود", "الكود", "كود"),
    "category": ("category", "المجموعة", "الصنف", "مجموعة الاصناف"),
    "karat": ("karat", "العيار", "عيار"),
    "gross_weight_g": ("weight", "gross weight", "gross_weight_g", "الوزن", "الوزن القائم"),
    "stone_weight_ct": ("stones ct", "stone weight", "stone_weight_ct", "وزن الاحجار",
                        "وزن الأحجار"),
    "cost_amount": ("making cost", "cost", "cost_amount", "المصنعية", "تكلفة المصنعية"),
    "list_making_rate": ("making price per g", "list_making_rate", "مصنعية البيع"),
    "stone_cost_amount": ("stones cost", "stone_cost_amount", "تكلفة الاحجار",
                          "تكلفة الأحجار"),
    "label_price": ("label price", "label_price", "سعر التيكت", "سعر البيع"),
    "supplier": ("supplier", "المورد", "التاجر"),
    "external_code": ("model", "supplier code", "external_code", "الموديل", "كود المورد"),
}
REQUIRED = ("category", "gross_weight_g")
# What the template shows for each column (English / Arabic header).
TEMPLATE_HEADERS = [
    ("barcode", "Barcode", "الباركود"), ("category", "Category", "المجموعة"),
    ("karat", "Karat", "العيار"), ("gross_weight_g", "Weight", "الوزن"),
    ("stone_weight_ct", "Stones ct", "وزن الاحجار"), ("cost_amount", "Making cost", "المصنعية"),
    ("list_making_rate", "Making price per g", "مصنعية البيع"),
    ("stone_cost_amount", "Stones cost", "تكلفة الاحجار"),
    ("label_price", "Label price", "سعر التيكت"), ("supplier", "Supplier", "المورد"),
    ("external_code", "Model", "الموديل"),
]


class ImportError_(Exception):
    """The file itself cannot be read (not a spreadsheet, no header…)."""


@dataclass
class ParsedRow:
    line: int  # the row number in the file, as the shop sees it
    values: dict
    piece: PieceBalance | None
    errors: dict  # column key → message


def _normal(text) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _rows(upload) -> list[list[str]]:
    name = (getattr(upload, "name", "") or "").lower()
    data = upload.read()
    if name.endswith(".csv"):
        text = data.decode("utf-8-sig", errors="replace")
        return [[_cell(c) for c in row] for row in csv.reader(io.StringIO(text))]
    try:
        from openpyxl import load_workbook

        sheet = load_workbook(io.BytesIO(data), read_only=True, data_only=True).worksheets[0]
    except Exception as exc:  # noqa: BLE001 - any unreadable file gets the same answer
        raise ImportError_(_("This is not an Excel (.xlsx) or CSV file.")) from exc
    return [[_cell(c) for c in row] for row in sheet.iter_rows(values_only=True)]


def _header_map(header: list[str]) -> dict[str, int]:
    found = {}
    for index, title in enumerate(header):
        title = _normal(title.split("/")[0]) if "/" in title else _normal(title)
        for key, names in COLUMNS.items():
            if key not in found and (title in names or title == key):
                found[key] = index
    return found


def _lookup_tables():
    from apps.parties.models import PartyRoleType
    from apps.parties.selectors import parties_with_role

    categories = {}
    for category in ItemCategory.objects.filter(is_active=True, tracking=Tracking.SERIALIZED):
        categories[_normal(category.code)] = category
        categories.setdefault(_normal(category.name), category)
    karats = {}
    for karat in Karat.objects.filter(is_active=True).select_related("metal"):
        karats.setdefault(str(karat.code), karat)  # gold first when codes clash (21 vs 925)
        karats[f"{karat.code}k"] = karat
    suppliers = {}
    for party in parties_with_role(PartyRoleType.SUPPLIER):
        if party.code:
            suppliers[_normal(party.code)] = party
        suppliers.setdefault(_normal(party.name), party)
    return categories, karats, suppliers


def parse(upload) -> list[ParsedRow]:
    """The file's rows as pieces, each with its own errors. Raises ImportError_ for a file that
    cannot be read at all."""
    rows = _rows(upload)
    while rows and not any(rows[0]):
        rows.pop(0)
    if not rows:
        raise ImportError_(_("The file is empty."))
    columns = _header_map(rows[0])
    missing = [key for key in REQUIRED if key not in columns]
    if missing:
        names = ", ".join(dict((k, e) for k, e, _a in TEMPLATE_HEADERS)[key] for key in missing)
        raise ImportError_(_("The first row must name the columns. Missing: %(columns)s.")
                           % {"columns": names})
    body = [(number, row) for number, row in enumerate(rows[1:], start=2) if any(row)]
    if len(body) > MAX_ROWS:
        raise ImportError_(_("At most %(n)s rows in one file.") % {"n": MAX_ROWS})

    categories, karats, suppliers = _lookup_tables()
    parsed = []
    for number, row in body:
        values = {key: (row[index] if index < len(row) else "") for key, index in columns.items()}
        errors = {}
        category = categories.get(_normal(values.get("category")))
        if category is None:
            errors["category"] = _("Unknown category “%(value)s”.") % {"value":
                                                                       values.get("category")}
        karat_text = _normal(values.get("karat")).replace(" ", "")
        karat = karats.get(karat_text) if karat_text else None
        if karat_text and karat is None:
            errors["karat"] = _("Unknown karat “%(value)s”.") % {"value": values.get("karat")}
        supplier = None
        if values.get("supplier"):
            supplier = suppliers.get(_normal(values["supplier"]))
            if supplier is None:
                errors["supplier"] = _("Unknown supplier “%(value)s”.") % {
                    "value": values["supplier"]}
        piece = None
        if not errors:
            piece = PieceBalance(
                category_id=category.pk, karat_id=karat.pk if karat else None,
                gross_weight_g=values.get("gross_weight_g", ""),
                barcode=values.get("barcode", ""),
                stone_weight_ct=values.get("stone_weight_ct") or "0",
                cost_amount=values.get("cost_amount") or "0",
                list_making_rate=values.get("list_making_rate") or "0",
                stone_cost_amount=values.get("stone_cost_amount") or "0",
                label_price=values.get("label_price") or None,
                supplier_id=supplier.pk if supplier else None,
                external_code=values.get("external_code", ""))
        parsed.append(ParsedRow(number, values, piece, errors))
    return parsed


def template_workbook() -> bytes:
    """An empty sheet with the column headers, an example row, and the lists to pick from."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    from apps.parties.models import PartyRoleType
    from apps.parties.selectors import parties_with_role

    book = Workbook()
    sheet = book.active
    sheet.title = "Pieces"
    sheet.sheet_view.rightToLeft = False
    sheet.append([f"{english} / {arabic}" for _key, english, arabic in TEMPLATE_HEADERS])
    category = ItemCategory.objects.filter(is_active=True, tracking=Tracking.SERIALIZED).first()
    sheet.append(["1010000001", category.code if category else "RING", "21", "5.250", "0",
                  "150", "120", "0", "", "", ""])
    for cell in sheet[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="F4F4F5")
    for column in sheet.columns:
        sheet.column_dimensions[column[0].column_letter].width = 22

    lists = book.create_sheet("Lists")
    lists.append([_("Categories (code)"), _("Name"), "", _("Karats"), "", _("Suppliers")])
    for cell in lists[1]:
        cell.font = Font(bold=True)
    categories = list(ItemCategory.objects.filter(is_active=True, tracking=Tracking.SERIALIZED)
                      .order_by("code").values_list("code", "name"))
    karats = [str(k.code) for k in Karat.objects.filter(is_active=True).order_by("metal", "code")]
    suppliers = list(parties_with_role(PartyRoleType.SUPPLIER).order_by("name")
                     .values_list("name", flat=True))
    for index in range(max(len(categories), len(karats), len(suppliers))):
        code, name = categories[index] if index < len(categories) else ("", "")
        lists.append([code, name, "", karats[index] if index < len(karats) else "", "",
                      suppliers[index] if index < len(suppliers) else ""])
    for letter in "ABCDEF":
        lists.column_dimensions[letter].width = 24
    output = io.BytesIO()
    book.save(output)
    return output.getvalue()
