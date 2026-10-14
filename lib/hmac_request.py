import hashlib
import hmac
import time


def canonical_query(query):
    if not query:
        return ''
    parts = [p for p in query.split('&') if p != '']
    parts.sort(key=lambda p: (p.split('=', 1)[0], p))
    return '&'.join(parts)


def body_sha256(body):
    if body is None:
        body = b''
    if isinstance(body, str):
        body = body.encode('utf-8')
    return hashlib.sha256(body).hexdigest()


def canonical_hmac_message(method, path, query, body, api_key, nonce):
    method = (method or 'GET').upper()
    path = path or '/'
    return '\n'.join([
        method,
        path,
        canonical_query(query or ''),
        body_sha256(body),
        api_key,
        str(nonce),
    ])


def sign_hmac_message(secret_key, message):
    if isinstance(secret_key, str):
        secret_key = secret_key.encode('utf-8')
    if isinstance(message, str):
        message = message.encode('utf-8')
    return hmac.new(secret_key, message, hashlib.sha256).hexdigest()


def hmac_request_headers(api_key, secret_key, method, path, query='', body=b'', nonce=None):
    if nonce is None:
        nonce = str(int(time.time() * 1000))
    message = canonical_hmac_message(method, path, query, body, api_key, nonce)
    return {
        'APIKEY': api_key,
        'SIGNATURE': sign_hmac_message(secret_key, message).upper(),
        'NONCE': str(nonce),
    }
