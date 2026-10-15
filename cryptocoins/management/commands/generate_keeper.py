import logging
from django.db.models.query import QuerySet

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from cryptocoins.coins.btc import BTC_CURRENCY
from cryptocoins.coins.qoge import QOGE_CURRENCY
from core.consts.currencies import CRYPTO_WALLET_CREATORS, ALL_TOKEN_CURRENCIES
from core.currency import Currency
from core.models.cryptocoins import UserWallet
from cryptocoins.models import GasKeeper
from cryptocoins.models import Keeper
from cryptocoins.utils.btc import generate_btc_multisig_keeper
from cryptocoins.utils.qoge import generate_qoge_multisig_keeper
from cryptocoins.utils.commons import create_keeper
from cryptocoins.utils.keeper_handoff import write_secret_handoff
from cryptocoins.utils.wallet_keys import double_encrypt
from lib.cipher import AESCoderDecoder

log = logging.getLogger(__name__)


class Command(BaseCommand):

    def add_arguments(self, parser):
        parser.add_argument('currency', help='Currency code')
        parser.add_argument('-p', '--password', dest='password', help='Encryption password')
        parser.add_argument('-g', '--gas', dest='gas', action='store_true', help='Is gas keeper')

    def handle(self, *args, **options):
        password = options.get('password')
        currency = Currency.get(options.get('currency'))
        is_gas_keeper = options.get('gas')

        if currency == BTC_CURRENCY:
            _, keeper = generate_btc_multisig_keeper(log)
            self.stdout.write('BTC keeper {}'.format(keeper.user_wallet.address))
            return

        if currency == QOGE_CURRENCY:
            _, keeper = generate_qoge_multisig_keeper(log)
            self.stdout.write('QOGE keeper {}'.format(keeper.user_wallet.address))
            return

        if currency not in CRYPTO_WALLET_CREATORS or currency in ALL_TOKEN_CURRENCIES:
            raise Exception(f'There is no wallet creator for {currency}')

        wallet_create_fn = CRYPTO_WALLET_CREATORS[currency]
        kwargs = {'user_id': None, 'is_new': True, 'currency': currency}

        new_keeper_wallet: UserWallet = wallet_create_fn(**kwargs)
        if isinstance(new_keeper_wallet, QuerySet):
            new_keeper_wallet = new_keeper_wallet.first()

        if not new_keeper_wallet:
            raise Exception('New wallet was not created')

        if is_gas_keeper and not password:
            password = User.objects.make_random_password()
            self.stdout.write('Generated GasKeeper password; writing handoff (mode 600).')

        if not password:
            raise Exception('Password required for Keeper generation')

        private_key = AESCoderDecoder(settings.CRYPTO_KEY).decrypt(new_keeper_wallet.private_key)
        new_keeper_wallet.private_key = double_encrypt(private_key, password)
        new_keeper_wallet.save()
        log.info('Wallet successfully encrypted')
        if is_gas_keeper:
            handoff = write_secret_handoff(
                {
                    'currency': currency.code,
                    'gas_keeper_address': new_keeper_wallet.address,
                    'gas_keeper_password': password,
                },
                prefix='gas-keeper',
            )
            self.stdout.write('GasKeeper password written to {}'.format(handoff))

        KeeperModel = Keeper
        if is_gas_keeper:
            # if currency not in [ETH_CURRENCY, TRX_CURRENCY]:
            #     raise Exception('Only ETH and TRX GasKeeper can be created')
            KeeperModel = GasKeeper

        create_keeper(new_keeper_wallet, KeeperModel)
