import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from trip_scout.config import load_config
from trip_scout.discovery import cheapest_markets, discover, parse_google
from trip_scout.direct_stays import airbnb, booking, money
from trip_scout.provider import Deal
from trip_scout.quota import FreeQuota


FREE = {'plan_name': 'Free', 'plan_monthly_price': 0, 'account_status': 'Active',
        'plan_searches_left': 250, 'plan_renewal_date': '2030-02-01', 'account_id': 'test'}


def test_google_live_date_field_names():
    config = load_config(Path('config/settings.json'))
    row = {'outbound_date': '2026-11-19', 'return_date': '2026-11-22',
           'departure_airport_code': 'FCO', 'arrival_airport_code': 'ARN',
           'name': 'Stockholm', 'price': 42, 'average_price': 143,
           'flight_link': 'https://www.google.com/travel/flights'}
    deals = parse_google({'deals': [row]}, config, date(2026, 11, 9), date(2026, 12, 8))
    assert len(deals) == 1 and deals[0].price == 42
    assert deals[0].departure == '2026-11-19'
    with pytest.raises(ValueError, match='No usable Google deals'):
        parse_google({'deals': [{'price': 42}]}, config, date(2026, 11, 9), date(2026, 12, 8))


def test_google_success_without_deals_requires_resolved_airport():
    config = load_config(Path('config/settings.json'))
    payload = {'search_metadata': {'status': 'Success'},
               'departure_informations': {'airport_code': 'FCO'}}
    assert parse_google(payload, config, date(2026, 11, 9), date(2026, 12, 8)) == []
    with pytest.raises(ValueError):
        parse_google(payload | {'departure_informations': {}}, config, date(2026, 11, 9), date(2026, 12, 8))


def test_single_window_always_searches_first_airport_without_increasing_query_budget(monkeypatch, tmp_path):
    config = load_config(Path('config/settings.json'))
    config['max_windows_per_scan'] = 1
    config['markets_per_window'] = 2
    monkeypatch.setenv('SERPAPI_API_KEY', 'secret')
    calls = []
    class EmptySearch:
        def get(self, url, **kwargs):
            if url.endswith('account.json'):
                return Response(FREE)
            calls.append(kwargs['params'])
            return Response({'deals': []})
    state = {}
    discover(config, date(2026, 10, 3), state, EmptySearch(), tmp_path / 'usage.json')
    assert [p['departure_id'] for p in calls] == ['FCO', 'FCO']
    state['window_cursor'] = 0
    calls.clear()
    discover(config, date(2026, 10, 3), state, EmptySearch(), tmp_path / 'usage.json')
    assert [p['departure_id'] for p in calls] == ['FCO', 'FCO']


class Response:
    status_code = 200
    def __init__(self, payload):
        self.payload = payload
    def json(self):
        return self.payload


class Client:
    def __init__(self, account):
        self.account = account
    def get(self, *args, **kwargs):
        return Response(self.account)


@pytest.mark.parametrize('changes', [
    {'plan_monthly_price': 25}, {'plan_name': 'Starter'}, {'account_status': 'Inactive'},
    {'plan_monthly_price': '0'}, {'plan_searches_left': None}, {'plan_renewal_date': None},
])
def test_rejects_paid_or_unverifiable_accounts(changes, tmp_path):
    with pytest.raises(ValueError):
        FreeQuota(Client(FREE | changes), 'secret', tmp_path / 'usage.json')
    assert not (tmp_path / 'usage.json').exists()


def test_reservations_persist_across_runs_and_limit_failures(tmp_path):
    path = tmp_path / 'usage.json'
    quota = FreeQuota(Client(FREE), 'secret', path, limit=2)
    quota.reserve()
    fresh = FreeQuota(Client(FREE), 'secret', path, limit=2)
    fresh.reserve()
    with pytest.raises(ValueError):
        fresh.reserve()
    assert list(json.loads(path.read_text()).values()) == [2]
    assert 'secret' not in path.read_text()


def test_provider_remaining_guard(tmp_path):
    quota = FreeQuota(Client(FREE | {'plan_searches_left': 10}), 'secret', tmp_path / 'usage.json')
    with pytest.raises(ValueError):
        quota.reserve()


def test_no_key_falls_back_without_contacting_serpapi(monkeypatch, tmp_path):
    config = load_config(Path('config/settings.json'))
    monkeypatch.delenv('SERPAPI_API_KEY', raising=False)
    calls = []
    monkeypatch.setattr('trip_scout.discovery.search', lambda cfg, today: (calls.append(cfg) or [], []))
    class NoNetwork:
        def get(self, *args, **kwargs):
            raise AssertionError('No SerpApi request permitted')
    discover(config, date(2030, 1, 1), {}, NoNetwork(), tmp_path / 'usage.json')
    assert len(calls) == config['max_windows_per_scan']


@pytest.mark.parametrize('account', [FREE | {'plan_monthly_price': 25}, FREE | {'plan_searches_left': 10}])
def test_paid_or_exhausted_accounts_never_search(monkeypatch, tmp_path, account):
    config = load_config(Path('config/settings.json'))
    monkeypatch.setenv('SERPAPI_API_KEY', 'secret')
    monkeypatch.setattr('trip_scout.discovery.search', lambda *args: ([], []))
    class AccountOnly:
        def get(self, url, **kwargs):
            assert url.endswith('account.json')
            return Response(account)
    assert discover(config, date(2030, 1, 1), {}, AccountOnly(), tmp_path / 'usage.json') == ([], [])


def test_failed_search_still_reserves_quota(monkeypatch, tmp_path):
    config = load_config(Path('config/settings.json'))
    monkeypatch.setenv('SERPAPI_API_KEY', 'secret')
    class FailingSearch:
        def get(self, url, **kwargs):
            response = Response(FREE if url.endswith('account.json') else {})
            if not url.endswith('account.json'):
                response.status_code = 500
            return response
    path = tmp_path / 'usage.json'
    deals, errors = discover(config, date(2030, 1, 1), {}, FailingSearch(), path)
    assert not deals and len(errors) == 2
    assert list(json.loads(path.read_text()).values()) == [2]
    assert 'secret' not in str(errors)


def test_same_itinerary_keeps_cheapest_market_and_its_baseline():
    first = Deal('CIA', 'MRS', 'Marseille', '2030-06-12', '2030-06-16', 60, 'EUR',
                 provider='google', average_price=120, market='it')
    cheap = replace(first, price=40, average_price=100, market='de')
    other_date = replace(first, departure='2030-06-13')
    results = cheapest_markets([first, cheap, other_date])
    assert len(results) == 2
    assert results[0].price == 40 and results[0].average_price == 100
    assert results[0].market_prices == {'it': 60, 'de': 40}


def test_airbnb_only_accepts_explicit_totals_and_keeps_requested_party(monkeypatch):
    from pyairbnb import api, search
    import base64
    deal = Deal('CIA', 'MRS', 'Marseille', '2030-06-12', '2030-06-16', 40, 'EUR')
    row = {'__typename': 'StaySearchResult', 'demandStayListing': {
        'id': base64.b64encode(b'StayListing:12345').decode(),
        'description': {'name': {'localizedStringWithTranslationPreference': 'A room'}}},
        'structuredDisplayPrice': {'primaryLine': {'qualifier': 'total', 'price': '€200'}, 'secondaryLine': None}}
    seen = []
    monkeypatch.setattr(api, 'get', lambda *a, **k: 'public-key')
    def request(**kwargs):
        seen.append(kwargs)
        return {'data': {'presentation': {'staysSearch': {'results': {'searchResults': [row]}}}}}
    monkeypatch.setattr(search, 'get', request)
    assert airbnb(deal, 3, 10)[0]['price_amount'] == 200
    filters = {p['filterName']: p['filterValues'] for p in seen[0]['raw_params']}
    assert filters['adults'] == ['3'] and filters['checkin'] == [deal.departure]
    row['structuredDisplayPrice']['primaryLine']['qualifier'] = 'for 4 nights'
    assert airbnb(deal, 3, 10)[0]['price_amount'] == 200
    row['structuredDisplayPrice']['primaryLine']['qualifier'] = 'for 5 nights'
    assert not airbnb(deal, 3, 10)
    row['structuredDisplayPrice']['primaryLine']['qualifier'] = 'for 4 nights'
    row['listingParamOverrides'] = {'checkin': '2030-06-13'}
    assert not airbnb(deal, 3, 10)
    row['listingParamOverrides'] = {}
    row['structuredDisplayPrice']['primaryLine']['qualifier'] = 'night'
    assert not airbnb(deal, 3, 10)


def test_booking_challenge_is_not_a_zero_price():
    deal = Deal('CIA', 'MRS', 'Marseille', '2030-06-12', '2030-06-16', 40, 'EUR')
    response = Response({})
    response.status_code = 202
    with pytest.raises(RuntimeError):
        booking(deal, 1, 10, lambda *a, **k: response)
    assert money('$20', 'EUR') is None
