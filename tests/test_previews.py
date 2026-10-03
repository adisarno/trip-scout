import json
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

from trip_scout.cli import main
from trip_scout.config import load_config
from trip_scout.discovery import filter_live_dates
from trip_scout.preview import move_demo_dates, move_stay_dates, render_observations
from trip_scout.provider import Deal


def test_live_dates_reject_far_future_and_returns_beyond_horizon():
    config = load_config(Path('config/settings.json'))
    today = date(2026, 10, 2)
    valid = Deal('CIA', 'MRS', 'Marseille', '2026-11-25', '2026-11-29', 30, 'EUR')
    far = replace(valid, departure='2030-06-12', returning='2030-06-16')
    late_return = replace(valid, departure='2027-10-01', returning='2027-10-06')
    last_day = replace(valid, departure='2027-09-29', returning='2027-10-02')
    assert filter_live_dates([valid, far, late_return, last_day], config, today) == [valid, last_day]


def test_demo_stay_and_flight_dates_move_together():
    deal = Deal('CIA', 'MRS', 'Marseille', '2030-06-12', '2030-06-16', 30, 'EUR')
    today = date(2026, 10, 2)
    deals, delta = move_demo_dates([deal], today)
    fixture = {'airbnb:1': [{'check_in': deal.departure, 'check_out': deal.returning}]}
    shifted = move_stay_dates(fixture, delta)
    assert date.fromisoformat(deals[0].departure) == today + timedelta(days=30)
    assert shifted['airbnb:1'][0]['check_in'] == deals[0].departure
    assert shifted['airbnb:1'][0]['check_out'] == deals[0].returning
    assert fixture['airbnb:1'][0]['check_in'] == '2030-06-12'


def test_cli_live_observations_and_demo_do_not_overwrite_each_other(tmp_path, monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    today = datetime.now(ZoneInfo('Europe/Rome')).date()
    deal = Deal('CIA', 'MRS', 'Marseille', str(today + timedelta(days=30)),
                str(today + timedelta(days=34)), 30, 'EUR')
    far = replace(deal, departure='2099-06-12', returning='2099-06-16')
    monkeypatch.setattr('trip_scout.cli.discover', lambda *a, **k: ([deal, far], []))
    args = ['trip-scout', '--dry-run', '--state', str(tmp_path / 'state.json'), '--output', str(tmp_path)]
    monkeypatch.setattr('sys.argv', args)
    assert main() == 0
    live_html = (tmp_path / 'trips.html').read_text(encoding='utf-8')
    assert '30.00 EUR' in live_html and 'live search' in live_html
    assert '2099' not in live_html
    rows = json.loads((tmp_path / 'observations.json').read_text())
    assert len(rows) == 1
    fixture = Path('tests/fixtures/fares.json').resolve()
    monkeypatch.setattr('sys.argv', args + ['--fixture', str(fixture)])
    assert main() == 0
    assert (tmp_path / 'trips.html').read_text(encoding='utf-8') == live_html
    assert (tmp_path / 'demo-trips.html').exists()


def test_observed_prices_are_not_mislabelled_as_bargains():
    deal = Deal('CIA', 'MRS', '<script>', '2026-11-25', '2026-11-29', 30, 'EUR')
    html = render_observations([deal], {'providers': ['ryanair'], 'windows': []}, [])
    assert 'These are not automatically bargains' in html
    assert '&lt;script&gt;' in html and '30.00 EUR' in html
