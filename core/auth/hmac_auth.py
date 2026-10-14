import hmac
import logging
import time

from asgiref.sync import sync_to_async
from channels.auth import AuthMiddlewareStack
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from rest_framework import authentication
from rest_framework import exceptions

from core.models.facade import Profile
from lib.cache import redis_client as redis_c
from lib.hmac_request import canonical_hmac_message, sign_hmac_message

log = logging.getLogger(__name__)


class HMACAuthentication(authentication.BaseAuthentication):
    def authenticate(self, request):
        api_key, access_signature, nonce = get_rest_authorization_header(request=request)

        if not api_key or not access_signature or not nonce:
            raise exceptions.AuthenticationFailed('APIKEY, SIGNATURE or NONCE header does not set')

        try:
            user = get_hmac_user(
                api_key,
                access_signature,
                nonce,
                method=request.method,
                path=request.path,
                query=request.META.get('QUERY_STRING', ''),
                body=request.body,
            )
            return user, None
        except exceptions.AuthenticationFailed:
            raise
        except Exception as e:
            log.exception(repr(e))
            raise exceptions.AuthenticationFailed('Auth error')


class HMACAuthMiddleware:
    """
    HMAC authorization middleware for Django Channels 2
    """

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        headers = dict(scope['headers'])
        if b'apikey' in headers and b'signature' in headers and b'nonce' in headers:
            apikey = headers[b'apikey'].decode()
            signature = headers[b'signature'].decode()
            nonce = headers[b'nonce'].decode()
            path = scope.get('path') or '/'
            query = scope.get('query_string') or b''
            if isinstance(query, bytes):
                query = query.decode()
            try:
                user = await sync_to_async(get_hmac_user)(
                    apikey,
                    signature,
                    nonce,
                    method='GET',
                    path=path,
                    query=query,
                    body=b'',
                )
                scope['user'] = user if user else AnonymousUser()
            except Exception:
                scope['user'] = AnonymousUser()

        return await self.inner(scope, receive, send)


def HMACAuthMiddlewareStack(inner):
    return HMACAuthMiddleware(AuthMiddlewareStack(inner))


def _nonce_window_ms():
    try:
        max_age = int(getattr(settings, 'HMAC_NONCE_MAX_AGE_MS', 60000) or 60000)
    except (TypeError, ValueError):
        max_age = 60000
    try:
        skew = int(getattr(settings, 'HMAC_NONCE_FUTURE_SKEW_MS', 5000) or 5000)
    except (TypeError, ValueError):
        skew = 5000
    return max(1000, max_age), max(0, skew)


def get_hmac_user(api_key, access_signature, nonce, method='GET', path='/', query='', body=b''):
    try:
        nonce = int(nonce)
    except (TypeError, ValueError):
        raise exceptions.AuthenticationFailed('NONCE must be type of int')

    max_age_ms, future_skew_ms = _nonce_window_ms()
    now_ms = int(time.time() * 1000)
    if nonce > now_ms + future_skew_ms or now_ms - nonce > max_age_ms:
        raise exceptions.AuthenticationFailed('Incorrect NONCE header')

    profile = Profile.objects.select_related('user').filter(api_key=api_key).first()
    if not profile:
        raise exceptions.AuthenticationFailed('APIKEY does not exists')

    message = canonical_hmac_message(method, path, query, body, api_key, nonce)
    expected = sign_hmac_message(profile.secret_key, message)
    provided = (access_signature or '').strip().lower()
    if len(provided) != len(expected) or not hmac.compare_digest(provided, expected):
        raise exceptions.AuthenticationFailed('Incorrect SIGNATURE header')

    if not profile.user.is_active:
        raise exceptions.AuthenticationFailed('User is inactive')

    redis_key = 'hmac_nonce:{}:{}'.format(api_key, nonce)
    ttl = int((max_age_ms + future_skew_ms) / 1000) + 1
    try:
        claimed = redis_c.set(redis_key, '1', nx=True, ex=ttl)
    except Exception:
        log.exception('HMAC nonce store failed')
        raise exceptions.AuthenticationFailed('Auth error')
    if not claimed:
        raise exceptions.AuthenticationFailed('Incorrect NONCE header')

    return profile.user


def get_rest_authorization_header(request):
    """
    Return request's HMAC headers.
    """
    key = request.META.get('HTTP_APIKEY')
    signature = request.META.get('HTTP_SIGNATURE')
    nonce = request.META.get('HTTP_NONCE')

    return [key, signature, nonce]
