"""
Handles sourcing business leads from OpenStreetMap (free, no API key):
- Nominatim: geocodes a location name into a center point (lat/lon)
- Overpass API: queries businesses by tag within a radius of that point

Normalizes results into the pipeline's common record shape (see verify.py).
"""
import time

import requests

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

# Nominatim's free public server occasionally times out or drops connections
# under load. Retrying a couple of times with a short pause resolves most of
# these — the same issue Overpass has, handled with mirrors instead since
# there's only one Nominatim endpoint to retry against.
GEOCODE_MAX_ATTEMPTS = 3
GEOCODE_RETRY_DELAY_SECONDS = 3

# Multiple public Overpass mirrors, tried in order. The free public servers
# are prone to intermittent 500/502/504 errors under load, so falling back
# to another mirror is far more reliable than hoping one server responds.
OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
]

# Required by Nominatim's usage policy: identify your app with a real contact.
# Replace with your own project name/email before running at any real volume.
USER_AGENT = "KTZ-Lead-Engine/1.0 (contact: jabu@ktzmedia.co.za)"

# Radius searched around the city center, in meters. A full-city administrative
# boundary (e.g. all of Cape Town metro) is too large for Overpass's free public
# servers to search in one request without timing out — a radius keeps the query
# fast and reliable while still covering the main urban area.
DEFAULT_RADIUS_METERS = 8000

# Maps plain-English business types to OpenStreetMap tag(s).
# OSM has no single "business type" field — different categories use
# different tag keys, so this table needs to grow as you add categories.
# "point_only": True means we skip the (heavier) way/polygon query, since
# these categories are essentially always mapped as single points.
BUSINESS_TYPE_TAGS = {
    "restaurants": {"tags": [("amenity", "restaurant")], "point_only": True},
    "guest houses": {"tags": [("tourism", "guest_house")], "point_only": True},
    "hotels": {"tags": [("tourism", "hotel")], "point_only": False},
    "salons": {"tags": [("shop", "hairdresser"), ("shop", "beauty")], "point_only": True},
    "car dealerships": {"tags": [("shop", "car")], "point_only": False},
    "lawyers": {"tags": [("office", "lawyer")], "point_only": True},
    "accountants": {"tags": [("office", "accountant")], "point_only": True},
    "construction companies": {"tags": [("office", "company"), ("craft", "builder")], "point_only": True},
    "security companies": {"tags": [("office", "security")], "point_only": True},
    "medical practices": {"tags": [("amenity", "doctors"), ("amenity", "clinic")], "point_only": True},
    "schools": {"tags": [("amenity", "school")], "point_only": False},
    "tour operators": {"tags": [("office", "travel_agent")], "point_only": True},
}


def _geocode_location(location: str) -> dict:
    """
    Resolve a place name (e.g. 'Johannesburg') into a center point via Nominatim.
    Retries a few times on connection timeouts before giving up, since the
    free public server occasionally drops individual requests under load.
    """
    params = {"q": location, "format": "json", "limit": 1}
    headers = {"User-Agent": USER_AGENT}

    last_error = None
    for attempt in range(1, GEOCODE_MAX_ATTEMPTS + 1):
        try:
            response = requests.get(NOMINATIM_URL, params=params, headers=headers, timeout=15)
            response.raise_for_status()
            results = response.json()
            break
        except requests.exceptions.RequestException as e:
            last_error = e
            if attempt < GEOCODE_MAX_ATTEMPTS:
                print(f"Nominatim request failed (attempt {attempt}/{GEOCODE_MAX_ATTEMPTS}), retrying...")
                time.sleep(GEOCODE_RETRY_DELAY_SECONDS)
    else:
        raise RuntimeError(f"Nominatim geocoding failed after {GEOCODE_MAX_ATTEMPTS} attempts: {last_error}")

    if not results:
        raise ValueError(f"Could not geocode location: '{location}'")

    return {
        "lat": float(results[0]["lat"]),
        "lon": float(results[0]["lon"]),
    }


def _build_query(tags: list[tuple[str, str]], point_only: bool, center: dict, radius_m: int) -> str:
    """Build an Overpass QL query for one or more tag pairs within a radius of a point."""
    around = f"around:{radius_m},{center['lat']},{center['lon']}"

    clauses = []
    for key, value in tags:
        clauses.append(f'node["{key}"="{value}"]({around});')
        if not point_only:
            clauses.append(f'way["{key}"="{value}"]({around});')

    return f"""
    [out:json][timeout:25];
    (
      {"".join(clauses)}
    );
    out center tags;
    """


def _normalize(element: dict) -> dict:
    """Convert one raw Overpass element into the common record shape."""
    tags = element.get("tags", {})

    address_parts = [
        tags.get("addr:housenumber", ""),
        tags.get("addr:street", ""),
        tags.get("addr:suburb", ""),
        tags.get("addr:city", ""),
    ]
    address = " ".join(p for p in address_parts if p)

    return {
        "name": tags.get("name", ""),
        "address": address,
        "phone": tags.get("phone", tags.get("contact:phone", "")),
        "website": tags.get("website", tags.get("contact:website", "")),
        "rating": None,  # OSM has no rating concept
        "review_count": 0,  # OSM has no review concept — will always flag for review
        # OSM doesn't reliably tag closed businesses; disused:* prefix is the main signal.
        "business_status": "OPERATIONAL",
        "category": tags.get("amenity") or tags.get("shop") or tags.get("office") or tags.get("tourism", ""),
        "place_id": f"osm_{element.get('type', '')}_{element.get('id', '')}",
        "source": "osm",
        # A real, sourced signal for chain/franchise outlets — OSM tags chain
        # locations with their parent brand (e.g. brand=McDonald's). Empty
        # if this business isn't tagged as part of a chain.
        "brand": tags.get("brand", ""),
    }


def _query_overpass(query: str) -> dict:
    """
    Try each Overpass mirror in turn, since the free public servers are
    prone to intermittent 500/502/504 errors under load. Returns the parsed
    JSON response from whichever mirror succeeds first.
    """
    headers = {"User-Agent": USER_AGENT}
    last_error = None

    for mirror_url in OVERPASS_MIRRORS:
        try:
            response = requests.post(mirror_url, data={"data": query}, headers=headers, timeout=35)
            if response.ok:
                return response.json()
            last_error = f"{mirror_url} returned {response.status_code}: {response.text[:300]}"
        except requests.exceptions.RequestException as e:
            last_error = f"{mirror_url} failed: {e}"

    raise RuntimeError(f"All Overpass mirrors failed. Last error: {last_error}")


def search_osm(business_type: str, location: str, max_results: int = 20,
                radius_m: int = DEFAULT_RADIUS_METERS) -> list[dict]:
    """
    Search OpenStreetMap for a business type within a radius of a location.
    Returns a list of normalized records (see verify.py for the common shape).
    """
    business_type_key = business_type.strip().lower()
    config = BUSINESS_TYPE_TAGS.get(business_type_key)

    if not config:
        available = ", ".join(sorted(BUSINESS_TYPE_TAGS.keys()))
        raise ValueError(
            f"Unknown business type '{business_type}' for OSM source. "
            f"Available types: {available}"
        )

    center = _geocode_location(location)
    query = _build_query(config["tags"], config["point_only"], center, radius_m)

    data = _query_overpass(query)

    elements = data.get("elements", [])
    records = [_normalize(e) for e in elements if e.get("tags", {}).get("name")]

    return records[:max_results]
