from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from trip_scout.config import load_config
from trip_scout.provider import Deal
from trip_scout.schedules import enrich_schedules
from trip_scout.report import render_trips


class Response:
    status_code = 200
    def __init__(self, data):
        self.data = data
    def json(self):
        return self.data


def option(origin, destination, day, price=42):
    return {'type': 'Round trip', 'price': price, 'departure_token': 'outbound-token',
            'flights': [{'departure_airport': {'id': origin, 'time': day + ' 09:10'},
                         'arrival_airport': {'id': destination, 'time': day + ' 12:00'}}]}


def test_both_legs_are_matched_and_cached_without_spending_twice(monkeypatch, tmp_path):
    monkeypatch.setenv('SERPAPI_API_KEY', 'secret')
    config = load_config(Path('config/settings.json'))
    deal = Deal('NAP', 'ARN', 'Stockholm', '2026-11-19', '2026-11-22', 42, 'EUR', provider='google')
    calls = []
    class Client:
        def get(self, url, **kwargs):
            if url.endswith('account.json'):
                return Response({'plan_name': 'Free', 'plan_monthly_price': 0, 'account_status': 'Active',
                                 'plan_searches_left': 250, 'plan_renewal_date': '2026-11-01'})
            params = kwargs['params']
            calls.append(params)
            if params.get('departure_token'):
                return Response({'best_flights': [option('ARN', 'NAP', deal.returning)]})
            return Response({'best_flights': [option('NAP', 'ARN', deal.departure)]})
    state = {}
    now = datetime(2026, 10, 3, tzinfo=timezone.utc)
    result, notes = enrich_schedules([deal], config, state, tmp_path / 'usage.json', Client(), now)
    assert not notes and len(calls) == 2
    assert result[0].outbound_departure == '2026-11-19 09:10'
    assert result[0].return_departure == '2026-11-22 09:10'
    assert all(c['type'] == 1 and c['outbound_date'] == deal.departure and c['return_date'] == deal.returning for c in calls)
    next(iter(state['schedule_cache'].values()))['schedule']['schedule_note'] = 'legacy localized note'
    again, _ = enrich_schedules([deal], config, state, tmp_path / 'usage.json', Client(), now)
    assert again == result and len(calls) == 2


@pytest.mark.parametrize('price, origin, day', [(99, 'NAP', '2026-11-19'),
                                             (42, 'FCO', '2026-11-19'),
                                             (42, 'NAP', '2026-11-20')])
def test_schedule_does_not_attach_wrong_price_route_or_date(monkeypatch, tmp_path, price, origin, day):
    monkeypatch.setenv('SERPAPI_API_KEY', 'secret')
    config = load_config(Path('config/settings.json'))
    deal = Deal('NAP', 'ARN', 'Stockholm', '2026-11-19', '2026-11-22', 42, 'EUR', provider='google')
    from test_free_sources import FREE
    class Client:
        def get(self, url, **kwargs):
            return Response(FREE if url.endswith('account.json') else {'best_flights': [option(origin, 'ARN', day, price)]})
    result, notes = enrich_schedules([deal], config, {}, tmp_path / 'usage.json', Client())
    assert result == [deal] and notes


def test_email_highlights_five_keeps_rest_and_all_observations():
    deals = [Deal('NAP', 'ARN', 'Stockholm', '2026-11-19', '2026-11-22', 42 + n, 'EUR',
                  outbound_departure='2026-11-19 09:10', outbound_arrival='2026-11-19 12:00',
                  return_departure='2026-11-22 15:30', return_arrival='2026-11-22 18:20') for n in range(7)]
    trips = [{'flight': d.to_dict(), 'stays': [], 'links': {}, 'notes': [],
              'comparison': {'discount_percent': 70-n, 'baseline': 150, 'basis': 'test'}} for n, d in enumerate(deals)]
    other = replace(deals[0], destination='CPH', city='Copenhagen', price=500)
    html = render_trips([], flights=trips, observations=[other])[1]
    assert html.count('<strong>TOP ') == 5
    assert 'TOP 6' not in html and 'More flight deals' in html
    assert '48.00 EUR' in html and '500.00 EUR' in html
    assert 'Other search results' in html
    assert '09:10' in html and '15:30' in html and 'round-trip prices' in html
