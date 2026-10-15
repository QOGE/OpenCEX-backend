from django.conf import settings
from dj_rest_auth.views import LoginView, PasswordChangeView, PasswordResetConfirmView


def _strip_jwt_body(data):
    if not getattr(settings, 'JWT_AUTH_HTTPONLY', False):
        return data
    if isinstance(data, dict):
        data.pop('access_token', None)
        data.pop('refresh_token', None)
        data.pop('access', None)
        data.pop('refresh', None)
    return data


class ThrottledLoginView(LoginView):
    throttle_scope = 'auth_login'

    def get_response(self):
        response = super().get_response()
        _strip_jwt_body(response.data)
        return response


class ThrottledPasswordChangeView(PasswordChangeView):
    throttle_scope = 'auth_otp'


class ThrottledPasswordResetConfirmView(PasswordResetConfirmView):
    throttle_scope = 'auth_code'
