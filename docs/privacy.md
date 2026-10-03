# Public repository and private runtime data

The code and offline demo can be public. `.env`, local `data/`, local `output/`
and `.state/` are ignored. Never add them with `git add -f`.

GitHub Actions restores three JSON files from an AES-256-GCM encrypted archive
at `runtime/state.enc` on `main`, then saves them back after every scan,
including failures and dry runs. This preserves quota reservations, pending
delivery retries, alert baselines and accommodation caches without state
plaintext data in Git. Each scan creates a normal state commit on main.
Encryption protects the public archive; a new nonce is used for each write.
`runtime/installation.json` contains only the public repository identity.
Forks must initialize their own archive and key; the workflow rejects an
inherited installation identity rather than decrypting or resetting it.

The `STATE_ENCRYPTION_KEY` secret in this repository's `base` environment is a
base64-encoded random 32-byte key. Back it up privately. For a new installation,
generate it using:

```sh
python -c "import os,base64; print(base64.b64encode(os.urandom(32)).decode())"
```

Never print an existing production key or run this command in public Actions
logs. A missing/wrong key or incomplete archive stops the scan rather than
resetting quota/history. Changing the key requires re-encrypting existing
archives first. Resend/Groq/SerpApi keys stay in Actions secrets and `.env`.

Reports are encrypted before being uploaded as `report.enc` artifacts, retained
for 14 days. To inspect your downloaded report locally, put the encryption key
in `.env` and run:

```sh
python -m trip_scout.storage unpack report report.enc output/downloaded-report
```

The hosted scan uses `--quiet` to omit itinerary tables and detailed failures
from public logs. Counts, provider warnings and execution metadata remain
visible. Forks cannot decrypt this installation's data and should initialize
their own archive on main and their own key. Pull-request tests use offline fixtures and do
not receive production secrets.

Removing files from today's tree does not remove old commits or previously
uploaded artifacts/logs. Check historical data and commit-author addresses
before changing repository visibility. Use GitHub's noreply address for future
commits. Sanitizing old author metadata requires rewriting history, which is
a separate one-time operation rather than routine development.
