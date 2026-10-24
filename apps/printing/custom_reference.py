"""What a custom design (console) can use: the editor's "Insert" menu and its completions.

Each group is {"title", "items": [{"label", "insert", "hint"}]}: `insert` is the template text
put at the cursor, `label` what the menu shows, `hint` a short note (may be empty).
"""

from __future__ import annotations

from django.utils.translation import gettext as _

from .documents import COMMON_LABELS, TYPES

ITEMS_TABLE = """<table class="items">
  <thead>
    <tr>
      {% for column in doc.columns %}
      <th style="text-align:{{ column.align }}">{{ column.label }}</th>
      {% endfor %}
    </tr>
  </thead>
  <tbody>
    {% for row in doc.rows %}
    <tr>
      {% for cell in row.cells %}
      <td style="text-align:{{ cell.align }}">{{ cell.value }}</td>
      {% endfor %}
    </tr>
    {% endfor %}
  </tbody>
</table>"""

TOTALS = """{% for total in doc.totals %}
<div class="total{% if total.strong %} strong{% endif %}">
  <span>{{ total.label }}</span> <span class="num">{{ total.value }}</span>
</div>
{% endfor %}"""

SECTIONS = """{% for section in doc.sections %}
<h3>{{ section.title }}</h3>
{% for label, value in section.lines %}
<div><span>{{ label }}</span> <span class="num">{{ value }}</span></div>
{% endfor %}
{% endfor %}"""

SIGNATURES = """{% if design.signatures %}
<div style="display:flex;gap:24mm;margin-top:16mm">
  <span style="flex:1;border-top:1px solid #000;text-align:center">{{ doc.signatures.0 }}</span>
  <span style="flex:1;border-top:1px solid #000;text-align:center">{{ doc.signatures.1 }}</span>
</div>
{% endif %}"""


def _var(name: str, label, hint="") -> dict:
    return {"label": label, "insert": "{{ " + name + " }}", "code": name, "hint": str(hint)}


def _block(label, insert: str, hint="") -> dict:
    return {"label": label, "insert": insert, "code": "", "hint": str(hint)}


def _pairs(name: str) -> str:
    return ("{% for label, value in " + name + " %}\n"
            "<div><span>{{ label }}</span> <strong>{{ value }}</strong></div>\n{% endfor %}")


def reference(doc_type: str) -> list[dict]:
    kind = TYPES[doc_type]
    in_rows = _("Only inside the loop over doc.rows")
    groups = [
        (_("Document"), [
            _var("doc.title", _("Title")), _var("doc.number", _("Number")),
            _var("doc.date", _("Date")), _var("doc.status", _("Stamp (e.g. Cancelled)")),
            _var("doc.currency", _("Currency")), _var("doc.dir", _("Direction (rtl / ltr)")),
        ]),
        (_("Company"), [
            _block(_("Logo"), '{% if design.show_logo %}<img src="{{ doc.company.logo }}" alt="" '
                              'style="height:56px">{% endif %}'),
            _var("doc.company.name", _("Name")), _var("doc.company.legal_name", _("Legal name")),
            _var("doc.company.phone", _("Phone")), _var("doc.company.whatsapp", _("WhatsApp")),
            _var("doc.company.address", _("Address")), _var("doc.company.tax_no", _("Tax no.")),
            _var("doc.company.branch", _("Branch")),
        ]),
        (_("Blocks"), [
            _block(_("Details (number, date, branch…)"), _pairs("doc.meta")),
            _block(_("Customer"), _pairs("doc.party")),
            _block(_("Items table"), ITEMS_TABLE, _("Columns follow the options below")),
            _block(_("Extra sections (scrap received…)"), SECTIONS),
            _block(_("Totals"), TOTALS),
            _block(_("Signature lines"), SIGNATURES),
        ]),
        (_("Each item"), [_var(f"row.{column.key}", column.label, in_rows)
                          for column in kind.columns]),
        (_("Design settings"), [
            _var("design.color", _("Accent colour"), _("Or var(--accent) in CSS")),
            _var("design.header_note", _("Header note")),
            _var("design.footer_note", _("Footer note")),
            _var("design.terms", _("Terms")),
            _var("page.margin", _("Page margin")),
        ]),
        (_("Fixed words"), [_var(f"doc.labels.{key}", label)
                            for key, label in COMMON_LABELS.items()]),
        (_("Logic"), [
            _block(_("If … end if"), "{% if doc.status %}\n\n{% endif %}"),
            _block(_("Repeat (for)"), "{% for row in doc.rows %}\n\n{% endfor %}"),
            _block(_("Number with 2 decimals"), "{{ value|num:2 }}",
                   _("Needs “load ui” at the top")),
            _block(_("Translated text"), '{% translate "Total" %}',
                   _("Needs “load i18n” at the top")),
        ]),
    ]
    return [{"title": str(title), "items": [{**item, "label": str(item["label"])}
                                             for item in items]}
            for title, items in groups]
