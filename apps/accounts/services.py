"""
Services for Email OTP authentication.
Handles OTP generation, sending via email, and verification.
"""
import random
import logging
from typing import Optional

from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.utils import timezone

from apps.accounts.models import OTP, User
from apps.settings.models import ApplicationSetting

logger = logging.getLogger('flowforge.accounts')


class InvalidOTPException(Exception):
    """Raised when OTP verification fails."""
    pass


class EmailOTPService:
    """Service class handling OTP generation, sending, and verification."""

    @staticmethod
    def generate_otp(user: User) -> str:
        """
        Generate a 6-digit OTP, save it to database, and send via email.

        Args:
            user: The User object to associate the OTP with.

        Returns:
            The generated OTP code string.
        """
        OTP.clear_expired()
        code = str(random.randint(100000, 999999))
        OTP.objects.create(user=user, code=code)
        EmailOTPService.send_otp_email(user, code)
        logger.info(f"OTP generated and sent for user {user.email}")
        return code

    @staticmethod
    def send_otp_email(user: User, otp_code: str) -> None:
        """
        Send OTP code to user's email address.

        Args:
            user: The user to send OTP to.
            otp_code: The 6-digit OTP code.
        """
        try:
            subject = 'FlowForge - Your One-Time Password (OTP)'
            from_email = None  # Uses DEFAULT_FROM_EMAIL
            to_email = [user.email]

            # Render HTML and plain text templates
            context = {
                'user': user,
                'otp_code': otp_code,
                'expiry_minutes': ApplicationSetting.get_setting('OTP_EXPIRY_MINUTES', default=10),
            }
            html_content = render_to_string('accounts/email/otp_email.html', context)
            text_content = strip_tags(html_content)  # Plain text fallback

            email = EmailMultiAlternatives(
                subject=subject,
                body=text_content,
                from_email=from_email,
                to=to_email,
            )
            email.attach_alternative(html_content, 'text/html')
            email.send(fail_silently=False)
            logger.info(f"OTP email sent to {user.email}")

        except Exception as e:
            logger.error(f"Failed to send OTP email to {user.email}: {e}")
            raise

    @staticmethod
    def verify_otp(user: User, otp_code: str) -> bool:
        """
        Verify the provided OTP code for the given user.

        Args:
            user: The User object.
            otp_code: The OTP code string to verify.

        Returns:
            True if verification is successful.

        Raises:
            InvalidOTPException: If OTP is invalid, expired, or already used.
        """
        otp = OTP.objects.filter(
            user=user,
            code=otp_code,
            is_used=False,
        ).order_by('-created_at').first()

        if not otp:
            logger.warning(f"Invalid OTP attempt for user {user.email}")
            raise InvalidOTPException('Invalid OTP code.')

        if not otp.is_valid():
            logger.warning(f"Expired OTP attempt for user {user.email}")
            raise InvalidOTPException('OTP has expired. Please request a new one.')

        # Mark as used
        otp.is_used = True
        otp.save(update_fields=['is_used'])
        logger.info(f"OTP verified successfully for user {user.email}")
        return True

    @staticmethod
    def clear_expired_otps() -> None:
        """Delete all expired OTP records."""
        OTP.clear_expired()
