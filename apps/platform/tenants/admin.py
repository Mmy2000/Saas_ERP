from django.contrib import admin

from .models import Tenant, TenantDomain


class TenantDomainInline(admin.TabularInline):
    model = TenantDomain
    extra = 0


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ("slug", "name", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("slug", "name")
    inlines = [TenantDomainInline]

    def has_delete_permission(self, request, obj=None):
        return False  # tenants are archived then purged through tooling, never deleted here
