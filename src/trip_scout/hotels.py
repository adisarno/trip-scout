"""Dated Google Hotels totals, sharing the flight provider's free allowance."""
import os
from urllib.parse import urlencode

import requests

from .direct_stays import money
from .quota import FreeQuota


class HotelSearch:
    def __init__(self, config, usage_path, client=None):
        self.config, self.usage_path = config, usage_path
        self.client = client or requests.Session()
        self.quota = None
        self.used = 0

    def __call__(self, deal, adults):
        key = os.getenv('SERPAPI_API_KEY', '').strip()
        if not key:
            raise RuntimeError('Google Hotels needs a verified free SerpApi key')
        if self.used >= self.config['accommodation']['max_google_queries_per_scan']:
            raise RuntimeError('Google Hotels per-scan allowance reached')
        if self.quota is None:
            self.quota = FreeQuota(self.client, key, self.usage_path, self.config['serpapi_monthly_limit'])
        self.quota.reserve()
        self.used += 1
        checkin = deal.arrival_date or deal.departure
        params = {'engine': 'google_hotels', 'q': ', '.join(filter(None, [deal.city, deal.country])) + ' hotels',
                  'check_in_date': checkin, 'check_out_date': deal.returning,
                  'adults': adults, 'currency': deal.currency, 'gl': 'it', 'hl': 'en', 'sort_by': 3}
        response = self.client.get('https://serpapi.com/search.json', params=params | {'api_key': key}, timeout=45)
        if response.status_code != 200:
            raise RuntimeError('Google Hotels unavailable')
        return parse_hotels(response.json(), deal, adults, params, self.config['accommodation']['max_results_per_source'])


def parse_hotels(payload, deal, adults, params, limit):
    if not isinstance(payload, dict) or payload.get('error'):
        raise ValueError('Google Hotels search failed')
    echoed = payload.get('search_parameters', {})
    for field in ('check_in_date', 'check_out_date', 'adults', 'currency', 'q'):
        if str(echoed.get(field)) != str(params[field]):
            raise ValueError('Google Hotels did not confirm requested destination/dates/occupancy/currency')
    rows = payload.get('properties')
    if rows is None and payload.get('search_information', {}).get('total_results') == 0:
        return []
    if not isinstance(rows, list):
        raise ValueError('Unexpected Google Hotels properties response')
    records = []
    for row in rows:
        # Never multiply a headline nightly rate: only explicit full-stay totals.
        rate = row.get('total_rate') or {}
        total = money(str(rate.get('lowest', '')), deal.currency)
        extracted = rate.get('extracted_lowest')
        if not total or type(extracted) not in (int, float) or abs(total - extracted) > 0.51 or not row.get('name'):
            continue
        # The provider link can be the hotel's undated homepage. Link instead
        # to a dated Google comparison, without API URLs or credentials.
        url = 'https://www.google.com/travel/hotels?' + urlencode({
            'q': row['name'] + ' ' + deal.city, 'checkin': params['check_in_date'],
            'checkout': params['check_out_date'], 'adults': adults, 'curr': deal.currency})
        records.append({'name': row['name'], 'url': url, 'currency': deal.currency,
                        'checkIn': params['check_in_date'], 'checkOut': params['check_out_date'],
                        'adults': adults, 'price': total,
                        'roomName': '', 'rating': row.get('overall_rating')})
        if len(records) >= limit:
            break
    if rows and not records:
        raise ValueError('Google Hotels returned no supported full-stay totals')
    return records
