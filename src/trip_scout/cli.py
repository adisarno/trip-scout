import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from .accommodation import enrich, search_links
from .alerts import deliver_pending, load_state, mail_settings, save_state, select_deals
from .config import load_config
from .discovery import bargain_candidates, discover, filter_live_dates
from .preview import demo_html, move_demo_dates, move_stay_dates, render_observations
from .provider import parse_page
from .report import prepare_trip_pending, render_trips, select_flights, select_trips, summary
from .schedules import enrich_schedules


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    load_dotenv(override=False)
    parser = argparse.ArgumentParser(description='Trip Scout: unusually cheap flights and matching stays')
    parser.add_argument('--config', type=Path, default=Path('config/settings.json'))
    parser.add_argument('--state', type=Path, default=Path('data/state.json'))
    parser.add_argument('--cache', type=Path, default=Path('data/accommodation-cache.json'))
    parser.add_argument('--usage', type=Path, default=Path('data/usage.json'))
    parser.add_argument('--output', type=Path, default=Path('output'))
    parser.add_argument('--dry-run', action='store_true', help='Preview without email/history changes; persist free search reservations for live queries')
    parser.add_argument('--full-scan', action='store_true', help='Check all configured departure windows now; SerpApi queries still share the free cap')
    parser.add_argument('--quiet', action='store_true', help='Keep itinerary details out of public Actions logs')
    parser.add_argument('--fixture', type=Path, help='Offline sample flights; requires --dry-run')
    parser.add_argument('--stay-fixture', type=Path, help='Offline accommodation datasets; requires --fixture')
    args = parser.parse_args()
    if args.fixture and not args.dry_run:
        parser.error('--fixture requires --dry-run')
    if args.stay_fixture and not args.fixture:
        parser.error('--stay-fixture requires --fixture')
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    try:
        config = load_config(args.config)
        if args.full_scan:
            config['max_windows_per_scan'] = 12
        state = load_state(args.state)
        args.output.mkdir(parents=True, exist_ok=True)
        if not args.dry_run:
            sender, recipients, key = mail_settings()
            if state.get('pending'):
                deliver_pending(state, args.state, sender, recipients, key)
                print('Delivered pending digest; next run resumes discovery.')
                return 0
        today = datetime.now(ZoneInfo('Europe/Rome')).date()
        if args.fixture:
            deals, _ = parse_page(json.loads(args.fixture.read_text(encoding='utf-8')))
            deals, demo_delta = move_demo_dates(deals, today)
            candidates, comparisons, errors = deals, {}, []
        else:
            deals, errors = discover(config, today, state, usage_path=args.usage)
            deals = filter_live_dates(deals, config, today)
            candidates, comparisons = bargain_candidates(deals, config, state, today)
        candidates = select_deals(candidates, config, {'alerts': {}}, comparisons)
        schedule_notes = []
        if not args.fixture:
            candidates, schedule_notes = enrich_schedules(candidates, config, state, args.usage)
        if config['accommodation']['enabled']:
            fixture = json.loads(args.stay_fixture.read_text(encoding='utf-8')) if args.stay_fixture else {} if args.fixture else None
            if args.fixture and fixture:
                fixture = move_stay_dates(fixture, demo_delta)
            cache_path = args.output / 'preview-cache.json' if args.dry_run else args.cache
            if args.dry_run and args.cache.exists():
                cache_path.write_text(args.cache.read_text(encoding='utf-8'), encoding='utf-8')
            trips, stay_errors = enrich(candidates, config, cache_path, fixture=fixture, usage_path=args.usage)
            errors.extend(stay_errors)
        else:
            trips = [{'flight': d.to_dict(), 'stays': [], 'links': search_links(d), 'notes': ['Accommodation lookup disabled.']} for d in candidates]
        for trip in trips:
            d = trip['flight']
            trip['comparison'] = comparisons.get(f"{d['provider']}:{d['origin']}:{d['destination']}:{d['currency']}:return:{d['departure']}:{d['returning']}")
        selected = select_trips(trips, config, state) if config['accommodation']['enabled'] else []
        selected_flights = select_flights(trips, config, state)
        preview = trips
        preview_flights = trips
        editorial = summary(selected) if selected and not args.fixture else ''
        prefix = 'demo-' if args.fixture else ''
        if preview or preview_flights:
            _, html, text = render_trips(preview, editorial, preview_flights, None if args.fixture else state.get('last_scan'),
                                       None if args.fixture else deals, config['highlighted_deals'])
            if args.fixture:
                html = demo_html(html)
            (args.output / (prefix + 'trips.html')).write_text(html, encoding='utf-8')
            if not args.fixture and not args.quiet:
                print(text)
        else:
            html = demo_html('<html><body><p>No demo data.</p></body></html>') if args.fixture else render_observations(deals, state.get('last_scan', {}), errors)
            (args.output / (prefix + 'trips.html')).write_text(html, encoding='utf-8')
        (args.output / (prefix + 'trips.json')).write_text(json.dumps(trips, indent=2), encoding='utf-8')
        diagnostics = {'fares': len(deals), 'discount_candidates': len(candidates), 'email_trips': len(selected),
                       'mode': 'fictional_demo' if args.fixture else 'live',
                       'coverage': {} if args.fixture else state.get('last_scan', {}),
                       'email_flights': len(selected_flights),
                       'errors': errors, 'notes': schedule_notes + [note for trip in trips for note in trip['notes']],
                       'next_window_cursor': state.get('window_cursor', 0)}
        (args.output / (prefix + 'diagnostics.json')).write_text(json.dumps(diagnostics, indent=2), encoding='utf-8')
        if not args.fixture:
            (args.output / 'observations.json').write_text(json.dumps([d.to_dict() | {'booking_url': d.booking_url} for d in deals], indent=2), encoding='utf-8')
            (args.output / 'observations.html').write_text(render_observations(deals, state.get('last_scan', {}), errors), encoding='utf-8')
        else:
            print(f'DEMO ONLY: fictional prices, no live search. Preview: {args.output / "demo-trips.html"}')
        if not args.dry_run:
            if deals:
                state['pending'] = prepare_trip_pending(selected, state, sender, recipients, editorial, selected_flights,
                                                       display_flights=trips, observations=deals,
                                                       highlighted=config['highlighted_deals'], display_trips=trips)
                save_state(args.state, state)
                deliver_pending(state, args.state, sender, recipients, key)
                print('Trip digest delivered.')
            else:
                save_state(args.state, state)
        label = '[DEMO] ' if args.fixture else ''
        print(f'{label}Scanned {len(deals)} fares; {len(candidates)} bargains; {len(selected_flights)} flight alerts; {len(selected)} combined trips; {len(errors)} source failures.')
        for error in errors:
            logging.error('%s', 'Source failure; details are in encrypted diagnostics.' if args.quiet else error)
        missing = bool(candidates and config['accommodation']['enabled'] and not any(t['stays'] for t in trips))
        if missing:
            logging.warning('No accommodation prices; flight bargains can still be emailed. Inspect direct-source diagnostics.')
        # Partial source coverage is recorded, but a delivered/usable proposal is
        # still a successful run. No usable data with failures needs attention.
        return 1 if errors and not deals else 0
    except Exception as exc:
        logging.error('%s: %s', type(exc).__name__, exc)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
