"""
URL configuration for accounts app.
"""
from django.urls import path
from . import views

app_name = 'accounts'

urlpatterns = [
    path('login/', views.LoginView.as_view(), name='login'),
    path('otp-verify/', views.OTPVerificationView.as_view(), name='otp-verify'),
    path('logout/', views.LogoutView.as_view(), name='logout'),
]
