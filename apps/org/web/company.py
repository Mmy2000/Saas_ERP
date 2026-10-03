"""Company settings inside the workspace: the client's own name on screens, logo and colour."""

from __future__ import annotations

from django import forms
from django.contrib import messages
from django.shortcuts import redirect, render
from django.utils.translation import gettext_lazy as _

from apps.core.appearance import ACCENT_CHOICES
from apps.core.media import ImageUploadInput, replace_file, validate_image
from apps.iam.authz import permission_required
from apps.org.models import TenantProfile


class CompanyForm(forms.Form):
    display_name = forms.CharField(label=_("Name on screens and receipts"), max_length=200,
                                   widget=forms.TextInput(attrs={"class": "form-input"}))
    accent = forms.ChoiceField(
        label=_("Accent colour"), choices=ACCENT_CHOICES,
        widget=forms.Select(attrs={"class": "form-input"}),
        help_text=_("Users can still pick their own in the appearance menu."))
    logo = forms.FileField(
        label=_("Logo"), required=False, validators=[validate_image],
        widget=ImageUploadInput(attrs={"class": "form-file"}),
        help_text=_("PNG, JPEG, WEBP, GIF or ICO, up to 2 MB. A square or wide image on a "
                    "transparent background works best."))


@permission_required("org.settings.manage")
def company(request):
    profile = TenantProfile.objects.get()
    form = CompanyForm(request.POST or None, request.FILES or None, initial={
        "display_name": profile.display_name, "accent": profile.accent,
        "logo": profile.logo or None})
    if request.method == "POST" and form.is_valid():
        profile.display_name = form.cleaned_data["display_name"]
        profile.accent = form.cleaned_data["accent"]
        replace_file(profile, "logo", form.cleaned_data["logo"])
        profile.save()
        messages.success(request, _("Saved."))
        return redirect("company")
    return render(request, "org/company.html", {
        "form": form, "profile": profile,
        "logo_url": profile.logo.url if profile.logo else ""})
