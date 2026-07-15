"""
Views for the Workflows app.
Handles dynamic workflow forms, execution, and file downloads.
"""
import os
import logging
from pathlib import Path

from django.shortcuts import render, redirect, get_object_or_404
from django.views.generic import View
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import FileResponse, Http404, HttpResponseForbidden
from django.conf import settings
from django.contrib import messages
from django.utils.translation import gettext_lazy as _
from django.urls import reverse
from django.utils import timezone

from .models import Workflow, WorkflowPermission, WorkflowExecutionLog
from .forms import build_dynamic_form
from .services import (
    WorkflowProcessorService,
    FileValidatorService,
    OutputGeneratorService,
    ProcessorLoadError,
    ProcessorExecuteError,
)

logger = logging.getLogger('flowforge.workflows')


class WorkflowExecuteView(LoginRequiredMixin, View):
    """
    Handles the dynamic workflow form display (GET) and execution (POST).
    """

    login_url = '/accounts/login/'

    def get_workflow(self, slug):
        """Get workflow and verify user permission."""
        workflow = get_object_or_404(Workflow, slug=slug, is_active=True)

        # Check permission
        has_permission = WorkflowPermission.objects.filter(
            user=self.request.user,
            workflow=workflow,
        ).exists()

        if not has_permission and not self.request.user.is_staff:
            logger.warning(f"User {self.request.user.email} denied access to workflow {workflow.slug}")
            raise Http404(_("You don't have permission to access this workflow."))

        return workflow

    def get(self, request, slug):
        workflow = self.get_workflow(slug)
        FormClass = build_dynamic_form(workflow.input_config)
        form = FormClass()

        context = {
            'workflow': workflow,
            'form': form,
        }
        return render(request, 'workflows/workflow_form.html', context)

    def post(self, request, slug):
        workflow = self.get_workflow(slug)
        FormClass = build_dynamic_form(workflow.input_config)
        form = FormClass(request.POST, request.FILES)

        if not form.is_valid():
            context = {
                'workflow': workflow,
                'form': form,
            }
            return render(request, 'workflows/workflow_form.html', context)

        # Validate uploaded files
        cleaned_data = form.cleaned_data
        uploaded_files = {}
        form_data = {}

        try:
            for field_config in workflow.input_config:
                field_name = field_config['name']
                if field_config.get('field_type') == 'file':
                    if field_config.get('multiple'):
                        # Multi-file field: read every selected file, not
                        # just the first, and validate each one.
                        files = request.FILES.getlist(field_name)
                        if files:
                            for uploaded in files:
                                FileValidatorService.validate_file(uploaded, field_config)
                            uploaded_files[field_name] = files
                        elif field_config.get('required'):
                            raise ProcessorExecuteError(
                                _(f'At least one file is required for "{field_config.get("label", field_name)}".')
                            )
                    else:
                        file = request.FILES.get(field_name)
                        if file:
                            # Validate file
                            FileValidatorService.validate_file(file, field_config)
                            uploaded_files[field_name] = file
                        elif field_config.get('required'):
                            raise ProcessorExecuteError(_(f'Required file "{field_config.get("label", field_name)}" is missing.'))
                else:
                    # Store non-file form data
                    form_data[field_name] = cleaned_data.get(field_name)

            # Execute workflow
            output_files = WorkflowProcessorService.process_workflow(
                workflow=workflow,
                uploaded_files=uploaded_files,
                form_data=form_data,
                request=request,
            )

            # Prepare output information for display
            outputs = OutputGeneratorService.ensure_output_structure(
                workflow.output_config,
                output_files,
            )

            # Generate download URLs
            # We'll create an execution context stored in session
            execution_id = os.path.basename(os.path.dirname(next(iter(output_files.values()), '')))
            if not execution_id:
                raise ProcessorExecuteError(_('No output files were generated.'))

            request.session['last_execution'] = {
                'workflow_slug': slug,
                'outputs': {
                    key: {
                        'path': outputs[key]['path'],
                        'format': outputs[key]['format'],
                        'label': outputs[key]['label'],
                        'filename': outputs[key]['filename'],
                    }
                    for key in outputs
                },
            }

            messages.success(request, _('Workflow executed successfully!'))
            context = {
                'workflow': workflow,
                'outputs': outputs,
                'success': True,
            }
            return render(request, 'workflows/workflow_result.html', context)

        except (ProcessorLoadError, ProcessorExecuteError) as e:
            logger.error(f"Workflow execution error: {e}")
            messages.error(request, str(e))
            context = {
                'workflow': workflow,
                'form': form,
            }
            return render(request, 'workflows/workflow_form.html', context)
        except Exception as e:
            logger.error(f"Unexpected error during workflow execution: {e}", exc_info=True)
            messages.error(request, _('An unexpected error occurred. Please try again later.'))
            context = {
                'workflow': workflow,
                'form': form,
            }
            return render(request, 'workflows/workflow_form.html', context)


class WorkflowDownloadView(LoginRequiredMixin, View):
    """
    Serves generated output files for download.
    File information is retrieved from the session for security.
    """

    login_url = '/accounts/login/'

    def get(self, request, output_key):
        # Retrieve execution info from session
        execution_info = request.session.get('last_execution', {})
        if not execution_info:
            raise Http404(_('No execution found. Please run the workflow again.'))

        output_info = execution_info.get('outputs', {}).get(output_key)
        if not output_info:
            raise Http404(_('Output file not found.'))

        file_path = output_info['path']
        if not os.path.exists(file_path):
            raise Http404(_('File no longer exists. Please re-run the workflow.'))

        # Verify file is within the allowed generated directory
        generated_dir = os.path.realpath(os.path.join(settings.BASE_DIR, 'generated'))
        real_file_path = os.path.realpath(file_path)
        if not real_file_path.startswith(generated_dir):
            logger.error(f"Attempted path traversal: {file_path}")
            raise Http404(_('Invalid file path.'))

        filename = output_info.get('filename', os.path.basename(file_path))
        content_type = OutputGeneratorService.get_content_type(output_info.get('format', ''))

        response = FileResponse(
            open(file_path, 'rb'),
            content_type=content_type,
            as_attachment=True,
            filename=filename,
        )
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response