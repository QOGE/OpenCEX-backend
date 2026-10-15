PRIVATE_MSG_KINDS = frozenset((
    'user_notification',
    'balance',
    'wallets',
    'opened_orders',
    'closed_orders',
    'opened_orders_pair',
    'closed_orders_pair',
    'executed_order_notification',
    'wallet_history',
    'wallet_topups_history',
    'wallet_withdrawals_history',
    'wallet_history_ticker',
))


def is_private_group(group_name):
    name = group_name or ''
    for kind in PRIVATE_MSG_KINDS:
        if name == kind or name.startswith(kind + '_'):
            return True
    return False
