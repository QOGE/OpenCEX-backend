"""Idempotent DB seed for QOGE coin + QOGE-USDT pair on an existing install."""
import logging

from django.core.management.base import BaseCommand
from django.db.transaction import atomic

from bots.models import BotConfig
from core.models import DisabledCoin, FeesAndLimits, PairSettings, WithdrawalFee
from core.models.facade import CoinInfo
from core.models.inouts.pair import Pair
from cryptocoins.coins.btc import BTC
from cryptocoins.coins.qoge import QOGE, QOGE_CURRENCY
from cryptocoins.coins.qoge.service import QOGECoinService
from cryptocoins.models import Keeper, LastProcessedBlock
from cryptocoins.utils.qoge import generate_qoge_multisig_keeper
from django.contrib.auth import get_user_model

log = logging.getLogger(__name__)
User = get_user_model()

QOGE_USDT_ID = 13
USDT_QOGE_ID = 14


class Command(BaseCommand):
    help = 'Create CoinInfo, fees, QOGE-USDT pair, and optionally a QOGE keeper'

    def add_arguments(self, parser):
        parser.add_argument(
            '--keeper',
            action='store_true',
            help='Generate a 2-of-2 QOGE keeper (requires a reachable qogecoind wallet)',
        )

    @atomic
    def handle(self, *args, **options):
        CoinInfo.objects.update_or_create(
            currency=QOGE,
            defaults={
                'name': 'Qogecoin',
                'decimals': 8,
                'index': 0,
                'is_base': True,
                'tx_explorer': 'https://explorer.qoge.org/tx/',
                'links': {
                    'official': {
                        'href': 'https://qoge.org',
                        'title': 'qoge.org',
                    },
                    'exp': {
                        'href': 'https://explorer.qoge.org',
                        'title': 'Explorer',
                    },
                },
            },
        )
        FeesAndLimits.objects.update_or_create(
            currency=QOGE,
            defaults={
                'limits_deposit_min': 0.00020000,
                'limits_deposit_max': 100,
                'limits_withdrawal_min': 0.00020000,
                'limits_withdrawal_max': 5,
                'limits_order_min': 0.00030000,
                'limits_order_max': 5.00000000,
                'limits_code_max': 100.00000000,
                'limits_accumulation_min': 0.00020000,
                'fee_deposit_address': 0,
                'fee_deposit_code': 0,
                'fee_withdrawal_code': 0,
                'fee_order_limits': 0.00100000,
                'fee_order_market': 0.00200000,
                'fee_exchange_value': 0.00200000,
            },
        )
        WithdrawalFee.objects.update_or_create(
            currency=QOGE,
            blockchain_currency=QOGE,
            defaults={'address_fee': 0.00000001},
        )

        pair, _ = Pair.objects.get_or_create(
            id=QOGE_USDT_ID,
            defaults={'base': 'QOGE', 'quote': 'USDT'},
        )
        PairSettings.objects.update_or_create(
            pair=pair,
            defaults={
                'is_enabled': True,
                'is_autoorders_enabled': True,
                'price_source': PairSettings.PRICE_SOURCE_CUSTOM,
                'custom_price': 0.0005,
                'deviation': 0.99000000,
                'precisions': ['100', '10', '1', '0.1', '0.01'],
            },
        )

        usdt_qoge, _ = Pair.objects.get_or_create(
            id=USDT_QOGE_ID,
            defaults={'base': 'USDT', 'quote': 'QOGE'},
        )
        PairSettings.objects.update_or_create(
            pair=usdt_qoge,
            defaults={
                'is_enabled': True,
                'is_autoorders_enabled': False,
                'price_source': PairSettings.PRICE_SOURCE_CUSTOM,
                'custom_price': 2000,
                'deviation': 0.99000000,
                'precisions': ['1000', '100', '10', '1', '0.1'],
            },
        )

        bot = User.objects.filter(username='bot1@bot.com').first()
        if bot:
            BotConfig.objects.update_or_create(
                name='QOGE-USDT',
                defaults={
                    'pair': pair,
                    'user': bot,
                    'strategy': BotConfig.TRADE_STRATEGY_DRAW,
                    'instant_match': True,
                    'ohlc_period': 5,
                    'loop_period_random': True,
                    'min_period': 75,
                    'max_period': 280,
                    'ext_price_delta': 0,
                    'min_order_quantity': 0.001,
                    'max_order_quantity': 0.05,
                    'low_orders_max_match_size': 0.0029,
                    'low_orders_spread_size': 200,
                    'low_orders_min_order_size': 0.0003,
                    'enabled': True,
                },
            )

        try:
            service = QOGECoinService()
            last_processed, _ = LastProcessedBlock.objects.get_or_create(currency=QOGE_CURRENCY)
            last_processed.block_id = service.get_current_block_id()
            last_processed.save()
            self.stdout.write('Last processed QOGE block set to {}'.format(last_processed.block_id))
        except Exception as exc:
            self.stdout.write(self.style.WARNING('Could not query qogecoind: {}'.format(exc)))

        if options.get('keeper'):
            if Keeper.objects.filter(currency=QOGE_CURRENCY).exists():
                self.stdout.write('QOGE keeper already exists')
            else:
                info, keeper = generate_qoge_multisig_keeper(log)
                self.stdout.write('QOGE keeper {}'.format(keeper.user_wallet.address))
                self.stdout.write(str(info))

        DisabledCoin.objects.update_or_create(
            currency=BTC,
            defaults={
                'disable_all': True,
                'disable_stack': True,
                'disable_pairs': True,
                'disable_exchange': True,
                'disable_withdrawals': True,
                'disable_topups': True,
            },
        )
        try:
            btc_usdt = Pair.get('BTC-USDT')
            PairSettings.objects.filter(pair=btc_usdt).update(
                is_enabled=False,
                is_autoorders_enabled=False,
            )
            BotConfig.objects.filter(name='BTC-USDT').update(enabled=False)
        except Exception:
            pass

        self.stdout.write(self.style.SUCCESS('QOGE coin, QOGE-USDT, and USDT-QOGE pairs are registered'))
        self.stdout.write('BTC is disabled. Custom prices: QOGE-USDT 0.0005, USDT-QOGE 2000.')
