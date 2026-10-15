from dj_rest_auth.views import LoginView, PasswordChangeView, PasswordResetConfirmView


class ThrottledLoginView(LoginView):
    throttle_scope = 'auth_login'


class ThrottledPasswordChangeView(PasswordChangeView):
    throttle_scope = 'auth_otp'


class ThrottledPasswordResetConfirmView(PasswordResetConfirmView):
    throttle_scope = 'auth_code'
