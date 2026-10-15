import logging

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction

from cryptocoins.models import GasKeeper
from cryptocoins.utils.keeper_handoff import write_secret_handoff
from cryptocoins.utils.wallet_keys import (
    gas_keeper_password_for,
    looks_like_hex_privkey,
    unwrap_after_crypto_key,
    wrap_crypto_key_ciphertext,
    write_gas_keeper_password_file,
)
from lib.cipher import AESCoderDecoder
from django.conf import settings

log = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        'Apply the Keeper two-layer scheme to existing GasKeeper wallets. '
        'Writes the password to .handoff/gas_keeper_password (mode 600). '
        'Does not print the password or any private key.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '-p', '--password',
            dest='password',
            help='Password to use. Generated if omitted and none is already configured.',
        )

    def handle(self, *args, **options):
        password = (options.get('password') or '').strip()
        if not password:
            password = gas_keeper_password_for('')
        if not password:
            password = User.objects.make_random_password()

        keepers = list(GasKeeper.objects.select_related('user_wallet').all())
        if not keepers:
            self.stdout.write('No GasKeeper rows found.')
            return

        wrapped = []
        skipped = []
        with transaction.atomic():
            for keeper in keepers:
                wallet = keeper.user_wallet
                if not wallet or not wallet.private_key:
                    skipped.append((keeper.currency.code, 'missing wallet'))
                    continue
                new_cipher = wrap_crypto_key_ciphertext(wallet.private_key, password)
                if new_cipher is None:
                    skipped.append((keeper.currency.code, 'already wrapped'))
                    continue
                wallet.private_key = new_cipher
                wallet.save(update_fields=['private_key', 'updated'])
                wrapped.append({
                    'currency': keeper.currency.code,
                    'address': wallet.address,
                })

        if not wrapped:
            self.stdout.write(
                'No CRYPTO_KEY-only GasKeeper keys to wrap (skipped {s}).'.format(
                    s=len(skipped),
                )
            )
            return

        path = write_gas_keeper_password_file(password)
        handoff = write_secret_handoff(
            {
                'wrapped': wrapped,
                'skipped': [{'currency': c, 'reason': r} for c, r in skipped],
                'password_file': path,
                'GAS_KEEPER_PASSWORD': password,
            },
            prefix='gas-keeper-wrap',
        )

        # Prove unwrap works without printing keys.
        for keeper in GasKeeper.objects.select_related('user_wallet'):
            wallet = keeper.user_wallet
            if not wallet or not wallet.private_key:
                continue
            layer1 = AESCoderDecoder(settings.CRYPTO_KEY).decrypt(wallet.private_key)
            if looks_like_hex_privkey(layer1):
                raise RuntimeError(
                    '{} GasKeeper is still CRYPTO_KEY-only after wrap'.format(
                        keeper.currency.code,
                    )
                )
            inner = unwrap_after_crypto_key(layer1, password)
            if not looks_like_hex_privkey(inner):
                raise RuntimeError(
                    '{} GasKeeper unwrap failed after wrap'.format(keeper.currency.code)
                )

        self.stdout.write(
            'Wrapped {n} GasKeeper(s); skipped {s}. '
            'Password file {path}. Handoff {handoff}. '
            'Copy the handoff off this host and delete it.'.format(
                n=len(wrapped),
                s=len(skipped),
                path=path,
                handoff=handoff,
            )
        )
