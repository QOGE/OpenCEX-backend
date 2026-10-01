from cryptocoins.coins.qoge.utils import is_valid_qoge_address
from cryptocoins.utils.register import register_coin
from cryptocoins.utils.wallet import get_latest_block_id, get_wallet_data

QOGE = 21
CODE = 'QOGE'
DECIMALS = 8

QOGE_CURRENCY = register_coin(
    currency_id=QOGE,
    currency_code=CODE,
    address_validation_fn=is_valid_qoge_address,
    wallet_creation_fn=get_wallet_data,
    latest_block_fn=get_latest_block_id,
    blocks_diff_alert=1,
)
