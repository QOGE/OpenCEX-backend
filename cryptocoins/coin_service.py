import logging
import os
from collections import defaultdict, namedtuple
from decimal import Decimal
from typing import List, Optional, Tuple

import cachetools.func
import requests
from bitcoinrpc.authproxy import AuthServiceProxy
from cryptos import entropy_to_words
from cryptos.coins.base import BaseSyncCoin
from django.conf import settings
from django.db import transaction
from django.db.models import QuerySet

from core.consts.currencies import BlockchainAccount
from core.currency import Currency
from core.models.cryptocoins import UserWallet
from core.models.inouts.wallet import WalletTransactions
from core.models.inouts.withdrawal import WithdrawalRequest
from core.utils.inouts import get_min_accumulation_balance, get_keeper_accumulation_balance_limit
from core.utils.inouts import get_withdrawal_fee
from cryptocoins.accumulation_manager import AccumulationManager
from cryptocoins.exceptions import CoinServiceError, TransferAmountLowError, SignTxError
from cryptocoins.models.keeper import Keeper
from cryptocoins.models.scoring import ScoringSettings, TransactionInputScore
from cryptocoins.utils import commons
from cryptocoins.utils.btc import pubkey_to_address
from lib.cipher import AESCoderDecoder
from lib.helpers import to_decimal
from lib.utils import memcache_lock

TxOutput = namedtuple('TxOutput', ['address', 'amount'])


class CoinServiceBase:
    CURRENCY: Optional[Currency] = None
    cold_wallet_address: Optional[str] = None
    default_block_id_delta = 100

    def __init__(self) -> None:
        self.log = logging.getLogger(f'{self.__module__}.{self.__class__.__name__}')

        if self.currency is None:
            raise ValueError('currency must be set')

    @property
    def withdrawal_fee(self):
        """
        FeesAndLimits.get_fee by Currency
        """
        return get_withdrawal_fee(self.currency)

    @property
    def currency(self) -> Currency:
        """
        Currency
        """
        if self.CURRENCY is None:
            raise NotImplementedError
        return commons.ensure_currency(self.CURRENCY)

    @property
    def min_accumulation_balance(self):
        """
        FeesAndLimits.get_limit(ACCUMULATION, MIN_VALUE) by Currency
        """
        return get_min_accumulation_balance(self.currency)

    @property
    def keeper_accumulation_balance_limit(self):
        """
        FeesAndLimits.get_limit(ACCUMULATION, KEEPER) by Currency
        """
        return get_keeper_accumulation_balance_limit(self.currency)

    def get_current_block_id(self):
        """
        current block_id from blockchain
        """
        raise NotImplementedError

    def get_block_hash(self, block_id):
        """
        canonical block hash at height
        """
        raise NotImplementedError

    def get_block_header(self, block_hash):
        """
        block header by hash (confirmations, height, previousblockhash)
        """
        raise NotImplementedError

    def get_tx_confirmations(self, tx_hash):
        """
        confirmation count for a transaction, or None if this chain cannot check
        """
        return None

    @property
    def required_confirmations(self) -> int:
        mapping = getattr(settings, 'CRYPTO_TOPUP_REQUIRED_CONFIRMATIONS', None) or {}
        try:
            value = mapping[self.currency.code]
        except (KeyError, TypeError, AttributeError):
            value = getattr(settings, 'CRYPTO_TOPUP_REQUIRED_CONFIRMATIONS_COUNT', 6)
        try:
            value = int(value)
        except (TypeError, ValueError):
            value = 6
        return max(1, value)

    @property
    def reorg_max_rewind(self) -> int:
        try:
            value = int(getattr(settings, 'CRYPTO_REORG_MAX_REWIND', 1000) or 1000)
        except (TypeError, ValueError):
            value = 1000
        return max(self.required_confirmations, value)

    def confirmed_scan_height(self, tip: int) -> int:
        return tip - self.required_confirmations + 1

    def get_block_transactions(self, block_id):
        """
        return transaction list by block_id
        """
        raise NotImplementedError

    def check_tx_for_deposit(self, tx_data, block_id=None, block_hash=None):
        """
        check tx_data for deposit
        """
        raise NotImplementedError

    def get_wallet_balance(self, address) -> Decimal:
        """
        get wallet balance by address
        """
        raise NotImplementedError

    def get_keeper_balance(self) -> Decimal:
        """
        get keeper balance
        """
        raise NotImplementedError

    def accumulate(self):
        """
        try accumulate user transactions
        """
        raise NotImplementedError

    def process_withdrawals(self):
        """
        Process withdrawal requests
        """
        raise NotImplementedError

    def create_new_wallet(self, label='', addr_import: bool = True):
        """
        Create new wallet address and key and import address to node
        """
        raise NotImplementedError

    def create_userwallet(self, user_id, is_new=False):
        """
        Create new UserWallet
        """
        wallet = UserWallet.objects.filter(
            user_id=user_id,
            currency=self.currency,
            merchant=False,
            is_old=False,
        ).first()

        if not is_new and wallet is not None:
            self.log.info('Found %s wallet for user %s', self.currency.code, user_id)
            return wallet

        self.log.info('Create new %s wallet for user %s', self.currency.code, user_id)
        wallet_account = self.create_new_wallet()
        wallet = UserWallet.objects.create(
            user_id=user_id,
            currency=self.currency,
            address=wallet_account.address,
            private_key=AESCoderDecoder(settings.CRYPTO_KEY).encrypt(
                wallet_account.private_key
            ),
            blockchain_currency=self.currency
        )

        return wallet

    @cachetools.func.ttl_cache(ttl=5)
    def get_keeper_wallet(self) -> BlockchainAccount:
        keeper = Keeper.objects.filter(
            currency=self.currency,
        ).select_related(
            'user_wallet',
        ).only(
            'user_wallet__address',
            'user_wallet__private_key',
        ).first()

        if keeper is None:
            raise ValueError(f'Keeper for {self.currency.code} not found')

        return BlockchainAccount(
            address=keeper.user_wallet.address,
            private_key=AESCoderDecoder(settings.CRYPTO_KEY).decrypt(
                keeper.user_wallet.private_key
            ),
            redeem_script=keeper.extra.get('redeem_script')
        )

    @cachetools.func.ttl_cache(ttl=5)
    def get_users_addresses(self, exclude_blocked=False):
        qs = UserWallet.objects.filter(
            currency=self.currency,
            keeper=None,
        ).exclude(
            user_id=None,
        ).values_list(
            'address',
            flat=True,
        )
        if exclude_blocked:
            qs = qs.exclude(block_type=UserWallet.BLOCK_TYPE_DEPOSIT_AND_ACCUMULATION)
        return list(qs)

    def get_accumulation_ready_wallet_transactions(self) -> List[WalletTransactions]:
        return WalletTransactions.get_ready_for_accumulation(self.currency)

    def get_external_accumulation_ready_wallet_transactions(self) -> List[WalletTransactions]:
        return WalletTransactions.get_ready_for_external_accumulation(self.currency)

    def process_new_blocks(self):
        """
        Process blocks that have reached the confirmation depth. Pin the
        scanner cursor to a canonical hash so a reorg rewinds instead of
        skipping the fork.
        """
        with memcache_lock(f'{self.currency}_lock') as acquired:
            if acquired:
                self._process_new_blocks_locked()
                return
        self.log.warning('%s process_new_blocks task already works', self.currency)

    def _process_new_blocks_locked(self):
        tip = self.get_current_block_id()
        safe_height = self.confirmed_scan_height(tip)
        if safe_height < 0:
            self.log.debug(
                'Tip %s too low for %s confirmations',
                tip, self.required_confirmations,
            )
            return

        last = commons.load_last_processed_block(self.currency)
        if last is None:
            last_id = max(0, safe_height - self.default_block_id_delta)
            last_hash = None
        else:
            last_id = last.block_id
            last_hash = last.block_hash or None

        if last_id > safe_height:
            pin_hash = self._block_hash_or_none(safe_height)
            commons.store_last_processed_block_id(
                self.currency, safe_height, block_hash=pin_hash, allow_rewind=True,
            )
            self.log.info(
                '%s scanner cursor pinned to confirmed height %s (%s confirmations)',
                self.currency.code, safe_height, self.required_confirmations,
            )
            last_id = safe_height
            last_hash = pin_hash

        last_id = self._rewind_reorged_cursor(last_id, last_hash)

        if last_id >= safe_height:
            self.log.debug('Nothing to process')
            return

        blocks_to_process = list(range(last_id + 1, safe_height + 1))
        if len(blocks_to_process) > self.default_block_id_delta:
            blocks_to_process = blocks_to_process[:self.default_block_id_delta]

        self.log.info(
            'Need to process %s blocks count: %s (tip %s, confirmed height %s)',
            self.currency.code, len(blocks_to_process), tip, safe_height,
        )

        for block_id in blocks_to_process:
            self.log.info('Processing %s %s block', self.currency.code, block_id)
            self.process_block(block_id)
            commons.store_last_processed_block_id(
                self.currency,
                block_id,
                block_hash=self._block_hash_or_none(block_id),
            )

    def _block_hash_or_none(self, block_id):
        try:
            return self.get_block_hash(block_id)
        except NotImplementedError:
            return None
        except Exception as exc:
            self.log.warning(
                'Unable to read %s hash at %s: %s',
                self.currency.code, block_id, exc,
            )
            return None

    def _rewind_reorged_cursor(self, last_id, last_hash):
        if not last_hash:
            pin_hash = self._block_hash_or_none(last_id)
            if pin_hash:
                commons.store_last_processed_block_id(
                    self.currency, last_id, block_hash=pin_hash,
                )
            return last_id

        canonical = self._block_hash_or_none(last_id)
        if canonical and canonical == last_hash:
            return last_id

        self.log.warning(
            'Reorg or missing block for %s at height %s (stored %s, chain %s)',
            self.currency.code, last_id, last_hash, canonical,
        )

        rewind_to = self._common_ancestor_height(last_id, last_hash)
        if rewind_to is None:
            rewind_to = max(0, last_id - self.reorg_max_rewind)

        self._quarantine_orphaned_deposits(after_height=rewind_to)
        commons.store_last_processed_block_id(
            self.currency,
            rewind_to,
            block_hash=self._block_hash_or_none(rewind_to),
            allow_rewind=True,
        )
        self.log.warning('Rewound %s scanner cursor to %s', self.currency.code, rewind_to)
        return rewind_to

    def _common_ancestor_height(self, last_id, last_hash):
        try:
            header = self.get_block_header(last_hash)
        except NotImplementedError:
            return None
        except Exception as exc:
            self.log.info('Header lookup failed for %s %s: %s', self.currency.code, last_hash, exc)
            return None

        confirmations = int(header.get('confirmations') or 0)
        header_height = int(header.get('height', last_id) or last_id)
        if confirmations > 0 and header_height == last_id:
            return last_id

        prev = header.get('previousblockhash')
        steps = 0
        while prev and steps < self.reorg_max_rewind:
            try:
                header = self.get_block_header(prev)
            except Exception:
                break
            confirmations = int(header.get('confirmations') or 0)
            height = int(header.get('height') or 0)
            if confirmations > 0:
                return height
            prev = header.get('previousblockhash')
            steps += 1
        return None

    def _quarantine_orphaned_deposits(self, after_height):
        qs = WalletTransactions.objects.filter(
            currency=self.currency,
            status=WalletTransactions.STATUS_NOT_SET,
            block_id__gt=after_height,
        )
        for wallet_transaction in qs:
            confs = self.get_tx_confirmations(wallet_transaction.tx_hash)
            if confs is None or confs != 0:
                continue
            outcome = wallet_transaction.mark_orphaned()
            self.log.error(
                'Orphaned %s deposit %s tx %s: %s',
                self.currency.code,
                wallet_transaction.id,
                wallet_transaction.tx_hash,
                outcome,
            )

    def deposit_still_confirmed(self, wallet_transaction) -> bool:
        confs = self.get_tx_confirmations(wallet_transaction.tx_hash)
        if confs is None or confs < 0:
            return True
        return confs >= self.required_confirmations

    def process_block(self, block_id):
        """
        Run check_tx_for_deposit for transaction by block_id
        """
        block_hash = self._block_hash_or_none(block_id)
        for tx_data in self.get_block_transactions(block_id):
            self.check_tx_for_deposit(tx_data, block_id=block_id, block_hash=block_hash)

    def get_withdrawal_requests(self) -> QuerySet:
        """
        Get WithdrawalRequest by Currency
        """
        return WithdrawalRequest.crypto_to_process(currency=self.currency).order_by('created')

    def get_sufficient_withdrawal_requests(self, limit: Decimal, withdrawal_requests: Optional[QuerySet] = None):
        """
        Get withdrawal requests which keeper has sufficient balance to process
        """
        if withdrawal_requests is None:
            withdrawal_requests = self.get_withdrawal_requests()
        result = []
        total_amount = to_decimal(0)

        for item in withdrawal_requests:
            if (total_amount + item.amount) <= limit:
                total_amount += item.amount
                result.append(item)

            else:
                break

        return result

    def process_deposit(self, tx_hash, address, amount, is_scoring_ok=None, block_id=None, block_hash=None):
        self.log.info('Processing deposit %s %s %s %s', self.currency, amount, address, tx_hash)
        wallet = UserWallet.objects.filter(
            currency=self.currency,
            address=address,
        ).first()

        if wallet is None:
            self.log.error('Wallet %s does not exist', address)
            return

        wallet_transaction, created = WalletTransactions.get_or_create_for_chain_tx(
            wallet_id=wallet.id,
            tx_hash=tx_hash,
            currency=self.currency,
            amount=amount,
            block_id=block_id,
            block_hash=block_hash,
        )

        if not created:
            self.log.warning('Deposit %s %s for address %s already been processed, skipping',
                             self.currency.code, amount, address)
            return

        self.log.info('Deposit %s %s for address %s processed', self.currency.code, amount, address)

    def check_for_scoring(self):
        waiting_for_scoring = AccumulationManager.get_waiting_for_kyt_check(self.currency.code)
        for wallet_transaction in waiting_for_scoring:
            confs = self.get_tx_confirmations(wallet_transaction.tx_hash)
            if confs is None:
                wallet_transaction.check_scoring()
                continue
            if confs < 0:
                self.log.warning(
                    'Skip scoring credit for %s deposit %s; confirmations unknown',
                    self.currency.code, wallet_transaction.id,
                )
                continue
            if confs < self.required_confirmations:
                if confs == 0:
                    outcome = wallet_transaction.mark_orphaned()
                    self.log.error(
                        'Unconfirmed/orphaned %s deposit %s tx %s: %s',
                        self.currency.code,
                        wallet_transaction.id,
                        wallet_transaction.tx_hash,
                        outcome,
                    )
                else:
                    self.log.info(
                        'Skip scoring credit for %s deposit %s; confirmations %s/%s',
                        self.currency.code,
                        wallet_transaction.id,
                        confs,
                        self.required_confirmations,
                    )
                continue
            wallet_transaction.check_scoring()


class BitCoreCoinServiceBase(CoinServiceBase):
    """
    Base service for BitCore based coins e.g. BTC, LTC, BCH
    """
    node_config: Optional[dict] = None
    CRYPTO_COIN: Optional[BaseSyncCoin] = None

    def __init__(self) -> None:
        super().__init__()

        if self.node_config is None:
            raise ValueError('node_config attribute must be set')

        if self.cold_wallet_address is None:
            raise ValueError('cold_wallet_address attribute must be set')

        if self.withdrawal_fee is None:
            raise ValueError('withdrawal_fee attribute must be set')

        self.rpc_url = 'http://{username}:{password}@{host}:{port}'.format(**self.node_config, timeout=60)

    def get_transfer_fee(self, size):
        raise NotImplementedError

    @property
    def crypto_coin(self) -> BaseSyncCoin:
        if self.CRYPTO_COIN is None:
            raise NotImplementedError

        return self.CRYPTO_COIN

    @property
    def rpc(self) -> AuthServiceProxy:
        """
        Recreate instance each time due sharing problems
        """
        return AuthServiceProxy(self.rpc_url)

    @property
    @cachetools.func.ttl_cache(ttl=5)
    def node_version(self) -> int:
        return self.rpc.getnetworkinfo()['version']

    @cachetools.func.ttl_cache(ttl=5)
    def get_users_private_keys(self):
        return list(UserWallet.objects.filter(
            currency=self.currency,
            keeper=None,
        ).exclude(
            private_key='-',
        ).values_list(
            'private_key',
            flat=True,
        ))

    def import_address(self, address: str, label: str = ''):
        self.rpc.importaddress(address, label, False)
        self.log.info('Address %s %s imported', self.currency, address)

    def create_new_wallet(self, label: str = '', addr_import: bool = True) -> BlockchainAccount:
        """
        Create new wallet address and key and import address to node
        """
        self.log.info('Create new %s wallet', self.currency.code)

        words = entropy_to_words(os.urandom(20))
        if getattr(settings, 'BTC_ADDRESS_LEGACY', False):
            wallet = self.crypto_coin.wallet(words)
        else:
            wallet = self.crypto_coin.p2wpkh_wallet(words)

        address = wallet.new_receiving_address()
        private_key = wallet.privkey(address)
        public_key = self.crypto_coin.privtopub(private_key)

        if isinstance(private_key, bytes):
            private_key = private_key.decode('utf-8')

        if addr_import:
            self.import_address(address, label=label)

        return BlockchainAccount(
            address=address,
            private_key=private_key,
            public_key=public_key,
            redeem_script=None,
        )

    def get_unspent(self, addresses: Optional[list] = None):
        """
        get listunspent by addresses
        """
        if addresses is None:
            addresses = []

        return self.rpc.listunspent(1, 9999999, addresses)

    def get_users_unspent(self, exclude_blocked=False):
        """
        get unspent by user addresses
        """
        addresses = self.get_users_addresses(exclude_blocked)

        if not addresses:
            self.log.info('Have no user addresses for %s', self.currency.code)
            return []

        return self.get_unspent(addresses=addresses)

    def get_wallet_balance(self, address: str) -> Decimal:
        unspent = self.get_unspent(addresses=[address])
        return self.get_balance_from_unspent(unspent)

    def get_users_total_balance(self) -> Decimal:
        """
        get balance by user addresses
        """
        unspent = self.get_users_unspent()
        return self.get_balance_from_unspent(unspent)

    def get_keeper_balance(self) -> Decimal:
        keeper_wallet = self.get_keeper_wallet()
        return self.get_wallet_balance(keeper_wallet.address)

    def accumulate(self):
        self.log.info('Starting accumulation: %s', self.currency.code)

        inputs = self.get_users_unspent()
        # check if spendable
        checked_inputs = []
        for item in inputs:
            result = self.rpc.gettxout(
                item['txid'],
                item['vout'],
            )
            if not result:
                continue

            checked_inputs.append(item)

        # total_amount = self.get_users_total_balance()
        total_amount = sum([to_decimal(i['amount']) for i in checked_inputs])

        if total_amount < self.min_accumulation_balance or total_amount == 0:
            self.log.info('Total balance too low for accumulation: %s %s',
                          self.currency.code, total_amount)
            return

        self.log.info('Total accumulation balance: %s %s', self.currency.code, total_amount)

        private_keys = list(UserWallet.objects.filter(
            currency=self.currency,
            address__in=list(i['address'] for i in checked_inputs)
        ).values_list(
            'private_key',
            flat=True,
        ))
        private_keys = [AESCoderDecoder(settings.CRYPTO_KEY).decrypt(i) for i in private_keys]

        accumulation_address = self.get_accumulation_address(total_amount)

        self.transfer_to(checked_inputs, accumulation_address, total_amount, private_keys)

    def get_tx_size(self, inputs: list, outputs: dict, private_keys: list):
        """
        get transaction size in byte
        """
        tx_hex = self.rpc.createrawtransaction(inputs, outputs)
        signed_tx_without_fee = self._sign_transaction(tx_hex, private_keys)

        if not signed_tx_without_fee['complete']:
            self.log.error('Unable to sign TX')
            raise SignTxError

        tx_decode = self.rpc.decoderawtransaction(signed_tx_without_fee['hex'])
        return tx_decode.get('size')

    def sign_transfer(self, inputs: list, outputs: dict, private_keys: list):
        """
        Sign a transfer without broadcasting. Returns (txid, raw_hex).
        """
        self.log.info('Make transfer %s in -> %s out', len(inputs), len(outputs))
        tx_hex = self.rpc.createrawtransaction(inputs, outputs)
        signed_tx = self._sign_transaction(tx_hex, private_keys)

        if not signed_tx['complete']:
            self.log.error('Unable to sign TX')
            return None, None

        raw_hex = signed_tx['hex']
        return self.txid_from_raw(raw_hex), raw_hex

    def transfer(self, inputs: list, outputs: dict, private_keys: list):
        """
        transfer transaction
        """
        tx_id, raw_hex = self.sign_transfer(inputs, outputs, private_keys)
        if not tx_id or not raw_hex:
            return
        return self.broadcast_raw_tx(raw_hex)

    def transfer_to(self, inputs: list, address: str, amount: Decimal, private_keys: list) -> Tuple[str, Decimal]:
        """
        transfer tx to address
        """
        pre_outputs = {
            address: amount,
        }
        tx_size = self.get_tx_size(inputs, pre_outputs, private_keys)
        transfer_fee = self.get_transfer_fee(tx_size)

        transfer_amount = amount - transfer_fee

        if transfer_amount <= 0:
            self.log.info('Transfer amount too low after fee apply: %s', transfer_amount)
            raise TransferAmountLowError

        outputs = {
            address: transfer_amount
        }

        tx_hex = self.rpc.createrawtransaction(inputs, pre_outputs)
        signed_tx = self._sign_transaction(tx_hex, private_keys)

        if not signed_tx['complete']:
            self.log.error('Unable to sign TX')
            raise SignTxError

        self.log.info('Make transfer %s in -> %s out', len(inputs), len(outputs))
        tx_id = self.rpc.sendrawtransaction(signed_tx['hex'])
        self.log.info('Sent TX: %s', tx_id)

        return tx_id, transfer_amount

    def get_block_transactions(self, block_id):
        block_hash = self.rpc.getblockhash(block_id)
        return self.rpc.getblock(block_hash, 2)['tx']

    def check_tx_for_deposit(self, tx_data, block_id=None, block_hash=None):
        tx_id = tx_data['txid']
        outputs_amount = defaultdict(Decimal)

        # get total amount for each address
        for addr, amount in self.parse_tx_outputs(tx_data):
            outputs_amount[addr] += amount

        # process only our addresses
        for addr, amount in outputs_amount.items():
            if addr not in self.get_users_addresses():
                continue

            self.process_deposit(
                tx_id, addr, amount, block_id=block_id, block_hash=block_hash,
            )

    def get_current_block_id(self):
        return self.rpc.getblockcount()

    def get_block_hash(self, block_id):
        return self.rpc.getblockhash(int(block_id))

    def get_block_header(self, block_hash):
        return self.rpc.getblockheader(block_hash)

    def get_tx_confirmations(self, tx_hash):
        try:
            info = self.rpc.getrawtransaction(tx_hash, True)
        except Exception as exc:
            if self._is_missing_tx_error(exc):
                self.log.info(
                    'TX %s not in %s chain: %s',
                    tx_hash, self.currency.code, exc,
                )
                return 0
            self.log.warning(
                'Unable to query %s tx %s: %s',
                self.currency.code, tx_hash, exc,
            )
            return -1
        try:
            return int(info.get('confirmations') or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _is_missing_tx_error(exc) -> bool:
        error = getattr(exc, 'error', None)
        if isinstance(error, dict) and error.get('code') == -5:
            return True
        message = str(exc).lower()
        return (
            'no such mempool' in message
            or 'no such transaction' in message
            or 'not found' in message
        )

    def get_last_network_block_id(self):
        """
        Get last block_id from blockchain
        """
        response = requests.get('https://blockchain.info/latestblock', timeout=30)
        response.raise_for_status()

        return response.json().get('height', 0)
        # return self.rpc.getblockchaininfo().get('headers', 0)

    def process_withdrawals(self, *args, **kwargs):
        """
        Process withdrawal requests.

        Claim eligible rows, persist the signed payout, then broadcast so a
        crash or retry cannot build a second transaction for the same debit.
        """
        self.log.info('Processing %s withdrawals', self.currency.code)
        lock_id = f'{self.currency}_payouts_lock'
        with memcache_lock(lock_id, lock_id) as acquired:
            if not acquired:
                raise CoinServiceError(
                    f'{self.currency.code} payout processing already in progress'
                )
            self._process_withdrawals_locked(*args, **kwargs)

    def _process_withdrawals_locked(self, *args, **kwargs):
        unsigned_pending, recovered_count = self._recover_signed_payouts()

        keeper_wallet = self.get_keeper_wallet()
        keeper_unspent = self.get_unspent(addresses=[keeper_wallet.address])
        keeper_balance = self.get_balance_from_unspent(keeper_unspent)
        self.log.info('%s keeper balance: %s', self.currency.code, keeper_balance)

        selected_withdrawals = kwargs.get('selected_withdrawals')
        if selected_withdrawals is None:
            created_or_selected = list(self.get_withdrawal_requests())
        else:
            created_or_selected = list(selected_withdrawals)

        seen = {item.id for item in unsigned_pending}
        candidates = unsigned_pending + [
            item for item in created_or_selected if item.id not in seen
        ]
        sufficient_requests = self.get_sufficient_withdrawal_requests(
            keeper_balance, candidates
        )

        if not sufficient_requests:
            if recovered_count:
                self.log.info(
                    'Recovered %s %s payout request(s); nothing new to withdraw',
                    recovered_count, self.currency.code,
                )
                return
            self.log.debug('Nothing to withdraw or %s keeper balance too low', self.currency.code)
            raise CoinServiceError(f'Nothing to withdraw or {self.currency.code} keeper balance too low')

        self.log.info('Enough balance for requests count: %s', len(sufficient_requests))

        sufficient_requests = self._claim_withdrawal_requests(sufficient_requests)

        outputs = []
        for item in sufficient_requests:
            final_amount = item.amount - self.withdrawal_fee
            dest_address = item.data['destination']
            outputs.append(TxOutput(address=dest_address, amount=final_amount))

        tx_id, raw_hex = self.build_keeper_tx(
            outputs,
            *args,
            **kwargs,
            keeper_wallet=keeper_wallet,
            keeper_unspent=keeper_unspent,
        )

        if not tx_id or not raw_hex:
            self.log.error('Unable to withdraw %s', self.currency.code)
            raise CoinServiceError(f'Unable to withdraw {self.currency.code}')

        self._persist_payout_intent(sufficient_requests, tx_id, raw_hex)
        self.broadcast_raw_tx(raw_hex)
        self._complete_payout_batch(sufficient_requests, tx_id)
        self.log.info('Withdrawal completed')

    def _pending_payout_requests(self):
        return list(
            WithdrawalRequest.objects.filter(
                currency=self.currency,
                state=WithdrawalRequest.STATE_PENDING,
                approved=True,
                confirmed=True,
            ).order_by('created')
        )

    def _recover_signed_payouts(self):
        """Rebroadcast persisted payout hex and complete those requests."""
        unsigned = []
        groups = defaultdict(list)
        for item in self._pending_payout_requests():
            raw_hex = (item.data or {}).get('payout_hex')
            if raw_hex:
                groups[raw_hex].append(item)
            else:
                unsigned.append(item)

        recovered_count = 0
        for raw_hex, items in groups.items():
            tx_id = items[0].txid or self.txid_from_raw(raw_hex)
            self.broadcast_raw_tx(raw_hex)
            self._complete_payout_batch(items, tx_id)
            recovered_count += len(items)
            self.log.info(
                'Recovered %s payout %s for %s request(s)',
                self.currency.code, tx_id, len(items),
            )
        return unsigned, recovered_count

    def _claim_withdrawal_requests(self, requests):
        created_ids = [
            item.id for item in requests
            if item.state == WithdrawalRequest.STATE_CREATED
        ]
        if not created_ids:
            return requests

        with transaction.atomic():
            locked_ids = set(
                WithdrawalRequest.objects.select_for_update()
                .filter(
                    id__in=created_ids,
                    state=WithdrawalRequest.STATE_CREATED,
                    approved=True,
                    confirmed=True,
                )
                .values_list('id', flat=True)
            )
            if locked_ids != set(created_ids):
                raise CoinServiceError(
                    'Withdrawal requests already claimed or no longer eligible'
                )
            updated = WithdrawalRequest.objects.filter(
                id__in=created_ids,
                state=WithdrawalRequest.STATE_CREATED,
            ).update(state=WithdrawalRequest.STATE_PENDING)
            if updated != len(created_ids):
                raise CoinServiceError(
                    'Withdrawal requests already claimed or no longer eligible'
                )

        for item in requests:
            if item.state == WithdrawalRequest.STATE_CREATED:
                item.state = WithdrawalRequest.STATE_PENDING
        return requests

    def _persist_payout_intent(self, requests, tx_id, raw_hex):
        with transaction.atomic():
            for item in requests:
                data = dict(item.data or {})
                data['payout_hex'] = raw_hex
                item.data = data
                item.txid = tx_id
                item.our_fee_amount = self.withdrawal_fee
                item.state = WithdrawalRequest.STATE_PENDING
                item.save(update_fields=[
                    'data', 'txid', 'our_fee_amount', 'state', 'updated',
                ])

    def _complete_payout_batch(self, requests, tx_id):
        for item in requests:
            if item.txid != tx_id:
                item.txid = tx_id
                item.save(update_fields=['txid', 'updated'])
            if item.state != WithdrawalRequest.STATE_PENDING:
                item.refresh_from_db()
            item.complete()

    def txid_from_raw(self, raw_hex: str) -> str:
        decoded = self.rpc.decoderawtransaction(raw_hex)
        tx_id = decoded.get('txid')
        if not tx_id:
            raise CoinServiceError('Unable to decode payout transaction id')
        return tx_id

    @staticmethod
    def _is_already_broadcast(exc) -> bool:
        message = ''
        error = getattr(exc, 'error', None)
        if isinstance(error, dict):
            message = str(error.get('message') or '')
        if not message:
            message = str(exc)
        message = message.lower()
        return any(token in message for token in (
            'already in block chain',
            'already in blockchain',
            'txn-already-in-mempool',
            'txn-already-known',
            'already have',
        ))

    def _txid_is_known(self, tx_id: str) -> bool:
        try:
            raw = self.rpc.getrawtransaction(tx_id)
            return bool(raw)
        except Exception:
            return False

    def broadcast_raw_tx(self, raw_hex: str) -> str:
        try:
            tx_id = self.rpc.sendrawtransaction(raw_hex)
            self.log.info('Sent TX: %s', tx_id)
            return tx_id
        except Exception as exc:
            if self._is_already_broadcast(exc):
                tx_id = self.txid_from_raw(raw_hex)
                self.log.info('TX already known: %s', tx_id)
                return tx_id
            tx_id = self.txid_from_raw(raw_hex)
            if self._txid_is_known(tx_id):
                self.log.info('TX already in node: %s', tx_id)
                return tx_id
            raise

    def send_from_keeper(self, outputs, *args, **kwargs):
        """
        send transaction from keeper
        """
        tx_id, raw_hex = self.build_keeper_tx(outputs, *args, **kwargs)
        if not tx_id or not raw_hex:
            return None
        return self.broadcast_raw_tx(raw_hex)

    def build_keeper_tx(self, outputs, *args, **kwargs):
        """
        Sign a keeper payout without broadcasting. Returns (txid, raw_hex).
        """
        keeper_wallet = kwargs.get('keeper_wallet') or self.get_keeper_wallet()
        keeper_unspent = kwargs.get('keeper_unspent') or self.get_unspent(addresses=[keeper_wallet.address])
        password = kwargs.get('password')
        keeper_balance = self.get_balance_from_unspent(keeper_unspent)

        private_key = keeper_wallet.private_key
        if password:
            private_key = AESCoderDecoder(password).decrypt(private_key)

        tx_outputs = {}
        for item in outputs:
            if item.address in tx_outputs:
                tx_outputs[item.address] += to_decimal(item.amount)
            else:
                tx_outputs[item.address] = to_decimal(item.amount)

        # need to fill chargeback amount later
        tx_outputs[keeper_wallet.address] = 0

        estimated_tx_size = self.get_tx_size(
            keeper_unspent,
            tx_outputs,
            [private_key],
        )
        transfer_fee = self.get_transfer_fee(estimated_tx_size)

        outputs_sum = sum(tx_outputs.values())
        self.log.info('%s withdrawals outputs sum: %s', self.currency.code, outputs_sum)

        chargeback_amount = keeper_balance - transfer_fee - outputs_sum
        self.log.info('%s chargeback amount: %s', self.currency.code, chargeback_amount)

        if chargeback_amount < 0:
            self.log.error('Unable to process withdrawals, chargeback after fee less than 0')
            raise CoinServiceError('Unable to process withdrawals, chargeback after fee less than 0')

        tx_outputs[keeper_wallet.address] = chargeback_amount

        return self.sign_transfer(
            inputs=keeper_unspent,
            outputs=tx_outputs,
            private_keys=[private_key]
        )

    def _sign_transaction(self, tx_hex, private_keys):
        # backward compatibility
        if self.node_version >= 170000:
            return self.rpc.signrawtransactionwithkey(tx_hex, private_keys, [])

        return self.rpc.signrawtransaction(tx_hex, [], private_keys)

    @staticmethod
    def estimate_tx_size(inputs_num: int, outputs_num: int) -> int:
        return inputs_num * 148 + outputs_num * 34 + 10

    @staticmethod
    def estimate_script_sig_size(required_num: int, address_num: int) -> int:
        return 5 + 74 * address_num + 34 * required_num

    @staticmethod
    def parse_tx_outputs(tx_data):
        outputs = []
        for item in tx_data['vout']:
            address = None
            if 'addresses' in item['scriptPubKey']:
                address = item['scriptPubKey']['addresses'][0]
            if 'address' in item['scriptPubKey']:
                address = item['scriptPubKey']['address']
            if not address:
                continue
            outputs.append((
                address,
                item['value'],
            ))

        return outputs

    def parse_tx_inputs(self, tx_data):
        input_addresses = []
        for vin in tx_data['vin']:
            try:
                pubkey = vin['scriptSig'].get('asm', '').split(' ')[1]
                address = pubkey_to_address(pubkey)
                input_addresses.append(address)
            except BaseException:
                # skip
                pass
                # self.log.warning('[-] Error parsing tx ' + tx_data['txid'])
        return input_addresses

    @staticmethod
    def get_balance_from_unspent(unspent):
        return sum([to_decimal(i['amount']) for i in unspent])

    def skip_input(self, tx_hash, tx_amount):
        qs = TransactionInputScore.objects.filter(hash=tx_hash)
        if qs.exists():
            address_score_exists = qs.filter(
                deposit_made=True,
                accumulation_made=False
            ).exists()

            #  if hash exists, but without deposit
            if not address_score_exists:
                return True

        else:
            # if tx still in scoring process
            return ScoringSettings.need_to_check_score(tx_amount, self.currency.code)

    def get_accumulation_address(self, accumulation_amount):
        keeper_wallet = self.get_keeper_wallet()
        keeper_balance = self.get_wallet_balance(keeper_wallet.address)

        accumulation_address = self.cold_wallet_address

        if keeper_balance + accumulation_amount < self.keeper_accumulation_balance_limit:
            accumulation_address = keeper_wallet.address

        return accumulation_address
