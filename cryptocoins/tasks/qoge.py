import logging

from bitcoinrpc.authproxy import AuthServiceProxy
from celery import shared_task
from django.conf import settings

from cryptocoins.cache import sat_per_byte_cache
from lib.helpers import to_decimal

log = logging.getLogger(__name__)


def get_fees_from_tx(tx):
    fee = tx['fees']
    if type(fee) is dict:
        fee = fee['base']
    return fee


def _rpc_url():
    from urllib.parse import quote
    cfg = settings.NODES_CONFIG['qoge']
    user = quote(str(cfg.get('username') or ''), safe='')
    password = quote(str(cfg.get('password') or ''), safe='')
    url = 'http://{user}:{password}@{host}:{port}'.format(
        user=user,
        password=password,
        host=cfg.get('host', 'localhost'),
        port=cfg.get('port', 8332),
    )
    wallet = cfg.get('wallet') or ''
    if wallet:
        url = '{}/wallet/{}'.format(url, wallet)
    return url


@shared_task
def cache_qogecoin_sat_per_byte(logger=None):
    """Calculates Qogecoin sat/b from mempool and caches it."""
    logger = logger or log
    rpc = AuthServiceProxy(_rpc_url(), timeout=settings.SAT_PER_BYTES_UPDATE_PERIOD)
    s_p_b = 30

    try:
        txs = list(rpc.getrawmempool(True).values())
        spb_list = sorted(
            list([get_fees_from_tx(tx) * 10 ** 8 / tx['vsize'] for tx in txs]),
            reverse=True
        )
        total_txs_count = len(spb_list)
        if 0 < total_txs_count <= 1500:
            s_p_b = spb_list[-1]
        elif total_txs_count > 1500:
            s_p_b = spb_list[1500]
        s_p_b = round(to_decimal(s_p_b) * to_decimal(settings.SAT_PER_BYTES_RATIO))
    except Exception:
        logger.exception("Can't calculate Qogecoin satoshi per byte")

    if s_p_b < settings.SAT_PER_BYTES_MIN_LIMIT:
        s_p_b = settings.SAT_PER_BYTES_MIN_LIMIT
    if s_p_b > settings.SAT_PER_BYTES_MAX_LIMIT:
        s_p_b = settings.SAT_PER_BYTES_MAX_LIMIT

    sat_per_byte_cache.set('qogecoin', s_p_b)
