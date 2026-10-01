import json
from collections import OrderedDict
from typing import Tuple

from django.conf import settings

from core.models import UserWallet
from cryptocoins.coins.qoge import QOGE_CURRENCY
from cryptocoins.coins.qoge.coin import QogeCoin
from cryptocoins.models import Keeper
from cryptocoins.utils.commons import create_keeper
from lib.cipher import AESCoderDecoder


def generate_qoge_multisig_keeper(log=None) -> Tuple[OrderedDict, Keeper]:
    from cryptocoins.coins.qoge.service import QOGECoinService
    service = QOGECoinService()
    qoge = QogeCoin()
    ad1 = service.create_new_wallet(addr_import=False)
    ad2 = service.create_new_wallet(addr_import=False)
    ad3 = service.create_new_wallet(addr_import=False)

    is_segwit = not getattr(settings, 'QOGE_ADDRESS_LEGACY', False)
    pub_keys = [ad1.public_key, ad2.public_key, ad3.public_key]
    if is_segwit:
        script, address = qoge.mk_multsig_segwit_address(*pub_keys, num_required=2)
    else:
        script, address = qoge.mk_multisig_address(*pub_keys, num_required=2)

    private_key_encrypt = AESCoderDecoder(settings.CRYPTO_KEY).encrypt(
        ad3.private_key
    )

    owner = OrderedDict({
        'address': ad1.address,
        'public key': ad1.public_key,
        'private key': ad1.private_key
    })
    manager = OrderedDict({
        'address': ad2.address,
        'public key': ad2.public_key,
        'private key': ad2.private_key
    })
    site = OrderedDict({
        'address': ad3.address,
        'public key': ad3.public_key,
        'private key': ad3.private_key,
        'private key encrypted': private_key_encrypt
    })
    keeper_data = OrderedDict({
        'address': address,
        'extra: redeem_script': script
    })
    res = OrderedDict({
        'OWNER': owner,
        'MANAGER': manager,
        'SITE': site,
        'KEEPER': keeper_data
    })
    print(json.dumps(res, indent=4))

    addr_type = 'p2sh-segwit' if is_segwit else 'legacy'
    try:
        service.rpc.addmultisigaddress(2, pub_keys, 'keeper', addr_type)
    except Exception as exc:
        if log:
            log.warning('addmultisigaddress failed (%s); importing keeper as watch-only', exc)
    service.import_address(address, label='keeper')
    if log:
        log.info('Keeper address successfully imported to node')

    user_wallet = UserWallet.objects.create(
        user_id=None,
        currency=QOGE_CURRENCY,
        blockchain_currency=QOGE_CURRENCY,
        address=address,
        private_key=private_key_encrypt,
    )
    keeper: Keeper = create_keeper(user_wallet, extra={'redeem_script': script})
    return res, keeper
