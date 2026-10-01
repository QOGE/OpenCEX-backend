from collections import defaultdict
from decimal import Decimal
from urllib.parse import quote

from cryptos import apply_multisignatures, serialize
from django.conf import settings

from core.models.cryptocoins import UserWallet
from core.models.inouts.fees_and_limits import FeesAndLimits
from cryptocoins.cache import sat_per_byte_cache
from cryptocoins.coin_service import BitCoreCoinServiceBase
from cryptocoins.coins.qoge import QOGE_CURRENCY
from cryptocoins.coins.qoge.coin import QogeCoin
from cryptocoins.exceptions import CoinServiceError, TransferAmountLowError
from cryptocoins.models import AccumulationTransaction
from cryptocoins.models.accumulation_details import AccumulationDetails
from cryptocoins.models.scoring import ScoringSettings
from cryptocoins.scoring.manager import ScoreManager
from cryptocoins.tasks.scoring import process_deffered_deposit
from cryptocoins.utils.btc import btc2sat
from lib.cipher import AESCoderDecoder
from lib.helpers import to_decimal


class QOGECoinService(BitCoreCoinServiceBase):
    CURRENCY = QOGE_CURRENCY
    GAS_CURRENCY = settings.ETH_TX_GAS
    node_config = settings.NODES_CONFIG['qoge']
    cold_wallet_address = settings.QOGE_SAFE_ADDR
    const_fee = 0.00003
    CRYPTO_COIN = QogeCoin()

    def __init__(self):
        super().__init__()
        cfg = self.node_config or {}
        user = quote(str(cfg.get('username') or ''), safe='')
        password = quote(str(cfg.get('password') or ''), safe='')
        self.rpc_url = 'http://{user}:{password}@{host}:{port}'.format(
            user=user,
            password=password,
            host=cfg.get('host', 'localhost'),
            port=cfg.get('port', 8332),
        )
        wallet = cfg.get('wallet') or ''
        if wallet:
            self.rpc_url = '{}/wallet/{}'.format(self.rpc_url, wallet)

    def import_address(self, address: str, label: str = ''):
        """Watch-only import.

        This Qogecoin node is descriptor-only (no BDB). A wallet with private
        keys enabled rejects addr() descriptors; use a watch-only wallet
        (`createwallet disable_private_keys=true`) named QOGE_NODE_WALLET.
        """
        try:
            self.rpc.importaddress(address, label, False)
            self.log.info('Address %s %s imported', self.currency, address)
            return
        except Exception as exc:
            self.log.info('importaddress unavailable for %s (%s); using importdescriptors', address, exc)

        info = self.rpc.getdescriptorinfo('addr({})'.format(address))
        result = self.rpc.importdescriptors([{
            'desc': info['descriptor'],
            'timestamp': 'now',
            'label': label or address,
        }])
        if not result or not result[0].get('success'):
            raise CoinServiceError('Failed to import {}: {}'.format(address, result))
        self.log.info('Address %s %s imported via descriptor', self.currency, address)

    def create_new_wallet(self, label: str = '', addr_import: bool = True):
        """P2WPKH (bq1q...) by default; QOGE_ADDRESS_LEGACY=True for q... P2PKH."""
        import os
        from cryptos import entropy_to_words
        from core.consts.currencies import BlockchainAccount

        self.log.info('Create new %s wallet', self.currency.code)
        words = entropy_to_words(os.urandom(20))
        if getattr(settings, 'QOGE_ADDRESS_LEGACY', False):
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

    def get_last_network_block_id(self):
        info = self.rpc.getblockchaininfo()
        return info.get('blocks', 0)

    def get_transfer_fee(self, size):
        s_p_b = self.get_sat_per_byte()
        fee = to_decimal(size * s_p_b / 10 ** 8)
        self.log.info('Fee = %s Sat/b * %s bytes / 10**8 = %s QOGE', s_p_b, size, fee)
        return to_decimal(max(fee, to_decimal(self.const_fee)))

    def get_sat_per_byte(self):
        return sat_per_byte_cache.get('qogecoin', settings.SAT_PER_BYTES_MIN_LIMIT)

    def send_from_keeper(self, outputs, *args, **kwargs):
        """Build keeper payouts with node RPC so P2QPK destinations encode correctly."""
        private_key = kwargs.get('private_key')
        keeper_wallet = kwargs.get('keeper_wallet') or self.get_keeper_wallet()
        keeper_unspent = kwargs.get('keeper_unspent') or self.get_unspent(addresses=[keeper_wallet.address])
        keeper_balance = self.get_balance_from_unspent(keeper_unspent)

        tx_outputs = {}
        for item in outputs:
            if item.address in tx_outputs:
                tx_outputs[item.address] += to_decimal(item.amount)
            else:
                tx_outputs[item.address] = to_decimal(item.amount)

        tx_outputs[keeper_wallet.address] = to_decimal(0)

        vin = [{'txid': i['txid'], 'vout': i['vout']} for i in keeper_unspent]
        estimated_size = self._rpc_signed_tx_size(
            vin, tx_outputs, keeper_unspent, keeper_wallet.private_key, private_key, keeper_wallet.redeem_script
        )
        transfer_fee = self.get_transfer_fee(estimated_size)

        outputs_sum = sum(tx_outputs.values())
        self.log.info('%s withdrawals outputs sum: %s', self.currency.code, outputs_sum)

        chargeback_amount = keeper_balance - transfer_fee - outputs_sum
        self.log.info('%s chargeback amount: %s', self.currency.code, chargeback_amount)

        if chargeback_amount < 0:
            self.log.error('Unable to process withdrawals, chargeback after fee less than 0')
            raise CoinServiceError('Unable to process withdrawals, chargeback after fee less than 0')

        tx_outputs[keeper_wallet.address] = chargeback_amount
        return self._rpc_multi_transfer(
            vin, tx_outputs, keeper_unspent, keeper_wallet.private_key, private_key, keeper_wallet.redeem_script
        )

    def _prevtxs(self, unspent, redeem_script):
        prevtxs = []
        for item in unspent:
            prev = {
                'txid': item['txid'],
                'vout': item['vout'],
                'scriptPubKey': item['scriptPubKey'],
                'amount': item['amount'],
            }
            if redeem_script:
                prev['redeemScript'] = redeem_script
            prevtxs.append(prev)
        return prevtxs

    def _rpc_sign(self, vin, tx_outputs, unspent, private_key, private_key_s, redeem_script):
        raw = self.rpc.createrawtransaction(vin, tx_outputs)
        keys = [k for k in (private_key_s, private_key) if k]
        signed = self.rpc.signrawtransactionwithkey(raw, keys, self._prevtxs(unspent, redeem_script))
        return signed

    def _rpc_signed_tx_size(self, vin, tx_outputs, unspent, private_key, private_key_s, redeem_script):
        signed = self._rpc_sign(vin, tx_outputs, unspent, private_key, private_key_s, redeem_script)
        if not signed.get('hex'):
            raise CoinServiceError('Unable to sign QOGE keeper tx for size estimate: {}'.format(signed))
        decoded = self.rpc.decoderawtransaction(signed['hex'])
        return decoded.get('vsize') or decoded.get('size')

    def _rpc_multi_transfer(self, vin, tx_outputs, unspent, private_key, private_key_s, redeem_script):
        signed = self._rpc_sign(vin, tx_outputs, unspent, private_key, private_key_s, redeem_script)
        if not signed.get('complete'):
            self.log.error('Unable to sign QOGE keeper tx: %s', signed)
            raise CoinServiceError('Unable to sign QOGE keeper tx')
        tx_id = self.rpc.sendrawtransaction(signed['hex'])
        self.log.info('Sent TX: %s', tx_id)
        return tx_id

    @staticmethod
    def prepare_outs(outs: dict) -> list:
        return [
            {
                'address': address,
                'value': int(btc2sat(to_decimal(amount)))
            }
            for address, amount in outs.items()
        ]

    @staticmethod
    def prepare_inputs(inputs: list) -> list:
        return [
            {
                'address': item['address'],
                'tx_hash': item['txid'],
                'tx_pos': item['vout'],
                'output': item['txid'] + ':' + str(item['vout']),
                'value': int(btc2sat(to_decimal(item['amount'])))
            }
            for item in inputs
        ]

    def multi_tx_sign(self, inputs: list, outputs: list, private_key: str, private_key_s: str, redeem_script: str):
        tx_obj = self.crypto_coin.mktx(inputs, outputs)
        for i in range(0, len(tx_obj['ins'])):
            inp = tx_obj['ins'][i]
            segwit = False
            try:
                if address := inp['address']:
                    segwit = self.crypto_coin.is_native_segwit(address)
            except (IndexError, KeyError):
                pass
            sig1 = self.crypto_coin.multisign(tx_obj, i, redeem_script, private_key_s)
            sig3 = self.crypto_coin.multisign(tx_obj, i, redeem_script, private_key)
            tx_obj = apply_multisignatures(tx_obj, i, redeem_script, sig1, sig3, segwit=segwit)
        return serialize(tx_obj)

    def check_tx_for_deposit(self, tx_data):
        tx_id = tx_data['txid']
        outputs_amount = defaultdict(Decimal)

        for addr, amount in self.parse_tx_outputs(tx_data):
            outputs_amount[addr] += amount

        output_address = ', '.join(outputs_amount)
        accumulation_transaction: AccumulationTransaction = AccumulationTransaction.objects.filter(
            tx_hash=tx_id,
            tx_state=AccumulationTransaction.STATE_PENDING,
        ).first()

        if accumulation_transaction:
            addr = accumulation_transaction.wallet_transaction.wallet.address
            self.log.info('Found accumulation from %s to %s', addr, output_address)
            accumulation_details = AccumulationDetails.objects.filter(
                txid=tx_id,
                from_address=addr
            ).first()
            if not accumulation_details:
                AccumulationDetails.objects.create(
                    currency=QOGE_CURRENCY,
                    txid=tx_id,
                    from_address=addr,
                    to_address=output_address,
                )
            else:
                accumulation_details.to_address = output_address
                accumulation_details.complete()
            accumulation_transaction.complete()

        for addr, amount in outputs_amount.items():
            if addr not in self.get_users_addresses():
                continue

            if amount < FeesAndLimits.get_limit(self.currency.code, FeesAndLimits.DEPOSIT, FeesAndLimits.MIN_VALUE):
                self.log.info('Amount %s less than min deposit limit', amount)
                continue

            if ScoreManager.need_to_check_score(tx_id, addr, amount, self.currency.code):
                defer_time = ScoringSettings.get_deffered_scoring_time(self.currency.code)
                process_deffered_deposit.apply_async(
                    (tx_id, addr, amount, self.currency.code),
                    queue='qoge',
                    countdown=defer_time,
                )
            else:
                self.log.info('Tx amount too low for scoring')
                self.process_deposit(tx_id, addr, amount)

    def accumulate_deposit(self, wallet_transaction, inputs_dict, private_keys_dict):
        item = inputs_dict.get(wallet_transaction.tx_hash)
        if not item:
            return
        private_keys_dict[item['txid'] + ':' + str(item['vout'])] = private_keys_dict[item['address']]
        total_amount = wallet_transaction.amount

        accumulation_address = wallet_transaction.external_accumulation_address or self.get_accumulation_address(total_amount)
        accumulation_amount = 0

        try:
            tx_id, accumulation_amount = self.transfer_to([item], accumulation_address, total_amount, private_keys_dict)
        except TransferAmountLowError:
            wallet_transaction.set_balance_too_low()
            tx_id = None

        if tx_id:
            AccumulationDetails.objects.create(
                currency=QOGE_CURRENCY,
                txid=tx_id,
                from_address=item['address'],
                to_address=accumulation_address,
            )
            AccumulationTransaction.objects.create(
                wallet_transaction=wallet_transaction,
                amount=accumulation_amount,
                tx_type=AccumulationTransaction.TX_TYPE_ACCUMULATION,
                tx_hash=tx_id,
            )
            wallet_transaction.set_accumulation_in_progress()
        self.log.info('Accumulation to %s succeeded', accumulation_address)

    def accumulate(self):
        self.log.info('Starting accumulation: %s', self.currency.code)

        to_accumulate = self.get_accumulation_ready_wallet_transactions()
        to_accumulate_from_addresses = [w.wallet.address for w in to_accumulate]

        if not to_accumulate:
            self.log.warning('There are no addresses to accumulate')
            return

        inputs = self.get_unspent(addresses=to_accumulate_from_addresses)
        inputs_dict = {i['txid']: i for i in inputs}

        private_keys_dict = dict(UserWallet.objects.filter(
            currency=self.currency,
            address__in=to_accumulate_from_addresses,
        ).values_list(
            'address',
            'private_key'
        ))

        private_keys_dict = {
            address: AESCoderDecoder(settings.CRYPTO_KEY).decrypt(private_key)
            for address, private_key in private_keys_dict.items()
        }

        for wallet_transaction in to_accumulate:
            self.accumulate_deposit(wallet_transaction, inputs_dict, private_keys_dict)

        to_accumulate = self.get_external_accumulation_ready_wallet_transactions()
        to_accumulate_from_addresses = [w.wallet.address for w in to_accumulate]

        if not to_accumulate:
            self.log.warning('There are no addresses to accumulate')
            return

        inputs = self.get_unspent(addresses=to_accumulate_from_addresses)
        inputs_dict = {i['txid']: i for i in inputs}

        private_keys_dict = dict(UserWallet.objects.filter(
            currency=self.currency,
            address__in=to_accumulate_from_addresses,
        ).values_list(
            'address',
            'private_key'
        ))

        private_keys_dict = {
            address: AESCoderDecoder(settings.CRYPTO_KEY).decrypt(private_key)
            for address, private_key in private_keys_dict.items()
        }

        for wallet_transaction in to_accumulate:
            self.accumulate_deposit(wallet_transaction, inputs_dict, private_keys_dict)

    def transfer(self, inputs: list, outputs: dict, private_keys: dict):
        self.log.info('Make transfer %s in -> %s out', len(inputs), len(outputs))
        inputs = self.prepare_inputs(inputs)
        outputs = self.prepare_outs(outputs)
        tx_hex = self.crypto_coin.mktx(inputs, outputs)
        signed_tx = self.crypto_coin.signall(tx_hex, private_keys)
        signed_tx_s = serialize(signed_tx)
        tx_id = self.rpc.sendrawtransaction(signed_tx_s)
        self.log.info('Sent TX: %s', tx_id)
        return tx_id

    def get_tx_size(self, inputs: list, outputs: dict, private_keys: dict):
        inputs = self.prepare_inputs(inputs)
        outputs = self.prepare_outs(outputs)
        tx_hex = self.crypto_coin.mktx(inputs, outputs)
        signed_tx_without_fee = self.crypto_coin.signall(tx_hex, private_keys)
        signed_tx_without_fee_s = serialize(signed_tx_without_fee)
        tx_decode = self.rpc.decoderawtransaction(signed_tx_without_fee_s)
        return tx_decode.get('size')

    def transfer_to(self, inputs: list, address_to: str, amount: Decimal, private_keys: dict):
        pre_outputs = {
            address_to: amount
        }
        tx_size = self.get_tx_size(inputs, pre_outputs, private_keys)
        transfer_fee = self.get_transfer_fee(tx_size)
        transfer_amount = amount - transfer_fee
        self.log.info('Estimated transfer fee: %s fee[%s] size[%s]', self.currency.code, transfer_fee, tx_size)

        if transfer_amount <= 0:
            self.log.info('Transfer amount too low after fee apply: %s', transfer_amount)
            raise TransferAmountLowError

        outputs = {
            address_to: transfer_amount
        }
        inputs = self.prepare_inputs(inputs)
        outputs = self.prepare_outs(outputs)
        tx_hex = self.crypto_coin.mktx(inputs, outputs)
        signed_tx = self.crypto_coin.signall(tx_hex, private_keys)
        signed_tx_s = serialize(signed_tx)
        self.log.info('Make transfer %s in -> %s out', len(inputs), len(outputs))
        tx_id = self.rpc.sendrawtransaction(signed_tx_s)
        self.log.info('Sent TX: %s', tx_id)
        return tx_id, transfer_amount
