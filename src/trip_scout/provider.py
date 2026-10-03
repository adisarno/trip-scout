import logging
import math
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from urllib.parse import urlencode

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

LOG = logging.getLogger(__name__)
ENDPOINT = "https://www.ryanair.com/api/farfnd/v4/roundTripFares"


@dataclass(frozen=True)
class Deal:
    origin: str
    destination: str
    city: str
    departure: str
    returning: str
    price: float
    currency: str
    country: str = ""
    arrival_date: str = ""
    provider: str = "ryanair"
    link: str = ""
    average_price: float | None = None
    market: str = ""
    market_prices: dict = field(default_factory=dict)
    outbound_departure: str = ""
    outbound_arrival: str = ""
    return_departure: str = ""
    return_arrival: str = ""
    schedule_note: str = ""

    @property
    def key(self):
        # One baseline per route, rather than a new alert for every date variant.
        return f"{self.provider}:{self.origin}:{self.destination}:{self.currency}:return"

    @property
    def booking_url(self):
        if self.link:
            return self.link
        return "https://www.ryanair.com/gb/en/trip/flights/select?" + urlencode({
            "adults": 1, "teens": 0, "children": 0, "infants": 0,
            "dateOut": self.departure, "dateIn": self.returning,
            "originIata": self.origin, "destinationIata": self.destination,
            "isReturn": "true",
        })

    def to_dict(self):
        return asdict(self)


def session():
    client = requests.Session()
    client.headers.update({"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    client.mount("https://", HTTPAdapter(max_retries=Retry(
        total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"], respect_retry_after_header=True,
    )))
    return client


def parse_page(payload: dict) -> tuple[list[Deal], bool]:
    if not isinstance(payload, dict) or not isinstance(payload.get("fares"), list) or payload.get("code"):
        raise ValueError("Ryanair returned an unexpected response; fare finder may have changed")
    deals = []
    for fare in payload["fares"]:
        try:
            outbound, inbound = fare["outbound"], fare["inbound"]
            origin = outbound["departureAirport"]["iataCode"]
            airport = outbound["arrivalAirport"]
            destination = airport["iataCode"]
            if not re.fullmatch(r"[A-Z]{3}", origin) or not re.fullmatch(r"[A-Z]{3}", destination):
                raise ValueError("Invalid airport")
            if inbound["departureAirport"]["iataCode"] != destination or inbound["arrivalAirport"]["iataCode"] != origin:
                raise ValueError("Return route does not match")
            departure = date.fromisoformat(outbound["departureDate"][:10])
            returning = date.fromisoformat(inbound["departureDate"][:10])
            price = float(fare["summary"]["price"]["value"])
            currency = fare["summary"]["price"]["currencyCode"]
            if not math.isfinite(price) or price <= 0 or returning <= departure:
                raise ValueError("Invalid fare or dates")
            city = airport.get("city", {}).get("name") or airport.get("name") or destination
            arrival = date.fromisoformat(outbound.get("arrivalDate", str(departure))[:10])
            if arrival < departure or arrival >= returning:
                raise ValueError("Invalid arrival date")
            deals.append(Deal(origin, destination, str(city), str(departure), str(returning), price, currency,
                              str(airport.get("countryName", "")), str(arrival),
                              outbound_departure=outbound["departureDate"] if 'T' in outbound["departureDate"] else '',
                              outbound_arrival=outbound.get("arrivalDate", '') if 'T' in outbound.get("arrivalDate", '') else '',
                              return_departure=inbound["departureDate"] if 'T' in inbound["departureDate"] else '',
                              return_arrival=inbound.get("arrivalDate", '') if 'T' in inbound.get("arrivalDate", '') else ''))
        except (KeyError, TypeError, ValueError, AttributeError):
            LOG.warning("Skipping malformed fare")
    if payload["fares"] and not deals:
        raise ValueError("No usable fares in a nonempty response")
    return deals, payload.get("nextPage") is not None


def search(config: dict, today: date, client=None) -> tuple[list[Deal], list[str]]:
    client = client or session()
    start = today + timedelta(days=config["min_days_ahead"])
    end = today + timedelta(days=config["max_days_ahead"])
    last_return = min(end + timedelta(days=config['max_nights']), today + timedelta(days=365))
    results, errors = [], []
    for origin in config["origins"]:
        seen_pages = set()
        for page in range(config["max_pages_per_origin"]):
            params = {
                "departureAirportIataCode": origin, "currency": config["currency"],
                "outboundDepartureDateFrom": str(start), "outboundDepartureDateTo": str(end),
                "inboundDepartureDateFrom": str(start),
                "inboundDepartureDateTo": str(last_return),
                "durationFrom": config["min_nights"], "durationTo": config["max_nights"],
                "limit": 20, "offset": page * 20,
            }
            try:
                response = client.get(ENDPOINT, params=params, timeout=30)
                response.raise_for_status()
                deals, more = parse_page(response.json())
                signature = tuple(sorted((d.key, d.departure, d.returning, d.price) for d in deals))
                if signature in seen_pages:
                    note = f'{origin}: pagination returned a repeated page; coverage may be incomplete.'
                    warnings = config.get('_coverage_warnings', [])
                    if note not in warnings:
                        LOG.warning(note)
                        warnings.append(note)
                    break
                seen_pages.add(signature)
                results.extend(d for d in deals if d.origin == origin and d.currency == config["currency"]
                               and start <= date.fromisoformat(d.departure) <= end
                               and date.fromisoformat(d.returning) <= last_return
                               and config["min_nights"] <= (date.fromisoformat(d.returning) - date.fromisoformat(d.departure)).days <= config["max_nights"])
                if not more:
                    break
                if page + 1 == config["max_pages_per_origin"]:
                    LOG.warning("%s: page cap reached; some destinations may be missed", origin)
                    config.get('_coverage_warnings', []).append(f'{origin}: page cap reached; coverage may be incomplete.')
                time.sleep(0.5)
            except (requests.RequestException, ValueError) as exc:
                errors.append(f"{origin}: {type(exc).__name__}")
                LOG.error("Search failed for %s (%s)", origin, type(exc).__name__)
                break
    return results, errors
