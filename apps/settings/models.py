"""
Models for the Settings app.
Manages Allowed IP addresses and Application Settings (key-value configuration).
"""
import ipaddress
import logging
from django.db import models
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger('flowforge.settings')


class AllowedIP(models.Model):
    """
    Stores a whitelist of IP addresses or CIDR ranges that are allowed to access the application.
    """

    ip_address = models.CharField(
        _('IP address or CIDR'),
        max_length=45,
        unique=True,
        help_text=_('Single IP (e.g., 192.168.1.100) or CIDR range (e.g., 10.0.0.0/24).'),
    )
    description = models.CharField(
        _('description'),
        max_length=255,
        blank=True,
        default='',
        help_text=_('Optional description for this IP entry.'),
    )
    is_active = models.BooleanField(_('is active'), default=True)
    created_at = models.DateTimeField(_('created at'), auto_now_add=True)

    class Meta:
        db_table = 'settings_allowedip'
        verbose_name = _('allowed IP')
        verbose_name_plural = _('allowed IPs')
        ordering = ['ip_address']

    def __str__(self):
        return f'{self.ip_address} ({self.description or "no description"})'

    def clean(self):
        """Validate that the IP address or CIDR range is valid."""
        try:
            if '/' in self.ip_address:
                ipaddress.ip_network(self.ip_address, strict=True)
            else:
                ipaddress.ip_address(self.ip_address)
        except ValueError as e:
            raise ValidationError({'ip_address': _('Invalid IP address or CIDR range: %(error)s') % {'error': str(e)}})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class ApplicationSetting(models.Model):
    """
    Key-value store for application-wide settings.
    Supports typed values (string, integer, float, boolean).
    """

    VALUE_TYPES = (
        ('str', _('String')),
        ('int', _('Integer')),
        ('float', _('Float')),
        ('bool', _('Boolean')),
    )

    key = models.CharField(_('key'), max_length=255, unique=True, db_index=True)
    value = models.TextField(_('value'))
    value_type = models.CharField(
        _('value type'),
        max_length=10,
        choices=VALUE_TYPES,
        default='str',
    )
    description = models.TextField(_('description'), blank=True, default='')

    class Meta:
        db_table = 'settings_applicationsetting'
        verbose_name = _('application setting')
        verbose_name_plural = _('application settings')
        ordering = ['key']

    def __str__(self):
        return f'{self.key} = {self.value}'

    def get_typed_value(self):
        """Return the value cast to the appropriate type."""
        if self.value_type == 'int':
            return int(self.value)
        elif self.value_type == 'float':
            return float(self.value)
        elif self.value_type == 'bool':
            return self.value.lower() in ('true', '1', 'yes', 'on')
        return self.value

    @classmethod
    def get_setting(cls, key: str, default=None):
        """
        Retrieve a setting value by key.

        Args:
            key: The setting key.
            default: Default value if key is not found.

        Returns:
            The typed value of the setting or the default.
        """
        try:
            setting = cls.objects.get(key=key)
            return setting.get_typed_value()
        except cls.DoesNotExist:
            logger.debug(f"ApplicationSetting '{key}' not found. Using default: {default}")
            return default

    @classmethod
    def set_setting(cls, key: str, value, value_type: str = 'str', description: str = ''):
        """
        Create or update an application setting.

        Args:
            key: Setting key.
            value: Setting value.
            value_type: Type of the value ('str', 'int', 'float', 'bool').
            description: Optional description.

        Returns:
            The created or updated ApplicationSetting instance.
        """
        setting, created = cls.objects.update_or_create(
            key=key,
            defaults={
                'value': str(value),
                'value_type': value_type,
                'description': description,
            }
        )
        logger.info(f"ApplicationSetting '{key}' {'created' if created else 'updated'} with value: {value}")
        return setting
