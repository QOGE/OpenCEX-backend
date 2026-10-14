from collections import defaultdict
from decimal import Decimal


def normalize_txid(txid):
    return (txid or '').lower()


def utxo_outpoint(item):
    return normalize_txid(item.get('txid')), int(item['vout'])


def index_unspent_by_deposit(inputs):
    """Group listunspent rows by (txid, address), keeping every vout."""
    index = defaultdict(list)
    seen = set()
    for item in inputs or []:
        txid, vout = utxo_outpoint(item)
        address = item.get('address') or ''
        if (txid, vout) in seen:
            continue
        seen.add((txid, vout))
        index[(txid, address)].append(item)
    return index


def select_deposit_utxos(index, tx_hash, address, spent=None):
    """Return unspent outpoints that belong to one (tx, address) deposit."""
    spent = spent or set()
    items = []
    for item in index.get((normalize_txid(tx_hash), address), []):
        outpoint = utxo_outpoint(item)
        if outpoint in spent:
            continue
        items.append(item)
    return items


def utxo_total_amount(items):
    total = Decimal('0')
    for item in items:
        total += Decimal(str(item.get('amount') or 0))
    return total


def bind_utxo_private_keys(items, address_key, signing_keys):
    for item in items:
        signing_keys['{}:{}'.format(item.get('txid'), item.get('vout'))] = address_key
    return signing_keys
