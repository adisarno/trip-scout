import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from trip_scout.alerts import deliver_pending, load_state, prepare_pending, render, save_state, select_deals
from trip_scout.config import load_config
from trip_scout.provider import parse_page, search

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def config():
    return load_config(ROOT / 'config/settings.json')


@pytest.fixture
def deal():
    return parse_page(json.loads((ROOT / 'tests/fixtures/fares.json').read_text()))[0][0]


def test_return_total_and_link(deal):
    assert deal.price == 29.98
    assert 'dateIn=2030-06-16' in deal.booking_url
    assert 'originIata=CIA' in deal.booking_url


def test_duplicate_routes_and_cumulative_drop(config, deal):
    state = {'alerts': {}}
    assert select_deals([deal, replace(deal, price=40)], config, state) == [deal]
    state['alerts'][deal.key] = replace(deal, price=50).to_dict()
    assert select_deals([replace(deal, price=48)], config, state) == []
    assert select_deals([replace(deal, price=46)], config, state) == []
    assert select_deals([replace(deal, price=45)], config, state)


def test_filters_and_destination_caps(config, deal):
    state = {'alerts': {}}
    config['max_price'] = 20
    assert not select_deals([deal], config, state)
    config['destination_price_limits'] = {'MRS': 30}
    assert select_deals([deal], config, state)
    config['excluded_destinations'] = ['MRS']
    assert not select_deals([deal], config, state)


def test_all_affordable_results_remain_ranked_by_discount(config, deal):
    cities = [replace(deal, destination=airport, city=city, price=price, average_price=average)
              for airport, city, price, average in [
                  ('MRS', 'Marseille', 30, 60), ('ARN', 'Stockholm', 42, 143),
                  ('CPH', 'Copenhagen', 58, 186), ('BCN', 'Barcelona', 25, 50),
                  ('NYO', 'Stockholm', 40, 120), ('LHR', 'London', 150, 400)]]
    selected = select_deals(cities, config, {'alerts': {}})
    assert len(selected) == 6
    assert [d.city for d in selected[:2]] == ['Stockholm', 'Copenhagen']
    assert sum(d.city == 'Stockholm' for d in selected) == 2
    assert any(d.price == 150 for d in selected)


def test_failed_delivery_does_not_consume_alert_and_retries_same_payload(tmp_path, deal):
    path = tmp_path / 'state.json'
    state = load_state(path)
    state['pending'] = prepare_pending([deal], state, 'sender@example.com', ['to@example.com'])
    save_state(path, state)
    calls = []
    class Response:
        status_code = 500
        def json(self):
            return {'id': 'sent'}
    def post(*args, **kwargs):
        calls.append(kwargs)
        return Response()
    with pytest.raises(RuntimeError):
        deliver_pending(state, path, 'sender@example.com', ['to@example.com'], 'secret', post)
    assert load_state(path)['alerts'] == {}
    assert load_state(path)['pending']
    Response.status_code = 200
    deliver_pending(state, path, 'sender@example.com', ['to@example.com'], 'secret', post)
    assert calls[0] == calls[1]
    assert load_state(path)['pending'] is None
    assert load_state(path)['alerts'][deal.key]['price'] == deal.price
    assert 'to@example.com' not in path.read_text()


@pytest.mark.parametrize('payload', [{'code': 'InvalidLimit'}, {'fares': [{}]}, {'fares': None}])
def test_provider_schema_failures_are_visible(payload):
    with pytest.raises(ValueError):
        parse_page(payload)


def test_html_escaping(deal):
    assert '&lt;script&gt;' in render([replace(deal, city='<script>')])[1]


def test_paging_and_partial_failures(config, deal):
    payload = json.loads((ROOT / 'tests/fixtures/fares.json').read_text())
    calls = []
    class Response:
        def raise_for_status(self):
            pass
        def json(self):
            return payload
    class Client:
        def get(self, url, params, timeout):
            calls.append(params)
            if params['departureAirportIataCode'] == 'FCO':
                raise ValueError('bad schema')
            return Response()
    deals, errors = search(config, date(2030, 6, 1), Client())
    assert deals == [deal]
    assert len(errors) == 1
    assert calls[1]['inboundDepartureDateTo'] == '2031-06-01'


def test_repeated_provider_pages_do_not_inflate_results(config, deal, monkeypatch):
    payload = json.loads((ROOT / 'tests/fixtures/fares.json').read_text()) | {'nextPage': 1}
    config['origins'] = ['CIA']
    config['_coverage_warnings'] = []
    calls = []
    monkeypatch.setattr('trip_scout.provider.time.sleep', lambda *a: None)
    class Response:
        def raise_for_status(self):
            pass
        def json(self):
            return payload
    class Client:
        def get(self, *args, **kwargs):
            calls.append(kwargs)
            return Response()
    deals, errors = search(config, date(2030, 6, 1), Client())
    assert deals == [deal] and not errors
    assert len(calls) == 2
    assert 'coverage may be incomplete' in config['_coverage_warnings'][0]


@pytest.mark.parametrize('change', [{'origins': []}, {'max_price': -1}, {'min_nights': 6, 'max_nights': 2}, {'currency': 'eu'}, {'typo': 3}])
def test_invalid_config(tmp_path, change):
    path = tmp_path / 'settings.json'
    path.write_text(json.dumps(change))
    with pytest.raises(ValueError):
        load_config(path)
