"""
Views for the accounts app.
Handles Email OTP login, OTP verification, and logout.
"""
import logging
from django.shortcuts import render, redirect
from django.urls import reverse_lazy
from django.views.generic import FormView, RedirectView
from django.contrib.auth import login, logout
from django.contrib import messages
from django.utils.translation import gettext_lazy as _

from apps.accounts.forms import EmailForm, OTPForm
from apps.accounts.models import User
from apps.accounts.services import EmailOTPService, InvalidOTPException

logger = logging.getLogger('flowforge.accounts')


class LoginView(FormView):
    """
    View for initiating Email OTP login.
    User enters their email, an OTP is generated and sent.
    """
    template_name = 'accounts/login.html'
    form_class = EmailForm
    success_url = reverse_lazy('accounts:otp-verify')

    def form_valid(self, form):
        email = form.cleaned_data['email'].lower().strip()
        # Get or create user
        user, created = User.objects.get_or_create(
            email=email,
            defaults={'name': email.split('@')[0]}  # Default name from email local part
        )
        if created:
            logger.info(f"New user created with email: {email}")

        # Generate and send OTP
        try:
            EmailOTPService.generate_otp(user)
        except Exception as e:
            logger.error(f"Failed to send OTP for {email}: {e}")
            messages.error(self.request, _('Failed to send OTP. Please try again later.'))
            return self.form_invalid(form)

        # Store email in session for OTP verification
        self.request.session['otp_email'] = email
        messages.success(self.request, _('An OTP has been sent to your email address.'))
        return super().form_valid(form)


class OTPVerificationView(FormView):
    """
    View for verifying the OTP code sent to user's email.
    On success, user is logged in and redirected to dashboard.
    """
    template_name = 'accounts/otp_verify.html'
    form_class = OTPForm
    success_url = reverse_lazy('core:dashboard')

    def dispatch(self, request, *args, **kwargs):
        # Ensure session has the email from the login step
        if 'otp_email' not in request.session:
            messages.error(request, _('Please enter your email first.'))
            return redirect('accounts:login')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['email'] = self.request.session.get('otp_email', '')
        return context

    def form_valid(self, form):
        email = self.request.session.get('otp_email')
        otp_code = form.cleaned_data['otp_code']

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            messages.error(self.request, _('User not found. Please enter your email again.'))
            return redirect('accounts:login')

        try:
            EmailOTPService.verify_otp(user, otp_code)
        except InvalidOTPException as e:
            messages.error(self.request, str(e))
            return self.form_invalid(form)

        # Log the user in
        user.backend = 'django.contrib.auth.backends.ModelBackend'
        login(self.request, user)

        # Clean up session
        del self.request.session['otp_email']

        logger.info(f"User {user.email} logged in successfully.")
        messages.success(self.request, _('Login successful!'))
        return super().form_valid(form)


class LogoutView(RedirectView):
    """
    View for logging out the user.
    """
    url = reverse_lazy('accounts:login')

    def get(self, request, *args, **kwargs):
        logout(request)
        messages.info(request, _('You have been logged out.'))
        return super().get(request, *args, **kwargs)
