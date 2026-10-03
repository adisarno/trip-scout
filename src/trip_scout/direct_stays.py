"""No scraper-service accounts, proxies, or paid endpoints."""
import re
from datetime import date
from urllib.parse import urlencode, urljoin

import requests
from bs4 import BeautifulSoup


class QuoteRecords(list):
    """Keep safe source-shape diagnostics alongside parsed prices."""
    def __init__(self, records, diagnostics):
        super().__init__(records)
        self.diagnostics = diagnostics


def money(text, currency):
    symbols = {'EUR': '€', 'GBP': '£', 'USD': '$'}
    if currency not in text and symbols.get(currency, '\0') not in text:
        return None
    match = re.search(r'\d[\d.,\s\u00a0\u202f]*', text)
    if not match:
        return None
    value = re.sub(r'\s', '', match.group()).rstrip('.,')
    # Accept English and European grouping without turning 1.234 into 1.23.
    if ',' in value and '.' in value:
        decimal = ',' if value.rfind(',') > value.rfind('.') else '.'
        value = value.replace('.' if decimal == ',' else ',', '').replace(decimal, '.')
    elif ',' in value or '.' in value:
        separator = ',' if ',' in value else '.'
        pieces = value.split(separator)
        if len(pieces[-1]) == 3 and all(len(p) == 3 for p in pieces[1:]):
            value = ''.join(pieces)
        elif len(pieces) == 2 and len(pieces[-1]) <= 2:
            value = '.'.join(pieces)
        else:
            return None
    return float(value)


def airbnb(deal, adults, limit, max_nightly=None):
    from pyairbnb import api, search, standardize
    checkin = deal.arrival_date or deal.departure
    nights = (date.fromisoformat(deal.returning) - date.fromisoformat(checkin)).days
    params = {
        'query': ', '.join(filter(None, [deal.city, deal.country])),
        'checkin': checkin, 'checkout': deal.returning, 'adults': adults,
        'currency': deal.currency,
    }
    if max_nightly is not None:
        params['price_max'] = int(max_nightly * adults * (date.fromisoformat(deal.returning) - date.fromisoformat(checkin)).days)
    url = 'https://www.airbnb.com/s/homes?' + urlencode(params)
    try:
        public_key = api.get('', timeout=20)
        raw = search.get(api_key=public_key, cursor='', check_in=checkin, check_out=deal.returning,
                         ne_lat=0, ne_long=0, sw_lat=0, sw_long=0, zoom_value=0,
                         currency=deal.currency, place_type='', price_min=0, price_max=0,
                         amenities=[], free_cancellation=False, adults=adults, children=0, infants=0,
                         min_bedrooms=0, min_beds=0, min_bathrooms=0, language='en',
                         proxy_url='', hash='', raw_params=search.url_to_raw_params(url), timeout=20)
        rows = raw['data']['presentation']['staysSearch']['results']['searchResults']
    except Exception:
        # The library's exceptions can contain entire HTTP responses.
        raise RuntimeError('Airbnb direct response incompatible or blocked') from None
    records = []
    for row in rows:
        if row.get('__typename') != 'StaySearchResult':
            continue
        primary = row.get('structuredDisplayPrice', {}).get('primaryLine') or {}
        secondary = row.get('structuredDisplayPrice', {}).get('secondaryLine') or {}
        overrides = row.get('listingParamOverrides') or {}
        if isinstance(overrides, dict) and any(overrides.get(key, expected) != expected for key, expected in (('checkin', checkin), ('checkout', deal.returning))):
            continue  # Do not use offers with alternate dates.
        total = secondary.get('price', '')
        if 'total' not in total.lower():
            qualifier = str(primary.get('qualifier', '')).lower().strip()
            full_stay = 'total' in qualifier or re.fullmatch(rf'(?:for\s+)?{nights}\s+nights?', qualifier)
            if not full_stay:
                continue
            total = primary.get('discountedPrice') or primary.get('price', '')
        amount = money(total, deal.currency)
        listing = row.get('demandStayListing', {})
        try:
            listing_id = standardize.decode_listing_id(listing['id'])
            if not str(listing_id).isdigit():
                continue
            name = listing['description']['name']['localizedStringWithTranslationPreference']
        except (KeyError, TypeError, ValueError):
            continue
        if amount:
            records.append({'name': name, 'url': 'https://www.airbnb.com/rooms/' + str(listing_id)
                            + '?' + urlencode({'check_in': checkin, 'check_out': deal.returning, 'adults': adults}),
                            'currency': deal.currency, 'check_in': checkin, 'check_out': deal.returning,
                            'price_qualifier': 'total', 'price_amount': amount})
        if len(records) >= limit:
            break
    if rows and not records and any('total' in str((row.get('structuredDisplayPrice', {}).get('primaryLine') or {}).get('qualifier', '')).lower() for row in rows):
        raise ValueError('Airbnb returned listings but no supported explicit total prices')
    diagnostics = {'raw_results': len(rows), 'parsed_total_quotes': len(records),
                   'price_line_types': sorted({str((r.get('structuredDisplayPrice', {}).get('primaryLine') or {}).get('__typename', 'missing')) for r in rows}),
                   'qualifiers': sorted({str((r.get('structuredDisplayPrice', {}).get('primaryLine') or {}).get('qualifier', 'missing')) for r in rows})}
    return QuoteRecords(records, diagnostics)


def booking(deal, adults, limit, get=requests.get):
    checkin = deal.arrival_date or deal.departure
    params = {'ss': ', '.join(filter(None, [deal.city, deal.country])), 'checkin': checkin,
              'checkout': deal.returning, 'group_adults': adults, 'group_children': 0,
              'no_rooms': 1, 'selected_currency': deal.currency, 'lang': 'en-us', 'order': 'price'}
    response = get('https://www.booking.com/searchresults.html', params=params,
                   headers={'User-Agent': 'Mozilla/5.0', 'Accept-Language': 'en-US,en;q=0.9'}, timeout=25)
    if response.status_code != 200:
        raise RuntimeError(f'Booking blocked or unavailable (HTTP {response.status_code})')
    soup = BeautifulSoup(response.text, 'html.parser')
    for key in ('checkin', 'checkout', 'group_adults'):
        element = soup.find('input', attrs={'name': key})
        if element is None or str(element.get('value')) != str(params[key]):
            raise ValueError('Booking did not confirm the requested dates/occupancy')
    records = []
    for card in soup.select('[data-testid="property-card"]')[:limit]:
        title = card.select_one('[data-testid="title"]')
        link = card.select_one('a[data-testid="title-link"]')
        price = card.select_one('[data-testid="price-and-discounted-price"]')
        if not all((title, link, price)) or 'total' not in card.get_text(' ', strip=True).lower():
            continue
        records.append({'name': title.get_text(strip=True), 'bookingUrl': urljoin(response.url, link['href']),
                        'currency': deal.currency, 'checkIn': checkin, 'checkOut': deal.returning,
                        'price': money(price.get_text(' ', strip=True), deal.currency), 'roomName': ''})
    return records


def direct_search(source, deal, config, adults):
    limit = config['accommodation']['max_results_per_source']
    if source == 'airbnb':
        return airbnb(deal, adults, limit, config['accommodation']['max_nightly_price'])
    if source == 'booking':
        return booking(deal, adults, limit)
    # Public Hostelworld pages serve catalog "from" prices even with URL dates.
    # They cannot establish dated availability or a price for this party.
    raise RuntimeError('Hostelworld dated prices require partner access; use the manual search link')
