import hashlib
import json
import os
from html import escape
from pathlib import Path

import requests

from .provider import Deal


def load_state(path: Path) -> dict:
    if not path.exists():
        return {"version": 1, "alerts": {}, "pending": None}
    state = json.loads(path.read_text(encoding="utf-8"))
    if state.get("version") != 1 or not isinstance(state.get("alerts"), dict):
        raise ValueError("Invalid alert state; restore a known good copy")
    return state


def save_state(path: Path, state: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def select_deals(deals: list[Deal], config: dict, state: dict, comparisons=None) -> list[Deal]:
    cheapest = {}
    for deal in deals:
        if config["destinations"] and deal.destination not in config["destinations"]:
            continue
        if deal.destination in config["excluded_destinations"]:
            continue
        cap = config["destination_price_limits"].get(deal.destination, config["max_price"])
        if cap is not None and deal.price > cap:
            continue
        previous = state["alerts"].get(deal.key)
        if previous and not (deal.price < previous["price"] and deal.price <= previous["price"] * (1 - config["min_drop_percent"] / 100)):
            continue
        if deal.key not in cheapest or deal.price < cheapest[deal.key].price:
            cheapest[deal.key] = deal
    def rank(deal):
        comparison = (comparisons or {}).get(deal.key + ':' + deal.departure + ':' + deal.returning, {})
        discount = comparison.get('discount_percent',
                                  (1 - deal.price / deal.average_price) * 100 if deal.average_price else 0)
        priority = config['origins'].index(deal.origin) if deal.origin in config['origins'] else len(config['origins'])
        return (priority, -discount, deal.price, deal.key)
    selected = sorted(cheapest.values(), key=rank)
    return selected[:config['max_deals_per_email']] if config['max_deals_per_email'] else selected


def render(deals: list[Deal]) -> tuple[str, str, str]:
    subject = f"Trip Scout: {len(deals)} cheap return trips from {min(d.price for d in deals):.2f} {deals[0].currency}"
    rows, lines = [], []
    for d in deals:
        rows.append(f'<tr><td>{escape(d.origin)} → {escape(d.city)} ({escape(d.destination)})</td>'
                    f'<td>{escape(d.departure)} – {escape(d.returning)}</td>'
                    f'<td><b>{d.price:.2f} {escape(d.currency)}</b></td>'
                    f'<td><a href="{escape(d.booking_url, quote=True)}">Check fare</a></td></tr>')
        lines.append(f"{d.origin} -> {d.city} ({d.destination}): {d.price:.2f} {d.currency}, {d.departure} to {d.returning}\n{d.booking_url}")
    note = "Ryanair return fares for one adult. Baggage, seat selection and other extras may cost more. Prices may change; confirm at booking. Dates and times are local."
    html = ('<!doctype html><html><body style="font-family:Arial,sans-serif;color:#182331;max-width:900px;margin:30px auto">'
            '<h1>Trip Scout flight deals</h1><p>Cheap trips from your chosen airports.</p>'
            '<table cellpadding="12" style="border-collapse:collapse;width:100%"><thead><tr>'
            '<th align="left">Route</th><th align="left">Dates</th><th align="left">Return price</th><th></th>'
            '</tr></thead><tbody>' + ''.join(rows) + '</tbody></table><p>' + note + '</p></body></html>')
    return subject, html, '\n\n'.join(lines) + '\n\n' + note


def mail_settings() -> tuple[str, list[str], str]:
    sender = os.getenv("EMAIL_FROM", "").strip()
    recipients = [x.strip() for x in os.getenv("EMAIL_TO", "").replace(";", ",").split(",") if x.strip()]
    key = os.getenv("RESEND_API_KEY", "").strip()
    if not key or not sender or not recipients:
        raise ValueError("Set RESEND_API_KEY, EMAIL_FROM and EMAIL_TO before sending")
    return sender, recipients, key


def recipient_hash(sender: str, recipients: list[str]) -> str:
    return hashlib.sha256(json.dumps([sender, recipients]).encode()).hexdigest()


def prepare_pending(deals: list[Deal], state: dict, sender: str, recipients: list[str]) -> dict:
    subject, html, text = render(deals)
    pending = {"subject": subject, "html": html, "text": text,
               "deals": [d.to_dict() for d in deals], "recipient_hash": recipient_hash(sender, recipients)}
    # Baselines distinguish successive drops; stable payload allows safe retries.
    seed = json.dumps([pending, state["alerts"]], sort_keys=True)
    pending["idempotency_key"] = "trip-scout/" + hashlib.sha256(seed.encode()).hexdigest()
    return pending


def deliver_pending(state: dict, path: Path, sender: str, recipients: list[str], api_key: str, post=None):
    pending = state["pending"]
    if pending["recipient_hash"] != recipient_hash(sender, recipients):
        raise ValueError("Email settings changed while a digest is pending; restore settings or clear pending explicitly")
    response = (post or requests.post)("https://api.resend.com/emails", headers={
        "Authorization": f"Bearer {api_key}", "Idempotency-Key": pending["idempotency_key"],
    }, json={"from": sender, "to": recipients, "subject": pending["subject"],
             "html": pending["html"], "text": pending["text"]}, timeout=30)
    # Do not print response bodies: they can contain recipient addresses.
    if response.status_code not in (200, 201) or not response.json().get("id"):
        raise RuntimeError(f"Resend delivery failed (HTTP {response.status_code}); digest remains pending")
    state.setdefault('flight_alerts', {}).update(pending.get('flight_baselines', {}))
    for raw in pending["deals"]:
        deal = Deal(**raw)
        state["alerts"][deal.key] = raw
    state["pending"] = None
    state.setdefault('trip_alerts', {}).update(pending.get('trip_baselines', {}))
    save_state(path, state)
