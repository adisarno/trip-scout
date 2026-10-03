import json
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest

from trip_scout.accommodation import search_parameters, enrich, normalize
from trip_scout.alerts import deliver_pending, load_state, save_state
from trip_scout.config import load_config
from trip_scout.discovery import bargain_candidates, discover, parse_google
from trip_scout.provider import parse_page
from trip_scout.report import prepare_trip_pending, render_trips, select_flights, select_trips

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def config():
    return load_config(ROOT / 'config/settings.json')


@pytest.fixture
def deal():
    return parse_page(json.loads((ROOT / 'tests/fixtures/fares.json').read_text()))[0][0]


@pytest.fixture
def stays():
    return json.loads((ROOT / 'tests/fixtures/stays.json').read_text())


def test_google_average_and_invalid_dates(config):
    row = {'start_date': '2030-06-12', 'end_date': '2030-06-16', 'departure_airport_code': 'CIA',
           'arrival_airport_code': 'MRS', 'price': 40, 'average_price': 120, 'name': 'Marseille',
           'flight_link': 'https://www.google.com/travel/flights?q=test'}
    deals = parse_google({'deals': [row, row | {'start_date': '2029-01-01'}]}, config, date(2030, 6, 1), date(2030, 6, 30))
    assert len(deals) == 1
    candidates, comparisons = bargain_candidates(deals, config, {}, date(2030, 5, 1))
    assert candidates == deals
    assert next(iter(comparisons.values()))['discount_percent'] == 66.7


def test_history_requires_prior_days_and_uses_previous_baseline(config, deal):
    config['min_discount_percent'] = 30
    state = {}
    today = date(2030, 4, 1)
    for day in range(config['history_min_days']):
        assert not bargain_candidates([replace(deal, price=100)], config, state, today + timedelta(days=day))[0]
    assert not bargain_candidates([replace(deal, price=80)], config, state, today + timedelta(days=7))[0]
    assert bargain_candidates([replace(deal, price=60)], config, state, today + timedelta(days=7))[0]


def test_windows_rotate_and_never_exceed_horizon(config, monkeypatch, tmp_path):
    config['market_countries'] = ['it', 'de', 'us']
    config['max_windows_per_scan'] = 1
    config['markets_per_window'] = 2
    monkeypatch.setenv('SERPAPI_API_KEY', 'fake')
    calls = []
    class Response:
        status_code = 200
        def __init__(self, account=False):
            self.account = account
        def json(self):
            return {'plan_monthly_price': 0, 'plan_name': 'Free', 'account_status': 'Active',
                    'plan_searches_left': 250, 'plan_renewal_date': '2030-02-01'} if self.account else {'deals': []}
    class Client:
        def get(self, url, params, timeout):
            if 'account.json' in url:
                return Response(True)
            calls.append(params)
            return Response()
    state = {}
    for _ in range(13):
        discover(config, date(2030, 1, 1), state, Client(), tmp_path / 'usage.json')
    assert len(calls) == 26
    assert len({c['outbound_date'] for c in calls}) == 12
    assert calls[0]['outbound_date'] != calls[2]['outbound_date']
    # The 12-window cycle must not permanently leave one country unchecked.
    first_window = [c['gl'] for c in calls if c['outbound_date'] == calls[0]['outbound_date']]
    assert set(first_window) == {'it', 'de', 'us'}
    assert all(c['outbound_date'].split(',')[1] <= '2031-01-01' for c in calls)


def test_group_quotes_are_separate_and_dorm_estimates_explicit(config, deal, stays, tmp_path):
    config['accommodation']['max_options_per_trip'] = 4
    trips, errors = enrich([deal], config, tmp_path / 'cache.json', fixture=stays)
    assert not errors
    options = trips[0]['stays']
    assert {s['adults'] for s in options} == {1, 2, 3}
    dorm = next(s for s in options if s['source'] == 'hostelworld' and s['adults'] == 3)
    assert dorm['total'] == 18 * 4 * 3
    assert dorm['estimated'] and dorm['group_flight_estimate']
    double = next(s for s in options if s['source'] == 'booking' and s['adults'] == 2)
    assert double['total'] == 150  # Room total must not be multiplied by guests.
    assert search_parameters('airbnb', deal, config, 3)['adults'] == 3


def test_no_guessing_from_nightly_airbnb_wrong_dates_or_currency(deal, stays):
    row = stays['airbnb:1'][0]
    assert not normalize('airbnb', [row | {'price_qualifier': 'night'}], deal)
    assert not normalize('airbnb', [row | {'check_in': '2020-01-01'}], deal)
    assert not normalize('airbnb', [row | {'currency': 'USD'}], deal)
    assert not normalize('airbnb', [row | {'url': 'https://evil.test/'}], deal)


def test_private_hostel_room_price_not_per_bed(deal):
    row = {'name': 'Private', 'url': 'https://www.hostelworld.com/test', 'currency': 'EUR',
           'roomTypes': [{'name': 'Triple', 'type': 'private', 'capacity': 3, 'price': 60}]}
    assert normalize('hostelworld', [row], deal, 3)[0]['total'] == 240
    assert not normalize('hostelworld', [row | {'roomTypes': []}], deal)


def test_no_email_without_stay_and_total_drop_can_alert(config, deal, stays, tmp_path):
    trips, _ = enrich([deal], config, tmp_path / 'cache.json', fixture=stays)
    state = {'version': 1, 'alerts': {}, 'pending': None}
    selected = select_trips(trips, config, state)
    assert selected
    state['pending'] = prepare_trip_pending(selected, state, 'from@example.com', ['to@example.com'])
    class Response:
        status_code = 200
        def json(self):
            return {'id': 'delivered'}
    path = tmp_path / 'state.json'
    save_state(path, state)
    deliver_pending(state, path, 'from@example.com', ['to@example.com'], 'fake', lambda *a, **k: Response())
    assert not select_trips(trips, config, load_state(path))
    assert not select_flights(trips, config, load_state(path))
    for option in trips[0]['stays']:
        option['flight_plus_stay'] *= 0.8
    assert select_trips(trips, config, load_state(path))
    assert not select_flights(trips, config, load_state(path))
    trips[0]['stays'] = []
    assert not select_trips(trips, config, {})


def test_environment_airports(config, monkeypatch):
    monkeypatch.setenv('ORIGIN_AIRPORTS', 'mxp,bgy')
    assert load_config(ROOT / 'config/settings.json')['origins'] == ['MXP', 'BGY']


def test_cache_reuses_direct_quote(config, deal, stays, tmp_path):
    config['accommodation']['sources'] = ['airbnb']
    config['accommodation']['party_sizes'] = [1]
    calls = []
    def fetch(*args):
        calls.append(args)
        return stays['airbnb:1']
    cache = tmp_path / 'cache.json'
    assert enrich([deal], config, cache, fetch=fetch)[0][0]['stays']
    assert enrich([deal], config, cache, fetch=fetch)[0][0]['stays']
    assert len(calls) == 1
    assert calls[0][3] == 1


def test_report_escapes_names_and_marks_estimates(config, deal, stays, tmp_path):
    trips, _ = enrich([replace(deal, city='<script>')], config, tmp_path / 'cache.json', fixture=stays)
    html = render_trips(trips)[1]
    assert '&lt;script&gt;' in html and 'Group flights are estimates; seats unverified' in html


def test_report_shows_both_legs_and_single_round_trip_total(config, deal, tmp_path):
    trips, _ = enrich([deal], config, tmp_path / 'cache.json', fixture={})
    coverage = {'windows': [{'origin': 'CIA', 'from': '2030-06-01', 'to': '2030-06-30'}]}
    _, html, text = render_trips([], flights=trips, coverage=coverage)
    for content in (html, text):
        assert 'Outbound: CIA' in content and '2030-06-12' in content
        assert 'Return: MRS' in content and '2030-06-16' in content
        assert '29.98 EUR' in content and ('Round-trip total' in content or 'round-trip total' in content)
        assert 'Searched dates' in content and '30 Jun 2030' in content
    assert 'Return flight:' not in text


def test_default_scans_spread_dates_and_keep_two_query_budget(config, monkeypatch, tmp_path):
    monkeypatch.setenv('SERPAPI_API_KEY', 'fake')
    from test_free_sources import FREE, Response
    calls = []
    class Client:
        def get(self, url, **kwargs):
            if url.endswith('account.json'):
                return Response(FREE)
            calls.append(kwargs['params'])
            return Response({'deals': []})
    state = {}
    today = date(2026, 10, 3)
    for _ in range(6):
        discover(config, today, state, Client(), tmp_path / 'usage.json')
    assert len(calls) == 12
    assert len({c['outbound_date'] for c in calls}) == 12
    first, second = [date.fromisoformat(c['outbound_date'].split(',')[0]) for c in calls[:2]]
    assert (second - first).days == 180
    assert all(c['type'] == 1 and ',' not in c['departure_id'] for c in calls)


def test_shared_stay_cache_does_not_share_combined_flight_totals(config, deal, stays, tmp_path):
    config['accommodation']['sources'] = ['airbnb']
    config['accommodation']['party_sizes'] = [1]
    trips, _ = enrich([replace(deal, price=20), replace(deal, origin='FCO', price=100)],
                      config, tmp_path / 'cache.json', fetch=lambda *args: stays['airbnb:1'])
    assert trips[0]['stays'][0]['flight_plus_stay'] == 140
    assert trips[1]['stays'][0]['flight_plus_stay'] == 220


def test_flight_only_digest_without_accommodation_and_delivery_baselines(config, deal, tmp_path):
    trips, _ = enrich([deal], config, tmp_path / 'cache.json', fixture={})
    state = {'version': 1, 'alerts': {}, 'pending': None}
    assert not select_trips(trips, config, state)
    flights = select_flights(trips, config, state)
    assert flights
    state['pending'] = prepare_trip_pending([], state, 'from@example.com', ['to@example.com'], flights=flights)
    html = state['pending']['html']
    assert html.index('Flights only') < html.index('Flight + stay')
    assert '29.98 EUR' in html and 'No verified stay prices' in html
    assert not state['pending']['trip_baselines']
    class Response:
        status_code = 500
        def json(self):
            return {'id': 'accepted'}
    path = tmp_path / 'state.json'
    response = Response()
    with pytest.raises(RuntimeError):
        deliver_pending(state, path, 'from@example.com', ['to@example.com'], 'fake', lambda *a, **k: response)
    assert not state.get('flight_alerts') and state['pending']
    response.status_code = 200
    deliver_pending(state, path, 'from@example.com', ['to@example.com'], 'fake', lambda *a, **k: response)
    assert not select_flights(trips, config, load_state(path))


def test_flight_price_drop_can_alert_without_combined_total_drop(config, deal, stays, tmp_path):
    original, _ = enrich([deal], config, tmp_path / 'cache.json', fixture=stays)
    state = {'version': 1, 'alerts': {}, 'pending': None}
    selected = select_trips(original, config, state)
    pending = prepare_trip_pending(selected, state, 'from@example.com', ['to@example.com'])
    state['trip_alerts'] = pending['trip_baselines']
    state['flight_alerts'] = pending['flight_baselines']
    cheaper, _ = enrich([replace(deal, price=24)], config, tmp_path / 'cache.json', fixture=stays)
    assert select_flights(cheaper, config, state)
    assert not select_trips(cheaper, config, state)


def test_cli_previews_flight_bargain_even_without_stays(tmp_path, monkeypatch):
    from trip_scout.cli import main
    monkeypatch.setattr('sys.argv', ['trip-scout', '--dry-run', '--fixture', str(ROOT / 'tests/fixtures/fares.json'),
                                   '--output', str(tmp_path / 'output'), '--state', str(tmp_path / 'state.json')])
    assert main() == 0
    html = (tmp_path / 'output/demo-trips.html').read_text(encoding='utf-8')
    assert 'Flights only' in html and 'Flight + stay' in html
    assert 'fictional prices' in html and 'No verified stay prices' in html
    assert '2030' not in html and '<a ' not in html
    assert not (tmp_path / 'output/trips.html').exists()
    assert not (tmp_path / 'state.json').exists()
