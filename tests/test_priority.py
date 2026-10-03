from dataclasses import replace
from datetime import date
from pathlib import Path

from trip_scout.alerts import select_deals
from trip_scout.config import load_config
from trip_scout.discovery import discover
from trip_scout.provider import Deal
from trip_scout.report import render_trips, select_flights

from test_free_sources import FREE, Response


def test_primary_always_searched_and_every_secondary_gets_every_window(monkeypatch, tmp_path):
    monkeypatch.setenv('SERPAPI_API_KEY', 'secret')
    config = load_config(Path('config/settings.json'))
    config['origins'] = ['FCO', 'CIA', 'NAP']
    calls = []
    class Client:
        def get(self, url, **kwargs):
            if url.endswith('account.json'):
                return Response(FREE)
            calls.append(kwargs['params'])
            return Response({'deals': []})
    state = {'window_origin_cursors': {'7:36': 2}}
    for _ in range(24):
        discover(config, date(2026, 10, 3), state, Client(), tmp_path / 'usage.json')
    assert len(calls) == 48
    assert all(c['departure_id'] == 'FCO' for c in calls[::2])
    for airport in config['origins']:
        assert len({c['outbound_date'] for c in calls if c['departure_id'] == airport}) == 12
    assert [c['departure_id'] for c in calls[:4]] == ['FCO', 'CIA', 'FCO', 'NAP']


def test_env_order_is_preserved_in_selection_and_email(monkeypatch):
    monkeypatch.setenv('ORIGIN_AIRPORTS', 'FCO, CIA, NAP')
    config = load_config(Path('config/settings.json'))
    nap = Deal('NAP', 'ARN', 'Stockholm', '2026-11-19', '2026-11-22', 20, 'EUR', average_price=200)
    fco = replace(nap, origin='FCO', price=60, average_price=100)
    cia = replace(nap, origin='CIA', price=30, average_price=100)
    assert select_deals([nap, cia, fco], config, {'alerts': {}}) == [fco, cia, nap]
    trips = [{'flight': d.to_dict(), 'stays': [], 'links': {}, 'notes': [],
              'comparison': {'discount_percent': (1-d.price/d.average_price)*100, 'baseline': d.average_price, 'basis': 'test'}}
             for d in [nap, cia, fco]]
    assert [t['flight']['origin'] for t in select_flights(trips, config, {})] == config['origins']
    html = render_trips([], flights=trips, coverage={'configured_origins': config['origins']}, highlighted=1)[1]
    assert html.index('FCO ↔ ARN') < html.index('CIA ↔ ARN') < html.index('NAP ↔ ARN')
    assert '<strong>TOP 1' in html


def test_priority_follows_changed_environment_instead_of_hardcoded_fco(monkeypatch, tmp_path):
    monkeypatch.setenv('ORIGIN_AIRPORTS', 'NAP,FCO,CIA')
    monkeypatch.setenv('SERPAPI_API_KEY', 'secret')
    config = load_config(Path('config/settings.json'))
    calls = []
    class Client:
        def get(self, url, **kwargs):
            if url.endswith('account.json'):
                return Response(FREE)
            calls.append(kwargs['params']['departure_id'])
            return Response({'deals': []})
    discover(config, date(2026, 10, 3), {}, Client(), tmp_path / 'usage.json')
    assert calls == ['NAP', 'FCO']
