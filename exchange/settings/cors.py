from corsheaders.defaults import default_headers

from .env import env

# Opt-in only. Default is an explicit allow-list from DOMAIN and CORS_ALLOWED_ORIGINS.
CORS_ORIGIN_ALLOW_ALL = env.bool('CORS_ORIGIN_ALLOW_ALL', default=False)


def _cors_allowed_origins():
    origins = []
    origins.extend(env.list('CORS_ALLOWED_ORIGINS', default=[]))
    domain = (env('DOMAIN', default='example.com') or '').strip().rstrip('/')
    if domain:
        if '://' in domain:
            origins.append(domain)
        else:
            origins.append('https://{}'.format(domain))
    seen = set()
    out = []
    for origin in origins:
        origin = origin.strip().rstrip('/')
        if not origin or origin in seen:
            continue
        seen.add(origin)
        out.append(origin)
    return out


CORS_ALLOWED_ORIGINS = _cors_allowed_origins()

CORS_ALLOW_HEADERS = default_headers + (
    'APIKEY',
    'SIGNATURE',
    'NONCE',
    'X_KEY',
    'X_NONCE',
    'X_SIGN',
    'x-api-lang',
)

USE_X_FORWARDED_HOST = True
USE_X_FORWARDED_PORT = True
