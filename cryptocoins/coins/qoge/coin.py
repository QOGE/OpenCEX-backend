"""cryptos coin class for Qogecoin (Bitcoin Core 24 fork).

Mainnet prefixes from qogecoin/src/chainparams.cpp:
  PUBKEY_ADDRESS 120, SCRIPT_ADDRESS 102, SECRET_KEY 92, bech32_hrp "bq"
"""
from cryptos.coins.base import BaseSyncCoin
from cryptos.coins_async.base import BaseCoin


class AsyncQogeCoin(BaseCoin):
    coin_symbol = 'QOGE'
    display_name = 'Qogecoin'
    segwit_supported = True
    magicbyte = 120
    script_magicbyte = 102
    minimum_fee = 450
    wif_prefix = 0x5C
    segwit_hrp = 'bq'
    hd_path = 0
    # Electrumx is unused; OpenCEX talks to qogecoind over RPC.
    client_kwargs = {
        'server_file': 'bitcoin.json',
    }
    testnet_overrides = {
        'display_name': 'Qogecoin Testnet',
        'coin_symbol': 'QOGETEST',
        'magicbyte': 65,
        'script_magicbyte': 105,
        'wif_prefix': 0xCE,
        'segwit_hrp': 'bqt',
        'hd_path': 1,
        'client_kwargs': {
            'server_file': 'bitcoin_testnet.json',
            'use_ssl': False,
        },
    }


class QogeCoin(BaseSyncCoin):
    coin_class = AsyncQogeCoin
