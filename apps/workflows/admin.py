"""
Django admin configuration for workflows app.
Registers Workflow, WorkflowPermission, and WorkflowExecutionLog.
"""
from django.contrib import admin
from django.utils.translation import gettext_lazy as _
from .models import Workflow, WorkflowPermission, WorkflowExecutionLog


class WorkflowPermissionInline(admin.TabularInline):
    """Inline editor for workflow permissions."""
    model = WorkflowPermission
    extra = 1
    raw_id_fields = ('user',)
    verbose_name = _('Permission')
    verbose_name_plural = _('Permissions')


class WorkflowAdmin(admin.ModelAdmin):
    """Admin configuration for Workflow model."""
    list_display = ('name', 'slug', 'is_active', 'processor_module', 'created_at')
    list_filter = ('is_active', 'created_at')
    search_fields = ('name', 'slug', 'description')
    prepopulated_fields = {'slug': ('name',)}
    ordering = ('name',)
    readonly_fields = ('created_at', 'updated_at')
    fieldsets = (
        (None, {
            'fields': ('name', 'slug', 'description', 'is_active')
        }),
        (_('Form Configuration'), {
            'fields': ('input_config',),
            'classes': ('collapse',),
            'description': _('Define the dynamic form fields as JSON. See documentation for format.'),
        }),
        (_('Processing'), {
            'fields': ('processor_module', 'processor_function'),
        }),
        (_('Output Configuration'), {
            'fields': ('output_config',),
            'classes': ('collapse',),
            'description': _('Define the output files as JSON. See documentation for format.'),
        }),
        (_('Timestamps'), {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )
    inlines = [WorkflowPermissionInline]


class WorkflowExecutionLogAdmin(admin.ModelAdmin):
    """Admin configuration for execution logs (read-only)."""
    list_display = ('id', 'workflow', 'user', 'execution_time', 'status', 'duration_seconds')
    list_filter = ('status', 'execution_time', 'workflow')
    search_fields = ('user__email', 'workflow__name', 'error_message')
    ordering = ('-execution_time',)
    readonly_fields = (
        'user', 'workflow', 'execution_time', 'client_ip',
        'input_filenames', 'output_filenames', 'duration_seconds',
        'status', 'error_message',
    )

    def has_add_permission(self, request):
        return False  # Logs are created only programmatically

    def has_change_permission(self, request, obj=None):
        return False  # Logs are immutable


admin.site.register(Workflow, WorkflowAdmin)
admin.site.register(WorkflowExecutionLog, WorkflowExecutionLogAdmin)
# WorkflowPermission is registered via inline in WorkflowAdmin, but also add standalone for advanced use
@admin.register(WorkflowPermission)
class WorkflowPermissionAdmin(admin.ModelAdmin):
    list_display = ('user', 'workflow', 'date_granted')
    search_fields = ('user__email', 'workflow__name')
    raw_id_fields = ('user', 'workflow')
