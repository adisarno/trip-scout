import base64
import os

import pytest
from cryptography.exceptions import InvalidTag

from trip_scout.storage import pack, unpack, STATE_FILES


def test_private_state_roundtrip_and_tamper_rejection(tmp_path, monkeypatch):
    monkeypatch.setenv('STATE_ENCRYPTION_KEY', base64.b64encode(os.urandom(32)).decode())
    source = tmp_path / 'source'
    source.mkdir()
    for name in STATE_FILES:
        (source / name).write_text('{"private": "recipient-and-itinerary", "usage": 123}')
    archive = tmp_path / 'state.enc'
    pack(source, archive, STATE_FILES)
    assert b'recipient-and-itinerary' not in archive.read_bytes()
    target = tmp_path / 'target'
    unpack(archive, target, STATE_FILES)
    assert (target / 'usage.json').read_bytes() == (source / 'usage.json').read_bytes()
    corrupted = bytearray(archive.read_bytes())
    corrupted[-1] ^= 1
    archive.write_bytes(corrupted)
    with pytest.raises(InvalidTag):
        unpack(archive, tmp_path / 'other', STATE_FILES)
    assert not (tmp_path / 'other').exists()


def test_missing_key_or_state_never_resets_quota(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    source.mkdir()
    archive = tmp_path / 'state.enc'
    with pytest.raises(ValueError, match='Incomplete'):
        pack(source, archive, STATE_FILES)
    assert not archive.exists()
    for name in STATE_FILES:
        (source / name).write_text('{}')
    monkeypatch.delenv('STATE_ENCRYPTION_KEY', raising=False)
    with pytest.raises(ValueError, match='refusing'):
        pack(source, archive, STATE_FILES)
    assert not archive.exists()
