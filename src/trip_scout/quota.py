"""Fail closed unless the account is free; reserve searches before sending them."""
import hashlib
import json
from pathlib import Path

from filelock import FileLock

from .alerts import save_state


class FreeQuota:
    def __init__(self, client, key, path, limit=200):
        response = client.get('https://serpapi.com/account.json', params={'api_key': key}, timeout=20)
        if response.status_code != 200:
            raise ValueError('SerpApi free account could not be verified')
        account = response.json()
        if (not isinstance(account, dict)
                or type(account.get('plan_monthly_price')) not in (int, float)
                or account['plan_monthly_price'] != 0
                or 'free' not in str(account.get('plan_name', '')).lower()
                or account.get('account_status') != 'Active'
                or type(account.get('plan_searches_left')) is not int
                or not isinstance(account.get('plan_renewal_date'), str)):
            raise ValueError('Only a verified active SerpApi free plan is allowed')
        self.remaining = account['plan_searches_left']
        self.cycle = account['plan_renewal_date']
        self.identity = hashlib.sha256(str(account.get('account_id') or key).encode()).hexdigest()[:16]
        self.path = Path(path)
        self.limit = min(limit, 200)

    def reserve(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(self.path) + '.lock'):
            usage = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}
            bucket = f'{self.identity}:{self.cycle}'
            used = usage.get(bucket, 0)
            if self.remaining <= 10 or used >= self.limit:
                raise ValueError('Free search allowance exhausted; paid requests are disabled')
            usage[bucket] = used + 1
            save_state(self.path, usage)
            self.remaining -= 1
