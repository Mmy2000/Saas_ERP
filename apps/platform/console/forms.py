from django import forms
from django.conf import settings
from django.contrib.auth.forms import AuthenticationForm
from django.utils.translation import gettext_lazy as _

from apps.core.appearance import ACCENT_CHOICES
from apps.core.media import ImageUploadInput, validate_image
from apps.platform.tenants.models import (
    RESERVED_SLUGS,
    Plan,
    PlatformLink,
    PlatformSettings,
    Tenant,
    TenantDomain,
    slug_validator,
)

LOGO_HELP = _("PNG, JPEG, WEBP, GIF or ICO, up to 2 MB. A square or wide image on a "
              "transparent background works best.")
CURRENCIES = [(c, c) for c in ("EGP", "SAR", "AED", "KWD", "QAR", "BHD", "OMR", "JOD", "USD",
                               "EUR", "GBP")]
COUNTRIES = [("EG", _("Egypt")), ("SA", _("Saudi Arabia")), ("AE", _("United Arab Emirates")),
             ("KW", _("Kuwait")), ("QA", _("Qatar")), ("BH", _("Bahrain")), ("OM", _("Oman")),
             ("JO", _("Jordan")), ("GB", _("United Kingdom")), ("US", _("United States"))]
TIMEZONES = [(z, z) for z in ("Africa/Cairo", "Asia/Riyadh", "Asia/Dubai", "Asia/Kuwait",
                              "Asia/Qatar", "Asia/Bahrain", "Asia/Muscat", "Asia/Amman",
                              "Europe/London", "America/New_York")]
FINENESS = [("999.9", "999.9"), ("1000", "1000")]


class Styled:
    """Give every field the design system's input class (and numbers their LTR look)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            css = "form-input"
            if isinstance(field, forms.FileField):
                css = "form-file"
            elif isinstance(field, (forms.IntegerField, forms.DateField)):
                css += " num w-full text-start"
            field.widget.attrs.setdefault("class", css)


class StaffLoginForm(Styled, AuthenticationForm):
    """Email + password; only platform staff get in."""

    username = forms.EmailField(label=_("Email"), widget=forms.EmailInput(
        attrs={"autofocus": True, "autocomplete": "email"}))
    error_messages = {
        **AuthenticationForm.error_messages,
        "not_staff": _("This account cannot use the platform console."),
    }

    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if not user.is_platform_staff:
            raise forms.ValidationError(self.error_messages["not_staff"], code="not_staff")


class _Limits(Styled, forms.Form):
    plan = forms.ChoiceField(label=_("Plan"))
    only_active_plans = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        plans = Plan.objects.all()
        if self.only_active_plans:
            plans = plans.filter(is_active=True)
        current = (self.initial or {}).get("plan")
        self.fields["plan"].choices = [(p.code, p.label) for p in plans] + [
            (code, code) for code in [current] if code and not plans.filter(code=code).exists()]
    trial_ends_on = forms.DateField(label=_("Trial ends on"), required=False,
                                    widget=forms.DateInput(attrs={"type": "date"}))
    max_branches = forms.IntegerField(label=_("Most branches"), min_value=1, required=False)
    max_users = forms.IntegerField(label=_("Most users"), min_value=1, required=False)
    contact_name = forms.CharField(label=_("Contact person"), max_length=200, required=False)
    contact_email = forms.EmailField(label=_("Contact email"), required=False)
    contact_phone = forms.CharField(label=_("Contact phone"), max_length=30, required=False)
    notes = forms.CharField(label=_("Notes"), required=False, widget=forms.Textarea(
        attrs={"rows": 3}))


class TenantCreateForm(_Limits):
    only_active_plans = True
    name = forms.CharField(label=_("Company name"), max_length=200)
    slug = forms.CharField(label=_("Address"), max_length=63, validators=[slug_validator],
                           help_text=_("Lowercase letters, digits and hyphens."),
                           widget=forms.TextInput(attrs={
                               "class": "form-input num w-full rounded-e-none text-start",
                               "autocomplete": "off", "dir": "ltr"}))
    locale = forms.ChoiceField(label=_("Language"), choices=settings.LANGUAGES, initial="ar")
    country = forms.ChoiceField(label=_("Country"), choices=COUNTRIES, initial="EG")
    functional_currency = forms.ChoiceField(label=_("Currency"), choices=CURRENCIES,
                                            initial="EGP")
    timezone = forms.ChoiceField(label=_("Time zone"), choices=TIMEZONES,
                                 initial="Africa/Cairo")
    fineness_24k = forms.ChoiceField(label=_("24K fineness"), choices=FINENESS,
                                     initial="999.9")
    owner_display_name = forms.CharField(label=_("Owner's name"), max_length=150)
    owner_username = forms.SlugField(label=_("Owner's username"), max_length=150,
                                     initial="admin")
    owner_email = forms.EmailField(label=_("Owner's email"))
    owner_password = forms.CharField(
        label=_("Owner's password"), required=False, strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text=_("Leave empty when the owner already has an account (same email)."))

    field_order = ["name", "slug", "locale", "country", "functional_currency", "timezone",
                   "fineness_24k", "owner_display_name", "owner_username", "owner_email",
                   "owner_password", "plan", "trial_ends_on", "max_branches", "max_users",
                   "contact_name", "contact_email", "contact_phone", "notes"]

    def clean_slug(self):
        slug = self.cleaned_data["slug"].strip().lower()
        if slug in RESERVED_SLUGS:
            raise forms.ValidationError(_("This address is reserved."))
        if Tenant.objects.filter(slug=slug).exists():
            raise forms.ValidationError(_("This address is taken."))
        if TenantDomain.objects.filter(domain=domain_for(slug)).exists():
            raise forms.ValidationError(_("This address is taken."))
        return slug

    def clean(self):
        data = super().clean()
        from apps.iam.models import User

        email = data.get("owner_email")
        exists = email and User.objects.filter(email__iexact=email).exists()
        if email and not exists and not data.get("owner_password"):
            self.add_error("owner_password", _("A new owner needs a password."))
        if data.get("owner_password") and not exists:
            from django.contrib.auth.password_validation import validate_password

            try:
                validate_password(data["owner_password"])
            except forms.ValidationError as exc:
                self.add_error("owner_password", exc)
        return data


class TenantSettingsForm(_Limits):
    name = forms.CharField(label=_("Company name"), max_length=200)
    field_order = ["name", "plan", "trial_ends_on", "max_branches", "max_users",
                   "contact_name", "contact_email", "contact_phone", "notes"]


class ProfileForm(Styled, forms.Form):
    """Settings stored inside the client's own data (TenantProfile)."""

    display_name = forms.CharField(label=_("Name on screens and receipts"), max_length=200)
    locale = forms.ChoiceField(label=_("Default language"), choices=settings.LANGUAGES)
    country = forms.ChoiceField(label=_("Country"), choices=COUNTRIES)
    timezone = forms.ChoiceField(label=_("Time zone"), choices=TIMEZONES)
    accent = forms.ChoiceField(
        label=_("Accent colour"), choices=ACCENT_CHOICES,
        help_text=_("Users can still pick their own in the appearance menu."))
    logo = forms.FileField(label=_("Logo"), required=False, validators=[validate_image],
                           help_text=LOGO_HELP, widget=ImageUploadInput)


class DomainForm(Styled, forms.Form):
    domain = forms.CharField(label=_("Domain"), max_length=253,
                             help_text=_("e.g. erp.example.com, pointed at this server"))

    def clean_domain(self):
        domain = self.cleaned_data["domain"].strip().lower().rstrip(".")
        if "." not in domain or " " in domain or "/" in domain:
            raise forms.ValidationError(_("Enter a domain like erp.example.com."))
        if TenantDomain.objects.filter(domain=domain).exists():
            raise forms.ValidationError(_("This domain is already in use."))
        if domain in {h.lower() for h in settings.PLATFORM_HOSTS}:
            raise forms.ValidationError(_("This domain is already in use."))
        return domain


def domain_for(slug: str) -> str:
    return f"{slug}.{settings.TENANT_BASE_DOMAIN}"


class TrafficLimitForm(Styled, forms.Form):
    requests_per_minute = forms.IntegerField(
        label=_("Requests per minute"), min_value=0, required=False,
        help_text=_("Empty uses the platform default. 0 means no limit."))


class PlatformSettingsForm(Styled, forms.ModelForm):
    class Meta:
        model = PlatformSettings
        fields = ["brand_name", "tagline", "footer_text", "logo", "logo_dark"]
        labels = {
            "brand_name": _("Platform name"),
            "tagline": _("Line under each client's name"),
            "footer_text": _("Footer text"),
            "logo": _("Logo"),
            "logo_dark": _("Logo for dark backgrounds"),
        }
        help_texts = {
            "tagline": _("Shown in every workspace sidebar. Empty: the platform name."),
            "footer_text": _("e.g. © 2026 Your Company. All rights reserved."),
            "logo": LOGO_HELP,
            "logo_dark": _("Used on the dark sidebar, the sign-in panel and in dark mode. "
                           "Empty: the logo above."),
        }
        widgets = {"logo": ImageUploadInput, "logo_dark": ImageUploadInput}


class PlatformLinkForm(Styled, forms.ModelForm):
    url = forms.URLField(label=_("Link"), max_length=500, assume_scheme="https",
                         widget=forms.URLInput(attrs={"dir": "ltr", "placeholder": "https://"}))

    class Meta:
        model = PlatformLink
        fields = ["label", "url", "position"]
        labels = {"label": _("Label"), "url": _("Link"), "position": _("Order")}


PlatformLinkFormSet = forms.modelformset_factory(
    PlatformLink, form=PlatformLinkForm, extra=2, can_delete=True)


class PlanForm(Styled, forms.ModelForm):
    """A plan, with its features as checkboxes (saved as PlanFeature rows)."""

    class Meta:
        model = Plan
        fields = ["code", "name", "name_ar", "description", "price", "currency",
                  "billing_period", "max_branches", "max_users", "requests_per_minute",
                  "trial_days", "position", "is_active", "is_default"]
        labels = {
            "code": _("Code"), "name": _("Name (English)"), "name_ar": _("Name (Arabic)"),
            "description": _("Description"), "price": _("Price"), "currency": _("Currency"),
            "billing_period": _("Billed"), "max_branches": _("Most branches"),
            "max_users": _("Most users"), "requests_per_minute": _("Requests per minute"),
            "trial_days": _("Trial days"), "position": _("Order"),
            "is_active": _("Offered for new clients"), "is_default": _("Default for new clients"),
        }
        help_texts = {
            "code": _("Lowercase letters, digits and hyphens. Cannot change later."),
            "max_branches": _("Empty: no limit. A client's own limit wins."),
            "max_users": _("Empty: no limit. A client's own limit wins."),
            "requests_per_minute": _("Empty: the platform default. 0: no limit."),
            "trial_days": _("New clients on this plan get a trial ending this many days later."),
        }
        widgets = {"description": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.platform.tenants import features as registry

        self.fields["currency"] = forms.ChoiceField(label=_("Currency"), choices=CURRENCIES,
                                                    initial="EGP")
        if self.instance.pk:
            self.fields["code"].disabled = True
        saved = ({row.key: row.enabled for row in self.instance.feature_rows.all()}
                 if self.instance.pk else {})
        self.feature_fields = []
        for feature in registry.FEATURES:
            name = f"feature_{feature.key}"
            self.fields[name] = forms.BooleanField(
                label=feature.label, help_text=feature.description, required=False,
                initial=saved.get(feature.key, feature.default))
            self.feature_fields.append((feature, name))
        for field in self.fields.values():
            if isinstance(field, forms.BooleanField):
                field.widget.attrs["class"] = "size-4 rounded border-slate-300 accent-brand-600"
            elif isinstance(field, forms.DecimalField):
                field.widget.attrs["class"] = "form-input num w-full text-start"

    def clean(self):
        data = super().clean()
        if data.get("is_default") and not data.get("is_active"):
            self.add_error("is_active", _("The default plan must be offered for new clients."))
        return data

    def feature_rows(self):
        return [(feature, self[name]) for feature, name in self.feature_fields]

    def save_features(self, plan) -> None:
        from apps.platform.tenants.models import PlanFeature

        for feature, name in self.feature_fields:
            PlanFeature.objects.update_or_create(
                plan=plan, key=feature.key, defaults={"enabled": self.cleaned_data[name]})


class PlanDeleteForm(Styled, forms.Form):
    move_to = forms.ChoiceField(label=_("Move its clients to"))

    def __init__(self, *args, plan, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["move_to"].choices = [(p.code, p.label)
                                          for p in Plan.objects.exclude(pk=plan.pk)]
