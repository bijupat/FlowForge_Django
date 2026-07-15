"""
Models for the Accounts app.
Defines custom User model and OTP (One-Time Password) model.
"""
from django.db import models
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin, BaseUserManager
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

class UserManager(BaseUserManager):
    """
    Custom manager for User model with email as the unique identifier.
    """
    def create_user(self, email, name, password=None, **extra_fields):
        if not email:
            raise ValueError(_('The Email field must be set'))
        email = self.normalize_email(email)
        user = self.model(email=email, name=name, **extra_fields)
        if password:
            user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, name, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_active', True)

        if extra_fields.get('is_staff') is not True:
            raise ValueError(_('Superuser must have is_staff=True.'))
        if extra_fields.get('is_superuser') is not True:
            raise ValueError(_('Superuser must have is_superuser=True.'))

        return self.create_user(email, name, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    """
    Custom User model where email is the unique identifier for authentication.
    No username / password login required; authentication is done via Email OTP.
    """
    email = models.EmailField(_('email address'), unique=True, db_index=True)
    name = models.CharField(_('full name'), max_length=255)
    is_active = models.BooleanField(_('active'), default=True)
    is_staff = models.BooleanField(_('staff status'), default=False)
    date_joined = models.DateTimeField(_('date joined'), auto_now_add=True)

    objects = UserManager()

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['name']

    class Meta:
        db_table = 'accounts_user'
        verbose_name = _('user')
        verbose_name_plural = _('users')
        ordering = ['email']

    def __str__(self):
        return self.email

    def get_full_name(self):
        return self.name

    def get_short_name(self):
        return self.name.split(' ')[0] if self.name else self.email


class OTP(models.Model):
    """
    One-Time Password model for email-based authentication.
    Each OTP is associated with a user and can be used only once.
    """
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='otps'
    )
    code = models.CharField(_('OTP code'), max_length=6)
    created_at = models.DateTimeField(_('created at'), auto_now_add=True)
    is_used = models.BooleanField(_('is used'), default=False)

    class Meta:
        db_table = 'accounts_otp'
        verbose_name = _('OTP')
        verbose_name_plural = _('OTPs')
        ordering = ['-created_at']

    def __str__(self):
        return f'OTP for {self.user.email} - {self.code}'

    def is_valid(self) -> bool:
        """
        Check if OTP is still valid (not used and not expired).
        Expiry time is fetched from ApplicationSetting.
        """
        if self.is_used:
            return False

        from apps.settings.models import ApplicationSetting
        expire_minutes = ApplicationSetting.get_setting('OTP_EXPIRY_MINUTES', default=10)
        expiration_time = self.created_at + timezone.timedelta(minutes=expire_minutes)
        return timezone.now() <= expiration_time

    @staticmethod
    def clear_expired():
        """Delete all expired OTPs."""
        from apps.settings.models import ApplicationSetting
        expire_minutes = ApplicationSetting.get_setting('OTP_EXPIRY_MINUTES', default=10)
        cutoff = timezone.now() - timezone.timedelta(minutes=expire_minutes)
        OTP.objects.filter(created_at__lt=cutoff).delete()
