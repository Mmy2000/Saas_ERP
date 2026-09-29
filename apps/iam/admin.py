from django.contrib import admin

from .models import User


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    """Platform-host admin for global identities. Memberships are tenant data and are managed
    from inside each tenant, not here."""

    list_display = ("email", "display_name", "is_active", "is_platform_staff", "last_login")
    search_fields = ("email", "phone", "display_name")
    list_filter = ("is_active", "is_platform_staff")
    exclude = ("password", "groups", "user_permissions")
    readonly_fields = ("last_login", "date_joined")
