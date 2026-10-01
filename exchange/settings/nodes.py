import os

from exchange.settings import env


def _qoge_rpc_auth():
    user = env('QOGE_NODE_USER', default='')
    password = env('QOGE_NODE_PASS', default='')
    cookie_file = env('QOGE_NODE_COOKIE_FILE', default='')
    if not cookie_file:
        cookie_file = os.path.expanduser('~/.qogecoin/.cookie')
    if (not user or not password) and cookie_file and os.path.isfile(cookie_file):
        with open(cookie_file) as fh:
            raw = fh.read().strip()
        if ':' in raw:
            user, password = raw.split(':', 1)
    return user, password


_qoge_user, _qoge_pass = _qoge_rpc_auth()

NODES_CONFIG = {
    'btc': {
        'host': env('BTC_NODE_HOST', default='localhost'),
        'port': env('BTC_NODE_PORT', default=8333),
        'username': env('BTC_NODE_USER', default=''),
        'password': env('BTC_NODE_PASS', default=''),
    },
    'qoge': {
        'host': env('QOGE_NODE_HOST', default='localhost'),
        'port': env('QOGE_NODE_PORT', default=8332),
        'username': _qoge_user,
        'password': _qoge_pass,
        'wallet': env('QOGE_NODE_WALLET', default='opencex'),
    },
}
