"""Initialize a fork's encrypted state on main, preserving existing local data."""
import json
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv
from trip_scout.alerts import load_state, save_state
from trip_scout.storage import pack, STATE_FILES, encryption_key

load_dotenv()
encryption_key()
remote = subprocess.check_output(['git', 'remote', 'get-url', 'origin'], text=True).strip()
repository = remote.split('github.com:', 1)[1] if remote.startswith('git@github.com:') else urlsplit(remote).path.lstrip('/')
repository = repository.removesuffix('.git')
if len(repository.split('/')) != 2 or '..' in repository:
    raise SystemExit('Use your own GitHub repository as origin.')
directory = Path('runtime')
metadata = directory / 'installation.json'
if metadata.exists() and json.loads(metadata.read_text())['repository'].lower() == repository.lower():
    raise SystemExit('Installation already initialized; refusing to reset quota/history. Use the storage unpack command to restore local state.')
data = Path('data')
data.mkdir(exist_ok=True)
for name in STATE_FILES:
    target = data / name
    if not target.exists():
        save_state(target, load_state(target) if name == 'state.json' else {})
directory.mkdir(exist_ok=True)
pack(data, directory / 'state.enc', STATE_FILES)
metadata.write_text(json.dumps({'repository': repository, 'format': 1}, indent=2) + '\n', encoding='utf-8')
print('Your own encrypted state is ready. Commit runtime/ on main and push it. Save the same key as the base environment secret and back it up privately.')
