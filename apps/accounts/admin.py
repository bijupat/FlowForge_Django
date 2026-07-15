"""
Django admin configuration for accounts app.
Registers custom User and OTP models.
"""
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.translation import gettext_lazy as _
from .models import User, OTP


class UserAdmin(BaseUserAdmin):
    """Custom admin configuration for User model."""
    model = User
    list_display = ('email', 'name', 'is_active', 'is_staff', 'date_joined')
    list_filter = ('is_active', 'is_staff', 'is_superuser', 'date_joined')
    search_fields = ('email', 'name')
    ordering = ('email',)
    readonly_fields = ('date_joined', 'last_login')

    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        (_('Personal info'), {'fields': ('name',)}),
        (_('Permissions'), {'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
        (_('Important dates'), {'fields': ('last_login', 'date_joined')}),
    )

    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'name', 'password1', 'password2'),
        }),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related('groups', 'user_permissions')


class OTPAdmin(admin.ModelAdmin):
    """Admin configuration for OTP model."""
    list_display = ('user', 'code', 'created_at', 'is_used')
    list_filter = ('is_used', 'created_at')
    search_fields = ('user__email', 'code')
    readonly_fields = ('user', 'code', 'created_at')
    ordering = ('-created_at',)

    def has_add_permission(self, request):
        return False  # OTPs should only be created programmatically

    def has_change_permission(self, request, obj=None):
        return False  # OTPs should not be changed manually


admin.site.register(User, UserAdmin)
admin.site.register(OTP, OTPAdmin)
