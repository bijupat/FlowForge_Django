"""
URL configuration for workflows app.
"""
from django.urls import path
from . import views

app_name = 'workflows'

urlpatterns = [
    path('<slug:slug>/', views.WorkflowExecuteView.as_view(), name='execute'),
    path('download/<str:output_key>/', views.WorkflowDownloadView.as_view(), name='download'),
]
