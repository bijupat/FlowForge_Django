"""
Models for the Workflows app.
Defines Workflow, WorkflowPermission, and WorkflowExecutionLog.
"""
import logging
from django.db import models
from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger('flowforge.workflows')


class Workflow(models.Model):
    """
    Represents a single workflow automation definition.
    Contains configuration for input, processing, and output.
    """
    name = models.CharField(_('name'), max_length=255)
    slug = models.SlugField(_('slug'), max_length=255, unique=True)
    description = models.TextField(_('description'), blank=True, default='')
    is_active = models.BooleanField(_('is active'), default=True)

    # JSON configuration for dynamic form fields
    # Example: [{"name": "file1", "label": "Upload CSV", "field_type": "file", "allowed_extensions": ["csv"], ...}, ...]
    input_config = models.JSONField(
        _('input configuration'),
        default=list,
        help_text=_('List of input field definitions for the dynamic form.'),
    )

    # Processor module path (e.g., 'processors.merge_reports')
    processor_module = models.CharField(
        _('processor module'),
        max_length=255,
        help_text=_('Dotted Python path to the processor module (relative to apps.processors).'),
    )

    # Processor function name within the module
    processor_function = models.CharField(
        _('processor function'),
        max_length=255,
        help_text=_('Name of the function in the processor module to call.'),
    )

    # JSON configuration for output files
    # Example: [{"name": "merged_report", "label": "Merged Report", "format": "XLSX", "filename_template": "merged_{date}.xlsx"}, ...]
    output_config = models.JSONField(
        _('output configuration'),
        default=list,
        help_text=_('List of output definitions for generated files.'),
    )

    created_at = models.DateTimeField(_('created at'), auto_now_add=True)
    updated_at = models.DateTimeField(_('updated at'), auto_now=True)

    class Meta:
        db_table = 'workflows_workflow'
        verbose_name = _('workflow')
        verbose_name_plural = _('workflows')
        ordering = ['name']

    def __str__(self):
        return self.name


class WorkflowPermission(models.Model):
    """
    Maps which users have access to which workflows.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='workflow_permissions',
    )
    workflow = models.ForeignKey(
        Workflow,
        on_delete=models.CASCADE,
        related_name='permissions',
    )
    date_granted = models.DateTimeField(_('date granted'), auto_now_add=True)

    class Meta:
        db_table = 'workflows_workflowpermission'
        verbose_name = _('workflow permission')
        verbose_name_plural = _('workflow permissions')
        unique_together = ('user', 'workflow')
        ordering = ['-date_granted']

    def __str__(self):
        return f'{self.user.email} -> {self.workflow.name}'


class WorkflowExecutionLog(models.Model):
    """
    Logs each execution of a workflow, including timing, files, and status.
    """
    STATUS_CHOICES = (
        ('SUCCESS', _('Success')),
        ('FAILURE', _('Failure')),
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='execution_logs',
    )
    workflow = models.ForeignKey(
        Workflow,
        on_delete=models.CASCADE,
        related_name='execution_logs',
    )
    execution_time = models.DateTimeField(_('execution time'), auto_now_add=True)
    client_ip = models.GenericIPAddressField(_('client IP address'))
    input_filenames = models.TextField(
        _('input filenames'),
        blank=True,
        help_text=_('Newline-separated list of uploaded input filenames.'),
    )
    output_filenames = models.TextField(
        _('output filenames'),
        blank=True,
        help_text=_('Newline-separated list of generated output filenames.'),
    )
    duration_seconds = models.FloatField(_('duration (seconds)'), null=True, blank=True)
    status = models.CharField(
        _('status'),
        max_length=10,
        choices=STATUS_CHOICES,
        default='SUCCESS',
    )
    error_message = models.TextField(_('error message'), blank=True, default='')

    class Meta:
        db_table = 'workflows_executionlog'
        verbose_name = _('workflow execution log')
        verbose_name_plural = _('workflow execution logs')
        ordering = ['-execution_time']

    def __str__(self):
        return f'{self.workflow.name} by {self.user.email} at {self.execution_time}'

    @classmethod
    def log_success(cls, user, workflow, ip, input_files, output_files, duration):
        """Convenience method to log a successful execution."""
        return cls.objects.create(
            user=user,
            workflow=workflow,
            client_ip=ip,
            input_filenames='\n'.join(input_files),
            output_filenames='\n'.join(output_files),
            duration_seconds=duration,
            status='SUCCESS',
        )

    @classmethod
    def log_failure(cls, user, workflow, ip, input_files, error_message, duration=None):
        """Convenience method to log a failed execution."""
        return cls.objects.create(
            user=user,
            workflow=workflow,
            client_ip=ip,
            input_filenames='\n'.join(input_files) if input_files else '',
            duration_seconds=duration,
            status='FAILURE',
            error_message=error_message,
        )
