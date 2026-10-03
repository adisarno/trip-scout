"""Keep observed prices, qualified bargains and fictional demos distinct."""
from dataclasses import replace
from datetime import date, timedelta
from html import escape
import json

from bs4 import BeautifulSoup


def move_demo_dates(deals, today):
    if not deals:
        return [], timedelta(0)
    delta = today + timedelta(days=30) - min(date.fromisoformat(d.departure) for d in deals)
    def shift(value):
        return str(date.fromisoformat(value) + delta) if value else ''
    return [replace(d, departure=shift(d.departure), returning=shift(d.returning),
                    arrival_date=shift(d.arrival_date), outbound_departure='', outbound_arrival='',
                    return_departure='', return_arrival='', schedule_note='') for d in deals], delta


def move_stay_dates(fixture, delta):
    fixture = json.loads(json.dumps(fixture))
    for rows in fixture.values():
        for row in rows:
            for key in ('check_in', 'check_out', 'checkIn', 'checkOut'):
                if row.get(key):
                    row[key] = str(date.fromisoformat(row[key]) + delta)
    return fixture


def demo_html(html):
    soup = BeautifulSoup(html, 'html.parser')
    banner = soup.new_tag('div')
    banner['style'] = 'padding:24px;background:#ffe08a;font-size:22px;font-weight:bold'
    banner.string = 'DEMO: fictional prices. No live search. No bookable flights or accommodation.'
    soup.body.insert(0, banner)
    for link in soup.find_all('a'):
        link.name = 'span'
        link.attrs = {}
    for title in soup.find_all(['h2', 'h3']):
        title.insert(0, '[DEMO] ')
    return str(soup)


def render_observations(deals, coverage, errors):
    parts = ['<!doctype html><html><body style="font-family:Arial,sans-serif;max-width:1100px;margin:24px auto">',
             '<h1>Trip Scout: live search</h1>',
             '<p>Prices observed in search results; confirm at checkout. '
             'These are not automatically bargains: a usual-price comparison is required.</p>']
    parts.append(f'<p>{len(deals)} itineraries observed. Allowed horizon: '
                 f'{escape(coverage.get("earliest_departure", ""))} — {escape(coverage.get("latest_return", ""))}. '
                 f'Windows checked: {len(coverage.get("windows", []))}/{coverage.get("total_windows", "?")}. '
                 f'Sources: {escape(", ".join(coverage.get("providers", [])))}.</p>')
    for warning in coverage.get('warnings', []) + errors:
        parts.append('<p>' + escape(warning) + '</p>')
    cheapest = {}
    for d in deals:
        route = (d.origin, d.destination)
        if route not in cheapest or d.price < cheapest[route].price:
            cheapest[route] = d
    if not cheapest:
        parts.append('<p>No prices returned by sources in the checked windows.</p>')
    else:
        parts.append('<h2>Lowest observed price per route</h2><table cellpadding="10"><tr>'
                     '<th>From</th><th>Destination</th><th>Outbound</th><th>Return</th><th>Round trip, 1 adult</th><th>Source</th></tr>')
        for d in sorted(cheapest.values(), key=lambda d: d.price):
            parts.append(f'<tr><td>{escape(d.origin)}</td><td>{escape(d.city)} ({escape(d.destination)})</td>'
                         f'<td>{escape(d.departure)}</td><td>{escape(d.returning)}</td>'
                         f'<td>{d.price:.2f} {escape(d.currency)}</td><td><a href="{escape(d.booking_url, quote=True)}">'
                         f'{escape(d.provider)}</a></td></tr>')
        parts.append('</table>')
    parts.append('<p>Baggage, extras and accommodation excluded. These results do not establish checkout availability '
                 'or a discount against usual prices.</p></body></html>')
    return ''.join(parts)
