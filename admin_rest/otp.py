"""Admin Google Authenticator TOTP (RFC 6238) helpers.

django-otp TOTPDevice stores the secret and last used timestep. Verification
is pyotp with a ±1 period window. A matching timestep is consumed so the same
code cannot be replayed. Application throttles bound guess rate instead of
django-otp's exponential lockout.
"""
from base64 import b32encode

import pyotp
from django.conf import settings
from django.db import transaction
from django_otp.plugins.otp_totp.models import TOTPDevice

from lib.totp import matching_timestep


def get_or_create_admin_device(user):
    device = TOTPDevice.objects.filter(user=user).order_by('id').first()
    if device:
        return device
    return TOTPDevice.objects.create(
        user=user,
        name=user.get_username() or 'admin',
        confirmed=False,
    )


def device_base32_secret(device):
    return b32encode(device.bin_key).decode('ascii').rstrip('=')


def provisioning_uri(user, secret):
    issuer = getattr(settings, 'PROJECT_NAME', None) or 'OpenCEX'
    return pyotp.TOTP(secret).provisioning_uri(
        name=user.get_username(),
        issuer_name=issuer,
    )


def verify_totp(secret, token):
    return matching_timestep(secret, token) is not None


def verify_totp_device(device, token):
    """Verify token for a stored device and consume that timestep."""
    if device is None:
        return False
    secret = device_base32_secret(device)
    timestep = matching_timestep(secret, token)
    if timestep is None:
        return False
    with transaction.atomic():
        locked = TOTPDevice.objects.select_for_update().filter(pk=device.pk).first()
        if locked is None:
            return False
        last_t = locked.last_t if locked.last_t is not None else -1
        if timestep <= last_t:
            return False
        locked.last_t = timestep
        locked.save(update_fields=['last_t'])
        return True
