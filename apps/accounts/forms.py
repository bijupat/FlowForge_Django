"""
Forms for the accounts app.
Handles email input for OTP login and OTP code verification.
"""
import re
from django import forms
from django.utils.translation import gettext_lazy as _


class EmailForm(forms.Form):
    """
    Form for collecting user's email address to send OTP.
    """
    email = forms.EmailField(
        label=_('Email Address'),
        max_length=254,
        widget=forms.EmailInput(attrs={
            'class': 'form-control form-control-lg',
            'placeholder': 'Enter your email address',
            'autocomplete': 'email',
            'autofocus': True,
        }),
    )

    def clean_email(self):
        email = self.cleaned_data['email'].lower().strip()
        # Basic email format validation is done by EmailField
        return email


class OTPForm(forms.Form):
    """
    Form for verifying the OTP code.
    """
    otp_code = forms.CharField(
        label=_('OTP Code'),
        min_length=6,
        max_length=6,
        widget=forms.TextInput(attrs={
            'class': 'form-control form-control-lg text-center',
            'placeholder': '000000',
            'autocomplete': 'one-time-code',
            'autofocus': True,
            'inputmode': 'numeric',
            'pattern': '[0-9]{6}',
            'maxlength': '6',
        }),
    )

    def clean_otp_code(self):
        code = self.cleaned_data['otp_code']
        if not re.match(r'^\d{6}$', code):
            raise forms.ValidationError(_('OTP code must be exactly 6 digits.'))
        return code
