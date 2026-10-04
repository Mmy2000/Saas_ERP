"""Forms for document designs: the client's options, and the custom HTML (platform staff)."""

from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from .documents import TYPES
from .models import DocLayout, DocumentDesign, Paper

CHECKBOX = "size-4 rounded border-slate-300 accent-brand-600"
SWATCH = "h-9 w-16 cursor-pointer rounded-lg border border-slate-200 bg-surface p-1"
CODE = ("form-input num w-full text-start font-mono text-xs leading-5 whitespace-pre")


class DesignForm(forms.ModelForm):
    """Layout, paper, language, colour, notes, columns and options of one document type."""

    class Meta:
        model = DocumentDesign
        fields = ["follows", "layout", "paper", "lang", "color", "show_logo", "copies",
                  "header_note", "footer_note", "terms"]
        labels = {
            "follows": _("Design"), "layout": _("Layout"), "paper": _("Paper"),
            "lang": _("Language"),
            "color": _("Colour"), "show_logo": _("Show the logo"), "copies": _("Copies"),
            "header_note": _("Line under the header"), "footer_note": _("Line at the bottom"),
            "terms": _("Terms and conditions"),
        }
        help_texts = {
            "color": _("Empty: the workspace's accent colour."),
            "paper": _("The thermal layout always prints on an 80 mm roll."),
        }
        widgets = {"terms": forms.Textarea(attrs={"rows": 3}),
                    "color": forms.TextInput(attrs={"type": "color"})}

    def __init__(self, *args, doc_type: str, accent: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        self.doc_type = TYPES[doc_type]
        self.accent = accent  # the workspace colour: kept as "follow the workspace"
        design = self.instance
        if not design.color and accent:
            self.initial["color"] = accent
        self.fields["copies"].widget.attrs.update(min=1, max=5)
        self.fields["follows"] = forms.ChoiceField(
            label=_("Design"), required=False,
            help_text=_("Follow another document to print with its design (its layout, "
                        "colours and texts). Columns and details stay this document's own."),
            choices=[("", _("Its own design"))] + [
                (key, _("Same as: %(name)s") % {"name": kind.label})
                for key, kind in TYPES.items() if key != doc_type])
        self.fields["color"].required = False
        chosen = design.enabled_columns(doc_type)
        self.column_names = []
        for column in self.doc_type.columns:
            name = f"col_{column.key}"
            self.fields[name] = forms.BooleanField(
                label=column.label, required=False, initial=column.key in chosen,
                disabled=column.fixed)
            self.column_names.append(name)
        self.option_names = []
        for key, label, _default in self.doc_type.options:
            name = f"opt_{key}"
            self.fields[name] = forms.BooleanField(
                label=label, required=False, initial=design.option(doc_type, key))
            self.option_names.append(name)
        for name, field in self.fields.items():
            if isinstance(field, forms.BooleanField):
                field.widget.attrs["class"] = CHECKBOX
            elif name == "color":
                field.widget.attrs["class"] = SWATCH
            else:
                css = "form-input"
                if isinstance(field, forms.IntegerField):
                    css += " num w-full text-start"
                field.widget.attrs.setdefault("class", css)

    def clean(self):
        data = super().clean()
        follows = data.get("follows") or ""
        if follows:
            # No loops: the chosen document must not (in the end) follow this one.
            from .render import own_design

            seen, current = {self.doc_type.key}, follows
            while current:
                if current in seen:
                    self.add_error("follows", _("That document already uses this one's design."))
                    break
                seen.add(current)
                current = own_design(current).follows if current in TYPES else ""
        if data.get("layout") == DocLayout.THERMAL:
            data["paper"] = Paper.ROLL80
        elif data.get("paper") == Paper.ROLL80:
            data["layout"] = DocLayout.THERMAL
        data["copies"] = max(1, min(data.get("copies") or 1, 5))
        return data

    def column_fields(self):
        return [self[name] for name in self.column_names]

    def option_fields(self):
        return [self[name] for name in self.option_names]

    def apply(self) -> DocumentDesign:
        """The design with the submitted values, not saved (also used for previews)."""
        design = self.instance
        for name in self.Meta.fields:
            setattr(design, name, self.cleaned_data[name])
        color = (self.cleaned_data.get("color") or "").lower()
        design.color = "" if color == self.accent.lower() else color
        design.columns = [c.key for c in self.doc_type.columns
                          if c.fixed or self.cleaned_data.get(f"col_{c.key}")]
        design.options = {key: bool(self.cleaned_data.get(f"opt_{key}"))
                          for key, _label, _default in self.doc_type.options}
        return design


class CustomHtmlForm(forms.Form):
    use_custom = forms.BooleanField(label=_("Use this custom design"), required=False)
    custom_html = forms.CharField(
        label=_("HTML"), required=False,
        widget=forms.Textarea(attrs={"rows": 24, "spellcheck": "false", "dir": "ltr",
                                     "class": CODE}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["use_custom"].widget.attrs["class"] = CHECKBOX
