"""
Views for the core app.
Provides the user dashboard.
"""
import logging
from django.shortcuts import render
from django.views.generic import TemplateView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy

from apps.workflows.models import WorkflowPermission, Workflow

logger = logging.getLogger('flowforge.core')


class DashboardView(LoginRequiredMixin, TemplateView):
    """
    Dashboard view for authenticated users.
    Displays list of workflows assigned to the current user.
    """
    template_name = 'core/dashboard.html'
    login_url = reverse_lazy('accounts:login')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Get workflows assigned to the current user
        user = self.request.user
        workflow_ids = WorkflowPermission.objects.filter(
            user=user
        ).values_list('workflow_id', flat=True)
        workflows = Workflow.objects.filter(
            id__in=workflow_ids,
            is_active=True
        ).order_by('name')

        context['workflows'] = workflows
        logger.info(f"Dashboard accessed by user: {user.email}, workflows found: {workflows.count()}")
        return context
