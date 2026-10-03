"""Reject an inherited encrypted archive until a fork initializes its own data."""
import json
import os
from pathlib import Path

expected = os.environ.get('GITHUB_REPOSITORY', '').lower()
path = Path('runtime/installation.json')
if not path.exists() or json.loads(path.read_text())['repository'].lower() != expected:
    raise SystemExit('Initialize your own installation first: see README, GitHub Actions setup. Inherited state cannot be used with another installation key.')
