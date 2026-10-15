"""Wallet private-key wrapping.

User deposit keys stay CRYPTO_KEY-only so automated sweeps of reused
addresses keep working. GasKeeper keys get a second password layer, the
same scheme as the main EVM/TRX Keeper.
"""
import os

from django.conf import settings

from lib.cipher import AESCoderDecoder


def looks_like_hex_privkey(value):
    if not value or not isinstance(value, str):
        return False
    text = value.strip()
    if text.startswith(('0x', '0X')):
        text = text[2:]
    if len(text) != 64:
        return False
    try:
        int(text, 16)
    except ValueError:
        return False
    return True


def double_encrypt(plaintext, password):
    inner = AESCoderDecoder(password).encrypt(plaintext)
    return AESCoderDecoder(settings.CRYPTO_KEY).encrypt(inner)


def unwrap_after_crypto_key(layer1, password=None):
    """layer1 is already CRYPTO_KEY-decrypted.

    A 64-hex string is a legacy single-layer GasKeeper key. Anything else
    needs the second-layer password.
    """
    if looks_like_hex_privkey(layer1):
        return layer1
    if not password:
        raise ValueError(
            'GasKeeper key is double-encrypted; set GAS_KEEPER_PASSWORD '
            'or place it in .handoff/gas_keeper_password'
        )
    inner = AESCoderDecoder(password).decrypt(layer1)
    if not looks_like_hex_privkey(inner):
        raise ValueError('GasKeeper password did not yield a private key')
    return inner


def wrap_crypto_key_ciphertext(ciphertext, password):
    """Wrap a CRYPTO_KEY-only ciphertext with a second password. None if already wrapped."""
    layer1 = AESCoderDecoder(settings.CRYPTO_KEY).decrypt(ciphertext)
    if not looks_like_hex_privkey(layer1):
        return None
    return double_encrypt(layer1, password)


def gas_keeper_password_for(currency_code):
    code = str(currency_code or '').upper()
    specific = os.environ.get('{}_GAS_KEEPER_PASSWORD'.format(code), '') or ''
    if specific.strip():
        return specific.strip()
    env_value = os.environ.get('GAS_KEEPER_PASSWORD', '') or ''
    if env_value.strip():
        return env_value.strip()
    path = os.path.join(
        getattr(settings, 'KEEPER_HANDOFF_DIR', '') or os.path.join(settings.BASE_DIR, '.handoff'),
        'gas_keeper_password',
    )
    try:
        with open(path, 'r') as fh:
            return fh.read().strip()
    except OSError:
        return ''


def write_gas_keeper_password_file(password):
    directory = getattr(settings, 'KEEPER_HANDOFF_DIR', '') or os.path.join(settings.BASE_DIR, '.handoff')
    os.makedirs(directory, mode=0o700, exist_ok=True)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass
    path = os.path.join(directory, 'gas_keeper_password')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as fh:
        fh.write(password)
        fh.write('\n')
    os.chmod(path, 0o600)
    return path
