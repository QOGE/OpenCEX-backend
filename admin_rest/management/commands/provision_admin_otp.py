from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from admin_rest.otp import provision_admin_device, provisioning_uri
from cryptocoins.utils.keeper_handoff import write_secret_handoff

User = get_user_model()


class Command(BaseCommand):
    help = (
        'Provision admin TOTP for a staff user and write the secret to a '
        'mode-600 handoff file. Login never returns this secret.'
    )

    def add_arguments(self, parser):
        parser.add_argument('username', help='Staff username')

    def handle(self, *args, **options):
        username = options['username']
        user = User.objects.filter(username=username, is_active=True).first()
        if user is None:
            raise CommandError('No active user named {}'.format(username))
        if not user.is_staff and not user.is_superuser:
            raise CommandError('User is not staff')

        device, secret, created = provision_admin_device(user)
        path = write_secret_handoff(
            {
                'username': user.get_username(),
                'totp_secret': secret,
                'otpauth_url': provisioning_uri(user, secret),
                'confirmed': bool(device.confirmed),
                'created': created,
            },
            prefix='admin-totp',
        )
        self.stdout.write(
            'TOTP handoff written to {} (mode 600). '
            'Copy it off this host and delete it.'.format(path)
        )
