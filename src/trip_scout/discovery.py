import logging
import os
import re
import math
from datetime import date, timedelta
from statistics import median
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit

import requests

from .provider import Deal, search
from .quota import FreeQuota

LOG = logging.getLogger(__name__)


def parse_google(payload, config, start, end, market=''):
    if isinstance(payload, dict) and payload.get('error'):
        raise ValueError(str(payload['error']))
    if (isinstance(payload, dict) and 'deals' not in payload
            and payload.get('search_metadata', {}).get('status') == 'Success'
            and payload.get('departure_informations', {}).get('airport_code')):
        return []
    if not isinstance(payload, dict) or payload.get('error') or not isinstance(payload.get('deals'), list):
        raise ValueError('Unexpected Google Flights Deals response')
    deals = []
    malformed = 0
    for row in payload['deals']:
        try:
            departure = date.fromisoformat(row.get('outbound_date') or row['start_date'])
            returning = date.fromisoformat(row.get('return_date') or row['end_date'])
            origin, destination = row['departure_airport_code'], row['arrival_airport_code']
            price, average = float(row['price']), float(row['average_price'])
            link = row['flight_link']
            parts = urlsplit(link)
            if parts.scheme != 'https' or parts.hostname not in ('www.google.com', 'google.com'):
                continue
            if not re.fullmatch(r'[A-Z]{3}', destination) or origin not in config['origins']:
                continue
            if not math.isfinite(price) or not math.isfinite(average) or not 0 < price < average or not start <= departure <= end:
                continue
            if not config['min_nights'] <= (returning - departure).days <= config['max_nights']:
                continue
            deals.append(Deal(origin, destination, str(row['name']), str(departure), str(returning),
                              price, config['currency'], str(row.get('country', '')), str(departure),
                              'google', link, average, market))
        except (KeyError, TypeError, ValueError):
            malformed += 1
            LOG.warning('Skipping malformed Google deal')
    if malformed and malformed == len(payload['deals']):
        raise ValueError('No usable Google deals in a nonempty response; check response schema')
    return deals


def discover(config, today, state, client=None, usage_path=Path('data/usage.json')):
    client = client or requests.Session()
    windows = [(start, min(start + config['window_days'] - 1, config['max_days_ahead']))
               for start in range(config['min_days_ahead'], config['max_days_ahead'] + 1, config['window_days'])]
    cursor = state.get('window_cursor', 0) % len(windows)
    count = min(len(windows), config['max_windows_per_scan'])
    deals, errors = [], []
    coverage = {'configured_origins': config['origins'],
                'earliest_departure': str(today + timedelta(days=config['min_days_ahead'])),
                'latest_return': str(today + timedelta(days=config['max_days_ahead'])),
                'windows': [], 'providers': [], 'warnings': []}
    key = os.getenv('SERPAPI_API_KEY', '').strip()
    quota = None
    if config['flight_provider'] == 'google' and key:
        try:
            quota = FreeQuota(client, key, usage_path, config['serpapi_monthly_limit'])
        except (requests.RequestException, ValueError, TypeError, KeyError):
            LOG.warning('SerpApi free account unavailable/unverified; using keyless Ryanair')
            coverage['warnings'].append('SerpApi free account unavailable/unverified: Ryanair-only fallback.')
    elif config['flight_provider'] == 'google':
        LOG.info('No SerpApi key; using keyless Ryanair')
        coverage['warnings'].append('SERPAPI_API_KEY missing: Ryanair only, no Google or country comparisons.')
    markets = config['market_countries']
    market_cursors = state.setdefault('window_market_cursors', {})
    secondary_cursors = state.setdefault('window_secondary_origin_cursors', {})
    secondary_cursor = state.get('secondary_origin_cursor', 0)
    for index in range(count):
        # Spread each scan across the horizon, rather than adjacent months.
        window_index = (cursor + index * len(windows) // count) % len(windows)
        low, high = windows[window_index]
        window_id = f'{low}:{high}'
        market_cursor = market_cursors.get(window_id, window_index % len(markets)) % len(markets)
        # Always spend the first search on the user's first airport. Rotate
        # the remaining airports without locking any month to one origin.
        origin = config['origins'][0]
        if index and len(config['origins']) > 1:
            secondary = config['origins'][1:]
            position = secondary_cursors.get(window_id, secondary_cursor) % len(secondary)
            origin = secondary[position]
            secondary_cursors[window_id] = (position + 1) % len(secondary)
            secondary_cursor += 1
        start, end = today + timedelta(days=low), today + timedelta(days=high)
        coverage['windows'].append({'from': str(start), 'to': str(end), 'origin': origin})
        if config['flight_provider'] == 'ryanair' or quota is None:
            coverage['windows'][-1]['origin'] = ', '.join(config['origins'])
            coverage['providers'].append('ryanair')
            batch, problems = search(config | {'min_days_ahead': low, 'max_days_ahead': high,
                                              '_coverage_warnings': coverage['warnings']}, today)
            deals.extend(batch)
            errors.extend(problems)
            continue
        for offset in range(min(config['markets_per_window'], len(markets))):
            market = markets[(market_cursor + offset) % len(markets)]
            try:
                quota.reserve()
            except ValueError:
                coverage['windows'][-1]['origin'] = ', '.join(config['origins'])
                LOG.warning('SerpApi free allowance exhausted; using keyless Ryanair')
                batch, problems = search(config | {'min_days_ahead': low, 'max_days_ahead': high,
                                                  '_coverage_warnings': coverage['warnings']}, today)
                deals.extend(batch)
                errors.extend(problems)
                quota = None
                coverage['providers'].append('ryanair')
                coverage['warnings'].append('SerpApi free quota exhausted: Ryanair-only fallback.')
                break
            try:
                coverage['providers'].append('google')
                response = client.get('https://serpapi.com/search.json', params={
                    'api_key': key, 'engine': 'google_flights_deals', 'departure_id': origin,
                    'currency': config['currency'], 'hl': 'en', 'gl': market, 'type': 1,
                    'outbound_date': f'{start},{end}',
                    'trip_length': f"{config['min_nights']},{config['max_nights']}",
                }, timeout=45)
                if response.status_code != 200:
                    raise RuntimeError(f'HTTP {response.status_code}')
                deals.extend(parse_google(response.json(), config, start, end, market))
            except (requests.RequestException, ValueError, RuntimeError) as exc:
                # Request exceptions can contain URLs with the API key. Only
                # include controlled response errors, with the key redacted.
                detail = str(exc).replace(key, '[redacted]')[:500] if isinstance(exc, (ValueError, RuntimeError)) else type(exc).__name__
                errors.append(f'Google {origin} {market} {start} to {end}: {detail}')
        market_cursors[window_id] = (market_cursor + config['markets_per_window']) % len(markets)
    state['secondary_origin_cursor'] = secondary_cursor
    state['window_cursor'] = (cursor + 1) % len(windows)
    deals = cheapest_markets(deals)
    valid = filter_live_dates(deals, config, today)
    coverage['rejected_dates'] = len(deals) - len(valid)
    coverage['providers'] = sorted(set(coverage['providers']))
    coverage['warnings'] = list(dict.fromkeys(coverage['warnings']))
    coverage['total_windows'] = len(windows)
    state['last_scan'] = coverage
    return valid, errors


def filter_live_dates(deals, config, today):
    earliest = today + timedelta(days=config['min_days_ahead'])
    latest = today + timedelta(days=min(config['max_days_ahead'], 365))
    return [d for d in deals if earliest <= date.fromisoformat(d.departure) < date.fromisoformat(d.returning) <= latest]


def cheapest_markets(deals):
    """Compare route/date offers; airline/product details may differ by market."""
    groups = {}
    for deal in deals:
        groups.setdefault((deal.key, deal.departure, deal.returning), []).append(deal)
    result = []
    for offers in groups.values():
        cheapest = min(offers, key=lambda d: d.price)
        prices = {}
        for offer in offers:
            if offer.market:
                prices[offer.market] = min(prices.get(offer.market, offer.price), offer.price)
        result.append(replace(cheapest, market_prices=prices))
    return result


def bargain_candidates(deals, config, state, today):
    history = state.setdefault('price_history', {})
    candidates, comparisons = [], {}
    daily = {}
    for deal in deals:
        # Compare route, departure month, and trip length; not unrelated seasons/stays.
        nights = (date.fromisoformat(deal.returning) - date.fromisoformat(deal.departure)).days
        bucket = f'{deal.key}:{deal.departure[:7]}:{nights}'
        observations = history.get(bucket, {})
        baseline = deal.average_price
        basis = 'Google-reported average fare'
        if baseline is None:
            prior = [price for day, price in observations.items() if day < str(today)]
            if len(prior) >= config['history_min_days']:
                baseline = median(prior)
                basis = f'median observed daily low ({len(prior)} prior scan days, same departure month/stay length)'
        daily[bucket] = min(daily.get(bucket, deal.price), deal.price)
        if baseline is None or baseline <= 0:
            continue
        discount = (1 - deal.price / baseline) * 100
        if discount >= config['min_discount_percent']:
            candidates.append(deal)
            comparisons[deal.key + ':' + deal.departure + ':' + deal.returning] = {
                'baseline': round(baseline, 2), 'discount_percent': round(discount, 1), 'basis': basis,
            }
    for bucket, price in daily.items():
        observations = history.setdefault(bucket, {})
        observations[str(today)] = min(observations.get(str(today), price), price)
    cutoff = str(today - timedelta(days=90))
    state['price_history'] = {k: {d: p for d, p in v.items() if d >= cutoff}
                              for k, v in history.items() if k.split(':')[-2] >= str(today)[:7]}
    return candidates, comparisons
