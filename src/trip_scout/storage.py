"""Encrypt runtime state and reports before storing them on a public repository."""
import argparse
import base64
import json
import os
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from dotenv import load_dotenv

STATE_FILES = ('state.json', 'usage.json', 'accommodation-cache.json')
REPORT_FILES = ('trips.html', 'trips.json', 'observations.html', 'observations.json', 'diagnostics.json')
HEADER = b'trip-scout-storage-v1\0'


def encryption_key():
    try:
        value = base64.b64decode(os.environ.get('STATE_ENCRYPTION_KEY', ''), validate=True)
    except (ValueError, TypeError):
        value = b''
    if len(value) != 32:
        raise ValueError('STATE_ENCRYPTION_KEY must be a base64 encoded 32-byte key; refusing to reset private state')
    return value


def pack(directory, target, files):
    payload = {name: (directory / name).read_text(encoding='utf-8') for name in files if (directory / name).exists()}
    if files == STATE_FILES and set(payload) != set(STATE_FILES):
        raise ValueError('Incomplete state; refusing to overwrite persisted quota or alert history')
    nonce = os.urandom(12)
    encrypted = HEADER + nonce + AESGCM(encryption_key()).encrypt(nonce, json.dumps(payload).encode(), HEADER)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(encrypted)


def unpack(source, directory, files):
    raw = source.read_bytes()
    if not raw.startswith(HEADER):
        raise ValueError('Unknown encrypted storage format')
    nonce = raw[len(HEADER):len(HEADER) + 12]
    payload = json.loads(AESGCM(encryption_key()).decrypt(nonce, raw[len(HEADER) + 12:], HEADER))
    if not isinstance(payload, dict) or set(payload) - set(files) or any(not isinstance(v, str) for v in payload.values()):
        raise ValueError('Invalid storage entries')
    if files == STATE_FILES and set(payload) != set(STATE_FILES):
        raise ValueError('Incomplete private state')
    # Validate every state document before writing any file.
    if files == STATE_FILES:
        for text in payload.values():
            if not isinstance(json.loads(text), dict):
                raise ValueError('State documents must be JSON objects')
    directory.mkdir(parents=True, exist_ok=True)
    for name, text in payload.items():
        (directory / name).write_text(text, encoding='utf-8')


def main():
    load_dotenv(override=False)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('pack', 'unpack'))
    parser.add_argument('kind', choices=('state', 'report'))
    parser.add_argument('archive', type=Path)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    files = STATE_FILES if args.kind == 'state' else REPORT_FILES
    try:
        if args.operation == 'pack':
            pack(args.directory, args.archive, files)
        else:
            unpack(args.archive, args.directory, files)
    except Exception as exc:
        parser.exit(1, f'Encrypted storage failed ({type(exc).__name__}); state was not reset.\n')


if __name__ == '__main__':
    main()
