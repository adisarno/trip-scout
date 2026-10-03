import base64
import json
import os
from pathlib import Path
import subprocess
import sys

from trip_scout.storage import unpack, STATE_FILES

ROOT = Path(__file__).resolve().parents[1]


def test_fork_initializes_its_own_archive_and_never_resets_it_again(tmp_path, monkeypatch):
    monkeypatch.setenv('STATE_ENCRYPTION_KEY', base64.b64encode(os.urandom(32)).decode())
    subprocess.run(['git', 'init', str(tmp_path)], capture_output=True, check=True)
    subprocess.run(['git', '-C', str(tmp_path), 'remote', 'add', 'origin', 'https://github.com/example/trip-scout.git'], check=True)
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    (runtime / 'installation.json').write_text(json.dumps({'repository': 'adisarno/trip-scout'}))
    (runtime / 'state.enc').write_bytes(b'inherited encrypted archive')
    data = tmp_path / 'data'
    data.mkdir()
    (data / 'usage.json').write_text('{"already-reserved": 7}')
    env = os.environ.copy() | {'PYTHONPATH': str(ROOT / 'src')}
    command = [sys.executable, str(ROOT / 'scripts/initialize_state.py')]
    first = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True)
    assert first.returncode == 0, first.stderr
    assert json.loads((runtime / 'installation.json').read_text())['repository'] == 'example/trip-scout'
    unpack(runtime / 'state.enc', tmp_path / 'restored', STATE_FILES)
    assert json.loads((tmp_path / 'restored/usage.json').read_text()) == {'already-reserved': 7}
    encrypted = (runtime / 'state.enc').read_bytes()
    second = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True)
    assert second.returncode != 0
    assert (runtime / 'state.enc').read_bytes() == encrypted


def test_actions_rejects_inherited_installation_before_any_search(tmp_path):
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    (runtime / 'installation.json').write_text(json.dumps({'repository': 'adisarno/trip-scout'}))
    command = [sys.executable, str(ROOT / 'scripts/check_installation.py')]
    env = os.environ.copy() | {'GITHUB_REPOSITORY': 'example/trip-scout'}
    assert subprocess.run(command, cwd=tmp_path, env=env, capture_output=True).returncode != 0
    env['GITHUB_REPOSITORY'] = 'adisarno/trip-scout'
    assert subprocess.run(command, cwd=tmp_path, env=env, capture_output=True).returncode == 0
