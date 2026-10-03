from pathlib import Path

import pytest

from trip_scout.accommodation import normalize
from trip_scout.config import load_config
from trip_scout.hotels import HotelSearch, parse_hotels
from trip_scout.direct_stays import money
from trip_scout.provider import Deal
from test_free_sources import FREE, Response

DEAL = Deal('FCO', 'CPH', 'Copenhagen', '2027-02-13', '2027-02-18', 78, 'EUR')
PARAMS = dict(q='Copenhagen hotels', check_in_date=DEAL.departure, check_out_date=DEAL.returning, adults=1, currency='EUR')


def payload():
    return {'search_parameters': PARAMS, 'properties': [
        {'name': 'Hostel <untrusted>', 'total_rate': {'lowest': '\u20ac78', 'extracted_lowest': 78}, 'rate_per_night': {'extracted_lowest': 15.6}},
        {'name': 'Nightly only', 'rate_per_night': {'extracted_lowest': 10}},
        {'name': 'Other currency', 'total_rate': {'lowest': '$50', 'extracted_lowest': 50}}]}


def test_only_total_for_matching_currency_dates_and_party():
    rows = parse_hotels(payload(), DEAL, 1, PARAMS, 10)
    assert len(rows) == 1
    stay = normalize('google_hotels', rows, DEAL)[0]
    assert stay['total'] == 78 and stay['nightly'] == 15.6
    assert stay['kind'] == 'accommodation (room type unconfirmed)'
    assert 'checkin=2027-02-13' in stay['url']
    for field, wrong in [('adults', 3), ('currency', 'USD'), ('check_in_date', '2030-01-01'), ('q', 'Other city hotels')]:
        data = payload() | {'search_parameters': PARAMS | {field: wrong}}
        with pytest.raises(ValueError):
            parse_hotels(data, DEAL, 1, PARAMS, 10)


def test_hotels_share_persisted_free_cap_and_scan_bound(monkeypatch, tmp_path):
    monkeypatch.setenv('SERPAPI_API_KEY', 'private-key')
    calls = []
    class Client:
        def get(self, url, params, timeout):
            if url.endswith('account.json'):
                return Response(FREE)
            calls.append(params)
            return Response(payload() | {'search_parameters': params})
    config = load_config(Path('config/settings.json'))
    search = HotelSearch(config, tmp_path / 'usage.json', Client())
    search(DEAL, 1)
    search(DEAL, 1)
    with pytest.raises(RuntimeError, match='per-scan'):
        search(DEAL, 1)
    assert len(calls) == 2
    assert 'private-key' not in (tmp_path / 'usage.json').read_text()
    config['serpapi_monthly_limit'] = 2
    with pytest.raises(ValueError, match='exhausted'):
        HotelSearch(config, tmp_path / 'usage.json', Client())(DEAL, 1)
    assert len(calls) == 2


@pytest.mark.parametrize('text,expected', [('\u20ac1.234,56 total', 1234.56), ('EUR 1,234.56', 1234.56), ('\u20ac1.234 total', 1234), ('\u20ac15,60', 15.6), ('\u20ac1\u202f234', 1234)])
def test_locale_prices_do_not_make_expensive_stays_look_cheap(text, expected):
    assert money(text, 'EUR') == expected
