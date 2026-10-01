"""Run: python3 cryptocoins/coins/qoge/test_address.py"""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    'qoge_utils', Path(__file__).with_name('utils.py')
)
_utils = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_utils)
is_valid_qoge_address = _utils.is_valid_qoge_address

VALID = [
    'qXv4SyttDFCGb7rFyDfZwKdPHShmUw7uP4',  # P2PKH
    'qYVi6JTWot5bNDkFkXbXWZJwcGtqBb47SC',  # cold wallet P2PKH
    'iQ431DzyoCKeGRASSrST5FKxZvBA5Kiw5B',  # P2SH
    'bq1qhhdlfn9n9vwwxuccxdga3agwsx4x49np6eztjm',  # P2WPKH
    'bq1puwkqf9k4tj58t6d6vaveprr9dfpa9ap3pfrusu5964l76s7mu2cqts3hv4',  # P2TR
    'bq1zdr5mx26fyd4l3jxf4tcg6dpz8z7836e6n9n43nrxsda9lvr2c6rs3e2e92',  # P2QPK
]

INVALID = [
    '',
    '1GnX7YYimkWPzkPoHYqbJ4waxG6MN2cdSg',
    'bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4',
    'notanaddress',
]


def main():
    failed = 0
    for addr in VALID:
        if not is_valid_qoge_address(addr):
            print('expected valid:', addr)
            failed += 1
    for addr in INVALID:
        if is_valid_qoge_address(addr):
            print('expected invalid:', addr)
            failed += 1
    if failed:
        raise SystemExit(failed)
    print('ok', len(VALID), 'valid,', len(INVALID), 'invalid')


if __name__ == '__main__':
    main()
