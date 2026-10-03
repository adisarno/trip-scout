from dataclasses import replace
from datetime import date
import json
from pathlib import Path

from trip_scout.accommodation import enrich
from trip_scout.alerts import select_deals
from trip_scout.config import load_config
from trip_scout.discovery import bargain_candidates
from trip_scout.provider import parse_page
from trip_scout.report import prepare_trip_pending, select_trips


def test_large_discount_is_not_rejected_by_arbitrary_price_caps(tmp_path):
    config = load_config(Path('config/settings.json'))
    assert config['max_price'] is None
    assert config['accommodation']['max_nightly_price'] is None
    assert config['accommodation']['max_trip_price'] is None
    original = parse_page(json.loads(Path('tests/fixtures/fares.json').read_text()))[0][0]
    deal = replace(original, price=600, average_price=1000)
    bargains, comparisons = bargain_candidates([deal], config, {}, date(2030, 1, 1))
    assert select_deals(bargains, config, {'alerts': {}}, comparisons) == [deal]
    config['accommodation']['sources'] = ['airbnb']
    config['accommodation']['party_sizes'] = [1]
    fixture = json.loads(Path('tests/fixtures/stays.json').read_text())
    fixture['airbnb:1'][0]['price_amount'] = 1600
    trips, _ = enrich([deal], config, tmp_path / 'cache.json', fixture=fixture)
    assert trips[0]['stays'][0]['flight_plus_stay'] == 2200
    config['accommodation']['max_trip_price'] = 1000
    assert not enrich([deal], config, tmp_path / 'cache.json', fixture=fixture)[0][0]['stays']


def test_already_alerted_stays_remain_visible_without_advancing_baseline(tmp_path):
    config = load_config(Path('config/settings.json'))
    deal = parse_page(json.loads(Path('tests/fixtures/fares.json').read_text()))[0][0]
    fixture = json.loads(Path('tests/fixtures/stays.json').read_text())
    trips, _ = enrich([deal], config, tmp_path / 'cache.json', fixture=fixture)
    state = {'alerts': {}, 'pending': None}
    selected = select_trips(trips, config, state)
    first = prepare_trip_pending(selected, state, 'from@example.com', ['to@example.com'])
    state['trip_alerts'] = first['trip_baselines']
    assert not select_trips(trips, config, state)
    pending = prepare_trip_pending([], state, 'from@example.com', ['to@example.com'],
                                   flights=[], display_flights=trips, display_trips=trips)
    assert not pending['trip_baselines']
    assert trips[0]['stays'][0]['name'] in pending['text']
    assert '1 flight + stay option' in pending['subject']
