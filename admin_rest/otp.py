"""Admin Google Authenticator TOTP (RFC 6238) helpers.

django-otp TOTPDevice is only used as secret storage. Verification is pyotp
with a ±1 period window so a slightly skewed phone clock still works, without
django-otp's exponential lockout.
"""
from base64 import b32encode

import pyotp
from django.conf import settings
from django_otp.plugins.otp_totp.models import TOTPDevice


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
    if not token:
        return False
    return bool(pyotp.TOTP(secret).verify(str(token).strip(), valid_window=1))
