"""Resolve both legs of a round trip; use only the verified free allowance."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import os

import requests

from .quota import FreeQuota

SCHEDULE_NOTE = 'Local times for a round trip matched at the same rounded price; confirm the itinerary through the link.'


def journey(row, origin, destination, day):
    legs = row['flights']
    if not legs or legs[0]['departure_airport']['id'] != origin or legs[-1]['arrival_airport']['id'] != destination:
        raise ValueError('Schedule route mismatch')
    for index, leg in enumerate(legs):
        datetime.fromisoformat(leg['departure_airport']['time'])
        datetime.fromisoformat(leg['arrival_airport']['time'])
        if index and legs[index - 1]['arrival_airport']['id'] != leg['departure_airport']['id']:
            raise ValueError('Disconnected schedule')
    departure = legs[0]['departure_airport']['time']
    arrival = legs[-1]['arrival_airport']['time']
    if str(datetime.fromisoformat(departure).date()) != day:
        raise ValueError('Schedule date mismatch')
    return departure, arrival


def options(payload, price):
    for row in payload.get('best_flights', []) + payload.get('other_flights', []):
        # Google Deals rounds totals to whole currency units. Do not attach
        # an unrelated cheaper/more expensive itinerary's times.
        if row.get('type') == 'Round trip' and abs(float(row.get('price', -1)) - price) <= 0.5:
            yield row


def resolve(deal, client, quota, key):
    params = {'api_key': key, 'engine': 'google_flights', 'type': 1,
              'departure_id': deal.origin, 'arrival_id': deal.destination,
              'outbound_date': deal.departure, 'return_date': deal.returning,
              'currency': deal.currency, 'hl': 'en', 'gl': deal.market or 'it', 'adults': 1}
    def fetch(extra=None):
        quota.reserve()
        response = client.get('https://serpapi.com/search.json', params=params | (extra or {}), timeout=45)
        if response.status_code != 200:
            raise ValueError('Schedule search unavailable')
        payload = response.json()
        if not isinstance(payload, dict) or payload.get('error'):
            raise ValueError('Schedule search unsuccessful')
        return payload
    outbound = None
    for row in options(fetch(), deal.price):
        try:
            departure, arrival = journey(row, deal.origin, deal.destination, deal.departure)
            if row.get('departure_token'):
                outbound = (row, departure, arrival)
                break
        except (KeyError, ValueError, TypeError):
            continue
    if not outbound:
        raise ValueError('No matching outbound itinerary at the reported round-trip price')
    row, departure, arrival = outbound
    for returning in options(fetch({'departure_token': row['departure_token']}), deal.price):
        try:
            back_departure, back_arrival = journey(returning, deal.destination, deal.origin, deal.returning)
        except (KeyError, ValueError, TypeError):
            continue
        return {'outbound_departure': departure, 'outbound_arrival': arrival,
                'return_departure': back_departure, 'return_arrival': back_arrival,
                'arrival_date': str(datetime.fromisoformat(arrival).date()),
                'schedule_note': SCHEDULE_NOTE}
    raise ValueError('No matching return itinerary at the reported round-trip price')


def enrich_schedules(deals, config, state, usage_path, client=None, now=None):
    client = client or requests.Session()
    now = now or datetime.now(timezone.utc)
    key = os.getenv('SERPAPI_API_KEY', '').strip()
    cache = state.setdefault('schedule_cache', {})
    result, notes = [], []
    quota, checked = None, 0
    for deal in deals:
        identity = f'{deal.key}:{deal.departure}:{deal.returning}:{deal.price}:{deal.market}'
        saved = cache.get(identity)
        if saved and now - datetime.fromisoformat(saved['observed_at']) < timedelta(days=7):
            observed = datetime.fromisoformat(saved['observed_at'])
            saved['schedule']['schedule_note'] = SCHEDULE_NOTE + ' Checked on ' + observed.strftime('%Y-%m-%d %H:%M UTC') + '.'
            result.append(replace(deal, **saved['schedule']))
            continue
        if (deal.provider != 'google' or not key or deal.outbound_departure
                or checked >= config['max_schedule_checks_per_scan']):
            result.append(deal)
            continue
        checked += 1
        try:
            if quota is None:
                quota = FreeQuota(client, key, usage_path, config['serpapi_monthly_limit'])
            schedule = resolve(deal, client, quota, key)
            schedule['schedule_note'] += ' Checked on ' + now.strftime('%Y-%m-%d %H:%M UTC') + '.'
            cache[identity] = {'observed_at': now.isoformat(), 'schedule': schedule}
            result.append(replace(deal, **schedule))
        except (requests.RequestException, ValueError, KeyError, TypeError):
            notes.append(f'{deal.origin}–{deal.destination}: times unavailable or insufficient free allowance.')
            result.append(deal)
    cutoff = now - timedelta(days=7)
    state['schedule_cache'] = {k: v for k, v in cache.items()
                               if datetime.fromisoformat(v['observed_at']) >= cutoff}
    return result, notes
