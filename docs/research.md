# Travel data research — 2 October 2026

## Flight projects

| Project | Relevant ideas / practical limits |
| --- | --- |
| [Flight-Price-Tracker-and-Alerting-Bot](https://github.com/farhadvaseghi/Flight-Price-Tracker-and-Alerting-Bot) | Closest initial architecture: keyless Ryanair fare finder, route baselines, Telegram, Actions and committed state. Its code identified the fare endpoint and schema; Trip Scout is an independent Python implementation. |
| [worldwide-flight-bot](https://github.com/rizabalci/worldwide-flight-bot) | Destination discovery with Travelpayouts, price history and scheduled digests. API access/freshness must be assessed before adding it. |
| [flight-price-monitor](https://github.com/pedrivas/flight-price-monitor) | Configurable routes, budgets, history and scheduled price-drop monitoring. |
| [Flight_Pulse](https://github.com/Gruyanidas/Flight_Pulse) | Python flight alerts using Amadeus, Sheety and email. Current provider availability and airline coverage need independent verification. |
| [flightlight](https://github.com/pranavarora1895/flightlight) | Another example combining flight price tracking with Resend emails. |

## Google and historical comparisons

[SerpApi's Google Flights Deals documentation](https://serpapi.com/google-flights-deals-api) describes flexible departure ranges, trip-length filters and returned fare/average-price comparisons. This matches “cheap to anywhere” discovery better than an exhaustive search over airports × dates. Trip Scout calculates the discount from the fare and reported average, rather than trusting a generated description.

[Google Flights search](https://serpapi.com/google-flights-api) also exposes price insights and exact-date searches. It is a possible follow-up for group flight verification. [Google Travel Explore](https://serpapi.com/google-travel-explore-api) offers destination discovery, but broad hotel figures do not substitute for an actual room quote for the requested dates and occupancy.

[Google Hotels via SerpApi](https://serpapi.com/google-hotels-api) returns hotel and vacation-rental results, including full-stay totals. Trip Scout now uses it as a bounded fallback sharing the verified free flight-search allowance. Generic hotel results may not distinguish a dorm bed from a private room and do not guarantee Airbnb or Hostelworld inventory.

[Skyscanner API access](https://www.partners.skyscanner.net/contact/travel-api) is offered to selected commercial partners. It is not the default for this personal project. SerpApi is a third-party extraction API, not an official Google consumer booking API. Its [pricing](https://serpapi.com/pricing) should be reviewed against configured request volume.

Unlike a product's single SKU, airfare comparisons depend on route, dates, trip length, traveler count and available fare class. The source's average is useful evidence, not a universal lifetime “normal fare.” Trip Scout retains its own daily-low observations for a narrower fallback comparison.

## Accommodation access

| Source | Evidence | Consequence |
| --- | --- | --- |
| Booking.com | [Demand API prerequisites](https://developers.booking.com/demand/docs/getting-started/prerequisites), [accommodation tutorial](https://developers.booking.com/demand/docs/accommodations/accommodation-tutorial) | Official date-specific inventory requires managed affiliate access and credentials. |
| Airbnb | [API terms](https://www.airbnb.com/help/article/3418), [software partner programme](https://news.airbnb.com/announcing-our-2025-preferred-software-partners) | Official programmes primarily support host/software integrations; no simple public guest-search feed was established. |
| Hostelworld | [Affiliate API solution](https://partners.hostelworld.com/solutions/), [partner API docs](https://partner-api.hostelworld.com/) | Official room-type availability/pricing exists but requires partner credentials. |
| Hostelz | [Product updates](https://www.hostelz.com/articles/hostelz-innovations), [terms](https://www.hostelz.com/terms-conditions) | Useful manual comparison for hostel/dorm prices; no supported public search-price API was established in this review. |

## Scraper projects and services

- [johnbalvin/pyairbnb](https://github.com/johnbalvin/pyairbnb) supports dated Airbnb searches and details. It is an unofficial scraper; response contracts can change, and a dated search is necessary to get meaningful prices. Trip Scout now installs version 2.2.2 and queries one result page directly, without a user account, paid proxy or scraper-service token. Only explicit dated full-stay totals are accepted.
- [ScrapingBee/booking-scraper](https://github.com/ScrapingBee/booking-scraper) shows date-specific Booking search extraction with a paid scraping service. Generic nightly prices require careful normalization before being compared with whole-stay totals.
- [spider-rs/web-scraping-examples](https://github.com/spider-rs/web-scraping-examples) includes a Hostelworld example. Browser scrapers increase runtime and operational upkeep; not adopted in the first version.

Apify is not used. No actor calls, token configuration or paid scraping-service fallback are included.

Direct Booking requests returned HTTP 202 challenge pages in local probes. Hostelworld city pages loaded successfully but returned catalog "from" prices, without establishing the requested stay's availability. These are not promoted to dated quotations. Its official partner API requires approval. The program exposes missing-price notes and manual comparison links.

On 3 October, hosted Airbnb responses used explicit full-stay qualifiers such as "for 5 nights" while local responses used "total". The adapter now accepts a night-count qualifier only when it exactly matches the requested stay length, and rejects alternate-date overrides. A hosted verification returned Airbnb quotes for 1, 2 and 3 adults in Tenerife, Lisbon and Berlin. Availability can still change.

## Free quota and regional comparison

[SerpApi pricing](https://serpapi.com/pricing) currently lists a $0 plan with 250 searches per month. Its [Account API](https://serpapi.com/account-api) reports plan price, status, renewal date and remaining credits. Trip Scout rejects paid/unverifiable accounts, caps reservations at 200 per billing cycle, and falls back to keyless Ryanair when unavailable. Requests are reserved before sending; failures and live previews count conservatively against the local ceiling.

The [Google Flights Deals API](https://serpapi.com/google-flights-deals-api) accepts a country `gl` and currency separately. Country-localized searches use EUR throughout; identical route/date offers are grouped with the cheapest retained. This reveals observed offer differences, not a guaranteed saving caused by booking from another country: buyer IP, residence, fare eligibility and airline checkout are not controlled. Different market offers may also differ in carrier or fare terms.

## Decision

Use free-plan Google Flights Deals for the reported fare benchmark and rotating country comparisons. Fall back to Ryanair for keyless airline-limited coverage and local history. Search Airbnb directly for dated stays, attempt bounded Booking extraction, and provide manual Hostelworld/Hostelz links when dated prices cannot be obtained. Emails have independent flight-only and flight-plus-stay sections: missing primary-party stays do not suppress a flight alert. Neither historical accommodation discounts nor universal free scraping reliability are claimed.
