"""
Django admin configuration for Settings app.
Registers AllowedIP and ApplicationSetting models.
"""
from django.contrib import admin
from django.utils.translation import gettext_lazy as _
from .models import AllowedIP, ApplicationSetting


class AllowedIPAdmin(admin.ModelAdmin):
    """Admin configuration for AllowedIP."""
    list_display = ('ip_address', 'description', 'is_active', 'created_at')
    list_filter = ('is_active', 'created_at')
    search_fields = ('ip_address', 'description')
    ordering = ('ip_address',)
    actions = ['enable_ips', 'disable_ips']
    readonly_fields = ('created_at',)

    def enable_ips(self, request, queryset):
        queryset.update(is_active=True)
        self.message_user(request, _('Selected IPs have been enabled.'))
    enable_ips.short_description = _('Enable selected IPs')

    def disable_ips(self, request, queryset):
        queryset.update(is_active=False)
        self.message_user(request, _('Selected IPs have been disabled.'))
    disable_ips.short_description = _('Disable selected IPs')


class ApplicationSettingAdmin(admin.ModelAdmin):
    """Admin configuration for ApplicationSetting."""
    list_display = ('key', 'value', 'value_type', 'description')
    list_filter = ('value_type',)
    search_fields = ('key', 'description')
    ordering = ('key',)
    readonly_fields = ('id',)

    fieldsets = (
        (None, {
            'fields': ('key', 'value', 'value_type', 'description'),
        }),
    )

    def has_delete_permission(self, request, obj=None):
        # Optionally restrict deletion of critical settings
        if obj and obj.key in ['SESSION_TIMEOUT_MINUTES', 'OTP_EXPIRY_MINUTES', 'FILE_CLEANUP_HOURS']:
            return False
        return super().has_delete_permission(request, obj)


admin.site.register(AllowedIP, AllowedIPAdmin)
admin.site.register(ApplicationSetting, ApplicationSettingAdmin)
