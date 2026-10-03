import json
import math
import re
import os
from pathlib import Path


def load_config(path: Path) -> dict:
    config = json.loads(path.read_text(encoding="utf-8"))
    defaults = {
        "origins": ["FCO", "CIA", "NAP"], "destinations": [], "excluded_destinations": [],
        "currency": "EUR", "min_days_ahead": 7, "max_days_ahead": 365,
        "flight_provider": "google", "window_days": 30, "max_windows_per_scan": 2,
        "market_countries": ["it", "de"], "markets_per_window": 1,
        "serpapi_monthly_limit": 200,
        "min_discount_percent": 30, "history_min_days": 7,
        "min_nights": 2, "max_nights": 5, "max_price": None,
        "destination_price_limits": {}, "min_drop_percent": 10,
        "max_deals_per_email": 0, "max_pages_per_origin": 5,
        "highlighted_deals": 5, "max_schedule_checks_per_scan": 1,
        "accommodation": {},
    }
    if not isinstance(config, dict):
        raise ValueError("Configuration must be a JSON object")
    unknown = set(config) - set(defaults)
    if unknown:
        raise ValueError(f"Unknown configuration keys: {sorted(unknown)}")
    config = defaults | config
    if os.getenv("ORIGIN_AIRPORTS", "").strip():
        config['origins'] = [x.strip().upper() for x in os.environ['ORIGIN_AIRPORTS'].split(',') if x.strip()]
    if config['flight_provider'] not in ('google', 'ryanair'):
        raise ValueError('flight_provider must be google or ryanair')
    if (not isinstance(config['market_countries'], list) or not config['market_countries']
            or any(not isinstance(x, str) or not re.fullmatch(r'[a-z]{2}', x) for x in config['market_countries'])):
        raise ValueError('market_countries must contain lowercase two-letter country codes')
    config['market_countries'] = list(dict.fromkeys(config['market_countries']))
    for field, maximum in [('markets_per_window', 10), ('serpapi_monthly_limit', 200)]:
        if type(config[field]) is not int or not 1 <= config[field] <= maximum:
            raise ValueError(f'{field} must be between 1 and {maximum}')
    for field in ("origins", "destinations", "excluded_destinations"):
        if not isinstance(config[field], list) or any(not isinstance(x, str) or not re.fullmatch(r"[A-Z]{3}", x) for x in config[field]):
            raise ValueError(f"{field} must contain uppercase airport IATA codes")
        config[field] = list(dict.fromkeys(config[field]))
    if not config["origins"]:
        raise ValueError("At least one origin is required")
    if not re.fullmatch(r"[A-Z]{3}", str(config["currency"])):
        raise ValueError("currency must be a three-letter currency code")
    for field in ('max_deals_per_email', 'max_schedule_checks_per_scan'):
        if type(config[field]) is not int or config[field] < 0:
            raise ValueError(f'{field} must be a nonnegative integer')
    for field in ("min_days_ahead", "max_days_ahead", "min_nights", "max_nights", "highlighted_deals", "max_pages_per_origin", "window_days", "max_windows_per_scan", "history_min_days"):
        if type(config[field]) is not int or config[field] < 1:
            raise ValueError(f"{field} must be a positive integer")
    if config["min_days_ahead"] > config["max_days_ahead"] or config["min_nights"] > config["max_nights"]:
        raise ValueError("Minimum dates/durations must not exceed maximums")
    if config["max_pages_per_origin"] > 20:
        raise ValueError("max_pages_per_origin must be at most 20")
    if config['max_days_ahead'] > 365 or config['max_windows_per_scan'] > 12:
        raise ValueError('Search horizon must be <=365 days and windows per scan <=12')
    value = config['min_discount_percent']
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value < 100:
        raise ValueError('min_discount_percent must be between 0 and 100 (exclusive)')
    limits = config["destination_price_limits"]
    if not isinstance(limits, dict) or any(not re.fullmatch(r"[A-Z]{3}", k) for k in limits):
        raise ValueError("destination_price_limits must map airport codes to prices")
    for value in [config["max_price"], *limits.values()]:
        if value is None:
            continue
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError("Price limits must be positive finite numbers")
    drop = config["min_drop_percent"]
    if type(drop) not in (int, float) or not math.isfinite(drop) or not 0 <= drop < 100:
        raise ValueError("min_drop_percent must be between 0 and 100 (exclusive)")
    stay_defaults = {"enabled": True, "sources": ["booking", "airbnb", "hostelworld"],
                     "party_sizes": [1, 2, 3], "max_nightly_price": None, "max_trip_price": None,
                     "include_shared": True, "max_trips_per_scan": 3,
                     "max_results_per_source": 10, "max_options_per_trip": 1,
                     "cache_hours": 24, "max_google_queries_per_scan": 2}
    stay = config["accommodation"]
    if not isinstance(stay, dict) or set(stay) - set(stay_defaults):
        raise ValueError("Invalid accommodation configuration keys")
    stay = stay_defaults | stay
    for field in ("enabled", "include_shared"):
        if type(stay[field]) is not bool:
            raise ValueError(f"accommodation.{field} must be boolean")
    if not isinstance(stay["sources"], list) or not stay["sources"] or any(x not in ("booking", "airbnb", "hostelworld", "google_hotels") for x in stay["sources"]):
        raise ValueError("Accommodation sources must include booking, airbnb, hostelworld or google_hotels")
    stay["sources"] = list(dict.fromkeys(stay["sources"]))
    for field in ("max_trips_per_scan", "max_results_per_source", "max_options_per_trip", "cache_hours"):
        if type(stay[field]) is not int or not 1 <= stay[field] <= 100:
            raise ValueError(f"Invalid accommodation.{field}")
    if not isinstance(stay['party_sizes'], list) or not stay['party_sizes'] or any(type(x) is not int or x not in (1, 2, 3) for x in stay['party_sizes']):
        raise ValueError('party_sizes must contain 1, 2 or 3')
    stay['party_sizes'] = list(dict.fromkeys(stay['party_sizes']))
    if type(stay['max_google_queries_per_scan']) is not int or not 0 <= stay['max_google_queries_per_scan'] <= 10:
        raise ValueError('max_google_queries_per_scan must be between 0 and 10')
    for field in ("max_nightly_price", "max_trip_price"):
        if stay[field] is None:
            continue
        if type(stay[field]) not in (int, float) or not math.isfinite(stay[field]) or stay[field] <= 0:
            raise ValueError(f"Invalid accommodation.{field}")
    config["accommodation"] = stay
    return config
