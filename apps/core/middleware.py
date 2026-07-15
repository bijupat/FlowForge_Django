"""
Custom middleware for FlowForge.
Includes IP Whitelist enforcement and Session Timeout handling.
"""
import logging
from ipaddress import ip_address, ip_network

from django.http import HttpResponseForbidden
from django.shortcuts import render
from django.conf import settings
from django.contrib.auth import logout
from django.utils import timezone

from apps.settings.models import AllowedIP, ApplicationSetting

logger = logging.getLogger('flowforge.core')


class IPWhitelistMiddleware:
    """
    Middleware to enforce IP whitelist.
    Before any view is processed, verify client IP is in the allowed list.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Check IP whitelist
        if not self.is_ip_allowed(request):
            logger.warning(f"Access denied for IP: {self.get_client_ip(request)}")
            return render(request, '403.html', status=403)

        response = self.get_response(request)
        return response

    def get_client_ip(self, request) -> str:
        """
        Extract client IP address from request.

        Handles X-Forwarded-For header for proxied connections.
        """
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            # Take the first IP in the list
            ip = x_forwarded_for.split(',')[0].strip()
        else:
            ip = request.META.get('REMOTE_ADDR')
        return ip

    def is_ip_allowed(self, request) -> bool:
        """
        Check if the client IP is in the allowed IP list.
        """
        client_ip = self.get_client_ip(request)

        # Always allow localhost in DEBUG mode
        if settings.DEBUG and client_ip in ['127.0.0.1', '::1', 'localhost']:
            return True

        # Get all active IP entries
        allowed_ips = AllowedIP.objects.filter(is_active=True)

        try:
            client_ip_obj = ip_address(client_ip)
        except ValueError:
            logger.error(f"Invalid client IP format: {client_ip}")
            return False

        for entry in allowed_ips:
            try:
                # Check if entry is an IP address or network
                if '/' in entry.ip_address:
                    # CIDR notation
                    network = ip_network(entry.ip_address, strict=False)
                    if client_ip_obj in network:
                        return True
                else:
                    # Single IP
                    if client_ip_obj == ip_address(entry.ip_address):
                        return True
            except ValueError:
                logger.error(f"Invalid IP entry in database: {entry.ip_address}")
                continue

        # If we're in DEBUG mode, allow all (for development)
        if settings.DEBUG:
            return True

        return False


class SessionTimeoutMiddleware:
    """
    Middleware to enforce configurable session timeout.
    Reads timeout from ApplicationSetting and applies it to the session.
    Also handles automatic logout for expired sessions.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            self._check_session_timeout(request)

        response = self.get_response(request)

        # Update session expiry on each request for active users
        if request.user.is_authenticated:
            self._update_session_expiry(request)

        return response

    def _get_session_timeout(self) -> int:
        """
        Get session timeout in minutes from ApplicationSetting.
        Default is 30 minutes if not configured.
        """
        return ApplicationSetting.get_setting('SESSION_TIMEOUT_MINUTES', default=30)

    def _check_session_timeout(self, request):
        """
        Check if the user's session has expired based on last activity.
        If expired, logout the user.
        """
        last_activity = request.session.get('last_activity')
        if last_activity:
            last_activity_time = timezone.datetime.fromisoformat(last_activity)
            timeout_minutes = self._get_session_timeout()
            if timezone.now() > last_activity_time + timezone.timedelta(minutes=timeout_minutes):
                logger.info(f"Session expired for user {request.user.email}. Logging out.")
                logout(request)
                request.session.flush()

    def _update_session_expiry(self, request):
        """
        Update the session's last activity timestamp.
        """
        request.session['last_activity'] = timezone.now().isoformat()
        # Extend session expiry
        timeout_seconds = self._get_session_timeout() * 60
        request.session.set_expiry(timeout_seconds)
