import hashlib
import time

import pyotp
from django.core.cache import cache


def matching_timestep(secret, code, valid_window=1):
    """Return the TOTP timestep that matches code, or None."""
    if not secret or code is None:
        return None
    token = str(code).strip()
    if not token:
        return None
    totp = pyotp.TOTP(secret)
    interval = totp.interval or 30
    now = time.time()
    for offset in range(-int(valid_window), int(valid_window) + 1):
        timestep = int(now // interval) + offset
        if totp.verify(token, for_time=timestep * interval, valid_window=0):
            return timestep
    return None


def totp_consume_key(secret, extra=''):
    digest = hashlib.sha256('{}:{}'.format(extra, secret).encode('utf-8')).hexdigest()[:32]
    return 'totp-used:{}'.format(digest)


def consume_timestep(consume_key, timestep, interval=30):
    """Mark a timestep used. Returns False if it was already consumed."""
    if not consume_key or timestep is None:
        return False
    timeout = int(interval) * 3
    if timeout < 90:
        timeout = 90
    return bool(cache.add('{}:{}'.format(consume_key, int(timestep)), 1, timeout=timeout))


def verify_and_consume(secret, code, consume_key, valid_window=1):
    timestep = matching_timestep(secret, code, valid_window=valid_window)
    if timestep is None:
        return False
    totp = pyotp.TOTP(secret)
    return consume_timestep(consume_key, timestep, interval=totp.interval or 30)
