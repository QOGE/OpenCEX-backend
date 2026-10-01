"""Qogecoin address validation.

Mainnet encodings (chainparams.cpp):
  P2PKH     base58 version 120   e.g. q...
  P2SH      base58 version 102   e.g. i...
  P2WPKH/WSH bech32  HRP bq, witness v0   e.g. bq1q...
  P2TR      bech32m HRP bq, witness v1   e.g. bq1p...
  P2QPK     bech32m HRP bq, witness v2   e.g. bq1z...  (SIP-QOGE-PQC-02)

BTC addresses (version 0/5, HRP bc) are rejected.
"""
from hashlib import sha256

DIGITS58 = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
CHARSET = 'qpzry9x8gf2tvdw0s3jn54khce6mua7l'
BECH32M_CONST = 0x2bc830a3

QOGE_HRP = 'bq'
QOGE_P2PKH_VERSION = 120
QOGE_P2SH_VERSION = 102

# witness v0: 20-byte P2WPKH or 32-byte P2WSH
# witness v1: 32-byte P2TR
# witness v2: 32-byte P2QPK (HASH256 of SLH-DSA pubkey)
_WITNESS_PROGRAM_LEN = {
    0: (20, 32),
    1: (32,),
    2: (32,),
}


def decode_base58(bc, length):
    n = 0
    for char in bc:
        n = n * 58 + DIGITS58.index(char)
    return n.to_bytes(length, 'big')


def _valid_qoge_base58(address):
    try:
        raw = decode_base58(address, 25)
    except Exception:
        return False
    if raw[-4:] != sha256(sha256(raw[:-4]).digest()).digest()[:4]:
        return False
    return raw[0] in (QOGE_P2PKH_VERSION, QOGE_P2SH_VERSION)


def bech32_polymod(values):
    generator = [0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3]
    chk = 1
    for value in values:
        top = chk >> 25
        chk = (chk & 0x1ffffff) << 5 ^ value
        for i in range(5):
            chk ^= generator[i] if ((top >> i) & 1) else 0
    return chk


def bech32_hrp_expand(hrp):
    return [ord(x) >> 5 for x in hrp] + [0] + [ord(x) & 31 for x in hrp]


def convertbits(data, frombits, tobits, pad=True):
    acc = 0
    bits = 0
    ret = []
    maxv = (1 << tobits) - 1
    max_acc = (1 << (frombits + tobits - 1)) - 1
    for value in data:
        if value < 0 or (value >> frombits):
            return None
        acc = ((acc << frombits) | value) & max_acc
        bits += frombits
        while bits >= tobits:
            bits -= tobits
            ret.append((acc >> bits) & maxv)
    if pad:
        if bits:
            ret.append((acc << (tobits - bits)) & maxv)
    elif bits >= frombits or ((acc << (tobits - bits)) & maxv):
        return None
    return ret


def _decode_segwit(address):
    """Return (hrp, witver, program_bytes) or None."""
    if any(ord(x) < 33 or ord(x) > 126 for x in address):
        return None
    if address.lower() != address and address.upper() != address:
        return None
    bech = address.lower()
    pos = bech.rfind('1')
    if pos < 1 or pos + 7 > len(bech):
        return None
    if not all(x in CHARSET for x in bech[pos + 1:]):
        return None
    hrp = bech[:pos]
    data = [CHARSET.find(x) for x in bech[pos + 1:]]
    if len(data) < 7:
        return None
    polymod = bech32_polymod(bech32_hrp_expand(hrp) + data)
    if polymod == 1:
        encoding = 'bech32'
    elif polymod == BECH32M_CONST:
        encoding = 'bech32m'
    else:
        return None
    witver = data[0]
    program = convertbits(data[1:-6], 5, 8, pad=False)
    if program is None:
        return None
    if witver == 0 and encoding != 'bech32':
        return None
    if witver != 0 and encoding != 'bech32m':
        return None
    return hrp, witver, bytes(program)


def _valid_qoge_segwit(address):
    decoded = _decode_segwit(address)
    if not decoded:
        return False
    hrp, witver, program = decoded
    if hrp != QOGE_HRP:
        return False
    allowed = _WITNESS_PROGRAM_LEN.get(witver)
    if not allowed:
        return False
    return len(program) in allowed


def is_valid_qoge_address(address):
    if not address or not isinstance(address, str):
        return False
    if _valid_qoge_base58(address):
        return True
    return bool(_valid_qoge_segwit(address))
