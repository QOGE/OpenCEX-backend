import logging

import requests
from django.conf import settings

from core.consts.currencies import BEP20_CURRENCIES
from core.currency import Currency
from lib.services.etherscan_client import EtherscanClient

log = logging.getLogger(__name__)


class BSCscanClient(EtherscanClient):
    def __init__(self):
        # BscScan V1 (api.bscscan.com) is deprecated; BSC is chainid=56 on Etherscan API V2.
        self.url = 'https://api.etherscan.io/v2/api?chainid=56&'

    def _make_request(self, uri=''):
        res = {}
        api_key = settings.BSCSCAN_KEY or settings.ETHERSCAN_KEY
        try:
            res = requests.get(
                f'{self.url}{uri}&apikey={api_key}')
            res = res.json()
        except Exception as e:
            log.exception('Can\'t fetch data from blockchain.info')
        return res

    def get_token_params(self, currency_code):
        return BEP20_CURRENCIES.get(Currency.get(currency_code))
