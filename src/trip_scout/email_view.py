"""Compact English digest with inline styles and email-compatible tables."""
from datetime import date, datetime
from html import escape as esc

from .provider import Deal

INK = '#18352e'
MUTED = '#61746d'
ACCENT = '#14745b'
LINE = '#e0e7e2'
MONTHS = ['', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']


def money(value, currency):
    return f'{value:.2f} {currency}'


def day(value):
    parsed = date.fromisoformat(value[:10])
    return f'{parsed.day} {MONTHS[parsed.month]} {parsed.year}'


def date_range(start, end):
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if first.year == last.year:
        return f'{first.day} {MONTHS[first.month]} – {day(end)}'
    return f'{day(start)} – {day(end)}'


def timing(day_value, departure='', arrival=''):
    times = '—'
    if departure:
        times = datetime.fromisoformat(departure).strftime('%H:%M')
        if arrival:
            parsed = datetime.fromisoformat(arrival)
            times += ' → ' + parsed.strftime('%H:%M')
            if str(parsed.date()) != day_value:
                times += ' (' + day(str(parsed.date())) + ')'
    return f'<time datetime="{esc(day_value, quote=True)}">{day(day_value)}</time> · {esc(times)}'


def link(url, label):
    return f'<a href="{esc(url, quote=True)}" style="color:{ACCENT};font-weight:600;text-decoration:underline">{esc(label)}</a>'


def section(title, subtitle=''):
    return (f'<tr><td style="padding:28px 28px 12px"><h2 style="margin:0;font-size:20px;color:{INK}">{esc(title)}</h2>'
            + (f'<p style="margin:6px 0 0;color:{MUTED};font-size:12px">{esc(subtitle)}</p>' if subtitle else '') + '</td></tr>')


def deal_key(d):
    return (d.provider, d.origin, d.destination, d.currency, d.departure, d.returning)


def flight_table(trips, heading, observed=False):
    output = [section(heading, 'Observed prices, without a verified discount.' if observed else '')]
    output.append('<tr><td style="padding:0 28px"><table width="100%" cellspacing="0" cellpadding="0" style="table-layout:fixed;border-collapse:collapse">'
                  f'<thead><tr style="color:{MUTED};font-size:11px;text-align:left"><th width="35%" style="padding:10px 0">DESTINATION</th>'
                  '<th width="40%">OUTBOUND / RETURN</th><th width="25%" style="text-align:right">TOTAL RT</th></tr></thead><tbody>')
    for trip in trips:
        d = Deal(**trip['flight'])
        output.append(f'<tr><td style="padding:13px 8px 13px 0;border-top:1px solid {LINE};vertical-align:top;overflow-wrap:anywhere">'
                      f'{esc(d.city)}<br><span style="font-size:11px;color:{MUTED}">{esc(d.origin)} ↔ {esc(d.destination)}</span></td>'
                      f'<td style="padding:13px 4px;border-top:1px solid {LINE};font-size:12px;vertical-align:top">'
                      f'{timing(d.departure, d.outbound_departure, d.outbound_arrival)}<br>{timing(d.returning, d.return_departure, d.return_arrival)}</td>'
                      f'<td style="padding:13px 0;border-top:1px solid {LINE};text-align:right;vertical-align:top;font-size:12px">'
                      f'{esc(money(d.price, d.currency))}<br>{link(d.booking_url, "View flight")}</td></tr>')
    output.append('</tbody></table></td></tr>')
    return ''.join(output)


def render_email(flights, combined, coverage, observations, highlighted, editorial=''):
    shown = {deal_key(Deal(**t['flight'])) for t in flights}
    other = [d for d in (observations or []) if deal_key(d) not in shown]
    deal_count = f'{len(flights)} ' + ('deal' if len(flights) == 1 else 'deals')
    result_count = f'{len(flights) + len(other)} ' + ('result' if len(flights) + len(other) == 1 else 'results')
    origins = (coverage or {}).get('configured_origins', [])
    other.sort(key=lambda d: (origins.index(d.origin) if d.origin in origins else len(origins), d.price))
    rows = [f'<tr><td style="background:{INK};padding:30px 28px;color:#ffffff">'
            '<p style="margin:0 0 12px;font-size:11px;letter-spacing:2px;color:#b5d5c6">TODAY’S DEALS</p>'
            '<h1 style="font-size:34px;letter-spacing:-1px;line-height:1.1;margin:0">trip scout.</h1>'
            f'<p style="margin:14px 0 0;color:#d5e6dd;font-size:13px">{deal_count} · {result_count} · round-trip prices</p>'
            '</td></tr>']
    text = [f'Trip Scout · {deal_count} · {result_count}', 'Flights only']
    if editorial:
        rows.append(f'<tr><td style="padding:16px 28px;color:{MUTED};font-size:13px">{esc(editorial[:450])}</td></tr>')
    rows.append(section('Flights only', 'Best deals, in your airport priority order.'))
    if not flights:
        rows.append(f'<tr><td style="padding:0 28px 12px;color:{MUTED};font-size:13px">No verified bargains in this scan.</td></tr>')
    for index, trip in enumerate(flights[:highlighted]):
        d = Deal(**trip['flight'])
        comparison = trip.get('comparison') or {}
        discount = f'−{comparison["discount_percent"]:.0f}%' if comparison else ''
        basis = 'Google' if comparison.get('basis', '').startswith('Google') else 'history'
        baseline = f' · {basis} {comparison["baseline"]:.2f}' if comparison else ''
        market = f' · market {d.market.upper()}' if d.market else ''
        routes = [('Outbound', d.origin, d.destination, d.departure, d.outbound_departure, d.outbound_arrival),
                  ('Return', d.destination, d.origin, d.returning, d.return_departure, d.return_arrival)]
        rows.append(f'<tr><td style="padding:18px 28px;border-bottom:1px solid {LINE}"><table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
                    f'<tr><td style="vertical-align:top;padding-right:12px"><strong>TOP {index+1}</strong>'
                    f'<h3 style="font-size:23px;line-height:1.2;letter-spacing:-0.5px;margin:7px 0 3px;color:{INK}">{esc(d.city)}</h3>'
                    f'<p style="font-size:12px;color:{MUTED};margin:0">{esc(d.origin)} ↔ {esc(d.destination)} · {(date.fromisoformat(d.returning)-date.fromisoformat(d.departure)).days} nights{esc(market)}</p></td>'
                    f'<td style="vertical-align:top;text-align:right;width:145px"><strong style="font-size:24px;color:{ACCENT};white-space:nowrap">{esc(money(d.price, d.currency))}</strong>'
                    f'<p style="font-size:11px;color:{MUTED};margin:5px 0">Round-trip total · 1 adult</p>'
                    f'<span style="font-size:12px;color:{ACCENT}">{esc(discount + baseline)}</span></td></tr>')
        for label, origin, destination, value, departure, arrival in routes:
            rows.append(f'<tr><td colspan="2" style="padding-top:12px;font-size:12px;color:{INK}">'
                        f'<span style="color:{MUTED}">{esc(label)}: {esc(origin)} → {esc(destination)}</span><br>'
                        f'{timing(value, departure, arrival)}</td></tr>')
        if d.schedule_note:
            rows.append(f'<tr><td colspan="2" style="padding-top:8px;font-size:11px;color:{MUTED}">Times checked at the listed round-trip price.</td></tr>')
        actions = link(d.booking_url, 'View round trip')
        if trip['links']:
            actions += ' &nbsp;·&nbsp; ' + ' &nbsp;·&nbsp; '.join(link(url, name.split(' (')[0]) for name, url in trip['links'].items() if name in ('Booking.com', 'Airbnb') or name.startswith('Hostelworld'))
        rows.append(f'<tr><td colspan="2" style="padding-top:15px;font-size:12px">{actions}</td></tr></table></td></tr>')
    if len(flights) > highlighted:
        rows.append(flight_table(flights[highlighted:], 'More flight deals'))
    rows.append(section('Flight + stay', 'Totals for the dates and party sizes shown.'))
    if not combined:
        rows.append(f'<tr><td style="padding:0 28px 14px;font-size:13px;color:{MUTED}">No verified stay prices: check the destination links.</td></tr>')
    for trip in combined:
        d = Deal(**trip['flight'])
        rows.append(f'<tr><td style="padding:12px 28px"><h3 style="margin:0 0 10px;font-size:17px;color:{INK}">{esc(d.city)} · {date_range(d.departure, d.returning)}</h3>'
                    '<table width="100%" cellpadding="0" cellspacing="0" style="table-layout:fixed;font-size:12px;border-collapse:collapse"><thead>'
                    f'<tr style="color:{MUTED};text-align:left;font-size:10px"><th width="43%">STAY</th><th width="12%" style="white-space:nowrap;font-size:9px">GUESTS</th>'
                    '<th width="22%" style="text-align:right;white-space:nowrap">STAY TOTAL</th><th width="23%" style="text-align:right">FLIGHT +<br>STAY</th></tr></thead><tbody>')
        for stay in trip['stays']:
            note = ' · estimate' if stay['estimated'] or stay['group_flight_estimate'] else ''
            kind = {'accommodation (room type unconfirmed)': 'Room type unconfirmed',
                    'dorm bed': 'Dorm bed', 'private room': 'Private room'}.get(stay['kind'], stay['kind'])
            stay_dates = '' if (stay['checkin'], stay['checkout']) == (d.departure, d.returning) else '<br>' + date_range(stay['checkin'], stay['checkout'])
            rows.append(f'<tr><td style="padding:12px 6px 12px 0;border-top:1px solid {LINE};overflow-wrap:anywhere">{link(stay["url"], stay["name"])}'
                        f'<br><span style="font-size:11px;color:{MUTED}">{esc(stay["source"])} · {esc(kind)}{stay_dates}</span></td>'
                        f'<td style="border-top:1px solid {LINE};text-align:center">{stay["adults"]}</td>'
                        f'<td style="border-top:1px solid {LINE};text-align:right">{esc(money(stay["total"], stay["currency"]))}</td>'
                        f'<td style="border-top:1px solid {LINE};text-align:right;color:{ACCENT}"><strong>{esc(money(stay["flight_plus_stay"], stay["currency"]))}</strong>'
                        f'<br><span style="font-size:11px;color:{MUTED}">{esc(note.strip(" ·"))}</span></td></tr>')
        rows.append('</tbody></table></td></tr>')
    if other:
        rows.append(flight_table([{'flight': d.to_dict()} for d in other], 'Other search results', observed=True))
    footer = ['Round-trip prices for 1 adult; baggage and extras excluded. Confirm prices and terms through the links.',
              'Local times when available; checks cached up to 7 days. — = time unavailable.',
              'Stays: cheaper options among those found, without verified historical discounts. Group flights are estimates; seats unverified. Taxes and fees may apply.']
    if coverage and coverage.get('windows'):
        periods = 'Searched dates: ' + '; '.join(f'{w.get("origin", "")}: {day(w["from"])} – {day(w["to"])}' for w in coverage['windows'])
        footer.append(periods)
    if origins:
        footer.append('Airport priority: ' + ' → '.join(origins) + '. Other dates rotate in upcoming scans.')
    footer.append('Flight discounts use Google averages or observed history; country settings do not guarantee checkout savings.')
    rows.append(f'<tr><td style="padding:24px 28px;background:#f2f5f1;color:{MUTED};font-size:11px;line-height:1.6">' + '<br>'.join(esc(x) for x in footer) + '</td></tr>')
    for trip in flights:
        d = Deal(**trip['flight'])
        text.extend([f'{d.city} · {money(d.price, d.currency)} round-trip total',
                     f'Outbound: {d.origin} → {d.destination}, {d.departure} {d.outbound_departure[11:16] or "—"}',
                     f'Return: {d.destination} → {d.origin}, {d.returning} {d.return_departure[11:16] or "—"}', d.booking_url])
    text.append('Flight + stay')
    for trip in combined:
        for stay in trip['stays']:
            text.append(f'{trip["flight"]["city"]} · {stay["name"]} · {stay["adults"]} guests · {money(stay["flight_plus_stay"], stay["currency"])} flight + stay · {stay["url"]}')
    if other:
        text.append('Other search results (without a verified discount)')
        text.extend(f'{d.origin} ↔ {d.destination} · {d.departure} / {d.returning} · {money(d.price, d.currency)} round trip · {d.booking_url}' for d in other)
    text.extend(footer)
    html = ('<!doctype html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Trip Scout</title><style>@media(max-width:480px){td{word-wrap:break-word}h3{font-size:20px!important}.shell{width:100%!important}}</style></head>'
            f'<body style="margin:0;padding:20px 0;background:#eaf0eb;color:{INK};font-family:Arial,Helvetica,sans-serif;line-height:1.45">'
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">'
            '<table class="shell" role="presentation" width="680" cellpadding="0" cellspacing="0" style="width:100%;max-width:680px;background:#ffffff;text-align:left">'
            + ''.join(rows) + '</table></td></tr></table></body></html>')
    return html, '\n'.join(text)
