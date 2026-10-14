import json
import os
import time
from collections import OrderedDict

from django.conf import settings

PRIVATE_FIELD_NAMES = {
    'private key',
    'private key encrypted',
    'private_key',
    'password',
}


def public_keeper_record(data):
    if isinstance(data, dict):
        out = OrderedDict()
        for key, value in data.items():
            if str(key).lower() in PRIVATE_FIELD_NAMES:
                continue
            out[key] = public_keeper_record(value)
        return out
    if isinstance(data, list):
        return [public_keeper_record(value) for value in data]
    if isinstance(data, tuple):
        return tuple(public_keeper_record(value) for value in data)
    return data


def handoff_dir():
    configured = getattr(settings, 'KEEPER_HANDOFF_DIR', None) or ''
    path = configured or os.path.join(settings.BASE_DIR, '.handoff')
    os.makedirs(path, mode=0o700, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def write_secret_handoff(payload, prefix):
    directory = handoff_dir()
    filename = '{}_{}_{}.json'.format(prefix, int(time.time()), os.getpid())
    path = os.path.join(directory, filename)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'w') as fh:
            json.dump(payload, fh, indent=2)
            fh.write('\n')
    except Exception:
        try:
            os.unlink(path)
        except OSError:
            pass
        raise
    os.chmod(path, 0o600)
    return path
