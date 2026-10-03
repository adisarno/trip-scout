"""Bounded direct searches with explicit source, dates, and price semantics."""
import hashlib
import json
import logging
import math
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import requests

from .alerts import save_state
from .direct_stays import direct_search

LOG = logging.getLogger(__name__)
DOMAINS = {"airbnb": "airbnb.com", "booking": "booking.com", "hostelworld": "hostelworld.com", "google_hotels": "google.com"}


def within_limits(option, deal, settings):
    nightly_limit = settings['max_nightly_price']
    trip_limit = settings['max_trip_price']
    return ((nightly_limit is None or option['nightly'] / option['adults'] <= nightly_limit)
            and (trip_limit is None or option['total'] / option['adults'] + deal.price <= trip_limit)
            and (settings['include_shared'] or option['kind'] not in ('shared room', 'dorm bed', 'accommodation (room type unconfirmed)')))


def safe_url(value, source):
    if not isinstance(value, str):
        return None
    parts = urlsplit(value)
    host = parts.hostname or ""
    return value if parts.scheme == "https" and (host == DOMAINS[source] or host.endswith('.' + DOMAINS[source])) else None


def number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) and value > 0 else None
    except (TypeError, ValueError):
        return None


def search_links(deal):
    checkin = deal.arrival_date or deal.departure
    destination = ', '.join(filter(None, [deal.city, deal.country]))
    return {
        "Booking.com": 'https://www.booking.com/searchresults.html?' + urlencode({
            'ss': destination, 'checkin': checkin, 'checkout': deal.returning,
            'group_adults': 1, 'group_children': 0, 'no_rooms': 1, 'selected_currency': deal.currency,
        }),
        "Airbnb": 'https://www.airbnb.com/s/homes?' + urlencode({
            'query': destination, 'checkin': checkin, 'checkout': deal.returning,
            'adults': 1, 'currency': deal.currency,
        }),
        # Universal city entry; date-prefilled Hostelworld routes vary by city ID.
        "Hostelworld (select city/dates)": 'https://www.hostelworld.com/',
        "Hostelz (compare dorms; enter dates)": 'https://www.hostelz.com/search?' + urlencode({'q': destination}),
        "Google Hotels": 'https://www.google.com/travel/hotels?' + urlencode({
            'q': f'{destination} hotels {checkin} to {deal.returning} 1 adult',
        }),
    }


def search_parameters(source, deal, config, adults=1):
    return {'checkIn': deal.arrival_date or deal.departure, 'checkOut': deal.returning,
            'currency': deal.currency, 'location': ', '.join(filter(None, [deal.city, deal.country])),
            'adults': adults, 'maxResults': config['accommodation']['max_results_per_source']}


def normalize(source, records, deal, adults=1):
    if not isinstance(records, list):
        raise ValueError('Accommodation dataset must be an array')
    nights = (date.fromisoformat(deal.returning) - date.fromisoformat(deal.arrival_date or deal.departure)).days
    checkin = deal.arrival_date or deal.departure
    options = []
    for row in records:
        if not isinstance(row, dict) or row.get('currency') != deal.currency:
            continue
        url = safe_url(row.get('bookingUrl') or row.get('url'), source)
        if not url or not row.get('name'):
            continue
        def add(kind, amount, estimated=False, room=''):
            total = number(amount)
            if total:
                options.append({'source': source, 'name': str(row['name']), 'kind': kind,
                                'room': room, 'total': round(total, 2), 'nightly': round(total / nights, 2),
                                'currency': deal.currency, 'estimated': estimated, 'url': url,
                                'adults': adults,
                                'checkin': checkin, 'checkout': deal.returning,
                                'rating': row.get('rating', row.get('overallRating')),
                                'fees_note': 'Fees/taxes inclusion is not verified; check provider checkout.'})
        if source == 'airbnb':
            if row.get('check_in') != checkin or row.get('check_out') != deal.returning:
                continue
            qualifier = str(row.get('price_qualifier', '')).lower()
            if 'total' not in qualifier:
                continue  # Never treat a nightly headline as a full stay quote.
            room = str(row.get('room_type') or row.get('type') or '')
            kind = 'shared room' if 'shared' in room.lower() else 'private room' if 'private' in room.lower() else 'accommodation (room type unconfirmed)'
            add(kind, row.get('price_amount'), room=room)
        elif source in ('booking', 'google_hotels'):
            if row.get('checkIn') != checkin or row.get('checkOut') != deal.returning or row.get('isSoldOut') or row.get('isClosed'):
                continue
            room = str(row.get('roomName') or '')
            kind = 'dorm bed' if 'dorm' in room.lower() else 'private room' if any(x in room.lower() for x in ('single room', 'double room', 'twin room')) else 'accommodation (room type unconfirmed)'
            add(kind, row.get('price'), room=room)
        else:
            # Nightly "from" prices are estimates, not a full-stay bill.
            # These are estimates even when the dated search reports availability.
            rooms = row.get('roomTypes')
            if not isinstance(rooms, list) or not rooms:
                continue  # Catalog prices without dated room types are unusable.
            for room in rooms:
                if not isinstance(room, dict):
                    continue
                nightly = number(room.get('price'))
                kind = room.get('type')
                if not nightly or kind not in ('dorm', 'private'):
                    continue
                if kind == 'private' and (number(room.get('capacity')) or 0) < adults:
                    continue
                multiplier = adults if kind == 'dorm' else 1
                add('dorm bed' if kind == 'dorm' else 'private room', nightly * nights * multiplier,
                    estimated=True, room=str(room.get('name', '')))
    return options


def enrich(deals, config, cache_path: Path, fixture=None, fetch=None, usage_path=Path('data/usage.json')):
    from .hotels import HotelSearch
    hotels = HotelSearch(config, usage_path)
    stay = config['accommodation']
    cache = json.loads(cache_path.read_text(encoding='utf-8')) if cache_path.exists() else {}
    now = datetime.now(timezone.utc)
    trips, errors = [], []
    sources = [(source, adults) for source in stay['sources'] for adults in stay['party_sizes']]
    identities = list(dict.fromkeys((d.city, d.country, d.arrival_date or d.departure, d.returning, d.currency) for d in deals))[:stay['max_trips_per_scan']]
    hotel_plan = set([(identity, adults) for adults in stay['party_sizes'] for identity in identities][:stay['max_google_queries_per_scan']])
    # At most max_trips_per_scan unique date/city searches, reusing quotes across origins.
    searched = set()
    for deal in deals:
        trip = {'flight': deal.to_dict(), 'stays': [], 'links': search_links(deal), 'notes': []}
        identity = (deal.city, deal.country, deal.arrival_date or deal.departure, deal.returning, deal.currency)
        if identity not in searched and len(searched) >= stay['max_trips_per_scan']:
            trip['notes'].append('Accommodation search deferred by per-scan limit.')
            trips.append(trip)
            continue
        searched.add(identity)
        for source, adults in sources:
            body = search_parameters(source, deal, config, adults)
            cache_key = hashlib.sha256(json.dumps(['direct-v2', source, body], sort_keys=True).encode()).hexdigest()
            entry = cache.get(cache_key)
            records = None
            if fixture is not None:
                records = fixture.get(f'{source}:{adults}', [])
            elif entry and entry['options'] and (now - datetime.fromisoformat(entry['fetched_at'])).total_seconds() < stay['cache_hours'] * 3600:
                records = entry['options']
            else:
                try:
                    if source == 'google_hotels' and fetch is None:
                        if (identity, adults) not in hotel_plan:
                            trip['notes'].append(f'Google Hotels, {adults} traveler(s): query deferred by per-scan limit; primary occupancy has priority across cities.')
                            continue
                        records = hotels(deal, adults)
                    else:
                        records = (fetch or direct_search)(source, deal, config, adults)
                    if not isinstance(records, list):
                        raise ValueError('Unexpected search shape')
                except (requests.RequestException, ValueError, RuntimeError, KeyError, TypeError, IndexError) as exc:
                    # Never log exception URLs: query strings can contain credentials.
                    errors.append(f'{source}: {type(exc).__name__}')
                    reason = 'dated prices need partner access; use manual link' if source == 'hostelworld' else f'direct search blocked or incompatible ({type(exc).__name__})'
                    trip['notes'].append(f'{source}, {adults} traveler(s): {reason}.')
            if records is not None:
                if getattr(records, 'diagnostics', None):
                    trip.setdefault('source_diagnostics', []).append({'source': source, 'adults': adults, **records.diagnostics})
                cached = entry is not None and fixture is None and records is entry['options']
                options = records if cached else normalize(source, records, deal, adults)
                for option in options:
                    option['observed_at'] = entry['fetched_at'] if cached else now.isoformat()
                if fixture is None and not cached:
                    cache[cache_key] = {'fetched_at': now.isoformat(), 'options': options}
                # Cached stay prices can be reused across origins; combined totals
                # belong to each flight, not the shared cache or another proposal.
                trip['stays'].extend(dict(option) for option in options)
                if not options:
                    trip['notes'].append(f'{source}, {adults} traveler(s): no usable date-specific quotes returned.')
        eligible = [s for s in trip['stays'] if within_limits(s, deal, stay)]
        trip['accommodation_diagnostics'] = {'usable_quotes': len(trip['stays']), 'within_limits': len(eligible),
            'by_party': {str(adults): {'usable': sum(s['adults'] == adults for s in trip['stays']),
                                     'within_limits': sum(s['adults'] == adults for s in eligible)} for adults in stay['party_sizes']}}
        if trip['stays'] and not eligible:
            trip['notes'].append(f"{len(trip['stays'])} dated quotes rejected by budget/room filters (nightly cap {stay['max_nightly_price']} {deal.currency}/person).")
        # Keep alternatives across sources/types before filling remaining slots.
        eligible.sort(key=lambda s: s['total'])
        chosen, groups = [], set()
        for option in eligible:
            group = (option['adults'], option['kind'])
            if group not in groups:
                chosen.append(option)
                groups.add(group)
        for option in eligible:
            if option not in chosen:
                chosen.append(option)
        trip['stays'] = [s for adults in stay['party_sizes'] for s in
                         [x for x in chosen if x['adults'] == adults][:stay['max_options_per_trip']]]
        for option in trip['stays']:
            option['flight_plus_stay'] = round(deal.price * option['adults'] + option['total'], 2)
            option['group_flight_estimate'] = option['adults'] != 1
        if not trip['stays']:
            trip['notes'].append('No accommodation quote within your limits; trip total unknown.')
        trips.append(trip)
    if fixture is None:
        # Cache only normalized stay options, not raw profiles or contact information.
        cutoff = stay['cache_hours'] * 3600
        cache = {k: v for k, v in cache.items() if (now - datetime.fromisoformat(v['fetched_at'])).total_seconds() < cutoff}
        save_state(cache_path, cache)
    return trips, errors
