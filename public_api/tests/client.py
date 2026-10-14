import json

import requests

from lib.hmac_request import hmac_request_headers


class Client:

    def __init__(self, base_url=None) -> None:
        self.session = requests.Session()
        self.base_url = base_url
        if base_url is None:
            self.base_url = 'http://127.0.0.1:8080'

        self.token = None
        self.api_key = None
        self.secret_key = None

    def get(self, url, params=None):
        path, query = self._split_url(url)
        extra_query = self._encode_params(params)
        if query and extra_query:
            query = query + '&' + extra_query
        elif extra_query:
            query = extra_query
        headers = self._get_hmac_auth_headers('GET', path, query, b'')
        return self.session.get(self.base_url + url, params=params, headers=headers)

    def post(self, url, data=None):
        if data is None:
            data = {}
        path, query = self._split_url(url)
        body = json.dumps(data).encode('utf-8')
        headers = self._get_hmac_auth_headers('POST', path, query, body)
        headers['Content-Type'] = 'application/json'
        return self.session.post(self.base_url + url, data=body, headers=headers)

    def regenerate_keys(self):
        return self.session.post(self.base_url + '/api/v1/regenerate-api-key/').json()

    def login(self, username, password):
        # token auth
        result = self.session.post(self.base_url + '/api/v1/auth/login/', json={
            'username': username,
            'password': password,
        })
        self.token = result.json().get('key')
        self.session.headers['Authorization'] = f'Token {self.token}'

        # hmac auth
        result = self.regenerate_keys()
        self.api_key = result.get('api_key')
        self.secret_key = result.get('secret_key')

    def logout(self):
        self.token = None
        self.api_key = None
        self.secret_key = None

    def _get_hmac_auth_headers(self, method, path, query, body):
        if self.api_key is None or self.secret_key is None:
            return {}
        return hmac_request_headers(
            self.api_key,
            self.secret_key,
            method,
            path,
            query=query,
            body=body,
        )

    @staticmethod
    def _split_url(url):
        if '?' in url:
            path, query = url.split('?', 1)
            return path, query
        return url, ''

    @staticmethod
    def _encode_params(params):
        if not params:
            return ''
        from urllib.parse import urlencode
        return urlencode(params, doseq=True)
