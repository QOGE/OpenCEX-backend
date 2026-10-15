from rest_framework.permissions import BasePermission
from rest_framework_simplejwt.tokens import RefreshToken


# Set only by POST /apiadmin/login/. Trader login must never mint this claim.
ADMIN_OTP_CLAIM = 'otp_verified'


def token_has_admin_otp(request):
    token = getattr(request, 'auth', None)
    if token is None:
        return False
    try:
        return token.get(ADMIN_OTP_CLAIM) is True
    except Exception:
        return False


def issue_admin_tokens(user):
    """Access + refresh JWTs that IsStaffAccess will accept."""
    refresh = RefreshToken.for_user(user)
    refresh[ADMIN_OTP_CLAIM] = True
    return str(refresh.access_token), str(refresh)


class IsSuperAdminUser(BasePermission):
    """
    Allows access only to superadmin users with an admin-login JWT.
    """

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_active
            and request.user.is_staff
            and request.user.is_superuser
            and token_has_admin_otp(request)
        )
