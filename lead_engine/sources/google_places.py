"""
Handles all communication with the Google Places API (New), and
normalizes results into the pipeline's common record shape.
"""
import time

import requests

PLACES_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"

# Text Search returns at most 20 results per request, and Google caps total
# pageable results at 60 (3 pages) regardless of how many you ask for.
GOOGLE_HARD_CAP = 60
PAGE_SIZE = 20
# Google requires a short delay before a nextPageToken becomes valid.
PAGE_TOKEN_DELAY_SECONDS = 2

FIELD_MASK = ",".join([
    "places.displayName",
    "places.formattedAddress",
    "places.internationalPhoneNumber",
    "places.websiteUri",
    "places.rating",
    "places.userRatingCount",
    "places.businessStatus",
    "places.id",
    "places.primaryType",
])


def _normalize(place: dict) -> dict:
    """Convert one raw Google Places result into the common record shape."""
    return {
        "name": place.get("displayName", {}).get("text", ""),
        "address": place.get("formattedAddress", ""),
        "phone": place.get("internationalPhoneNumber", ""),
        "website": place.get("websiteUri", ""),
        "rating": place.get("rating", None),
        "review_count": place.get("userRatingCount", 0),
        "business_status": place.get("businessStatus", "UNKNOWN"),
        "category": place.get("primaryType", ""),
        "place_id": place.get("id", ""),
        "source": "google",
        # Google Places (New) has no equivalent "chain brand" field in this
        # API tier — franchise detection for Google-sourced leads relies on
        # the name-keyword list in franchise_filter.py instead.
        "brand": "",
    }


def search_places(business_type: str, location: str, api_key: str, max_results: int = 20) -> list[dict]:
    """
    Search Google Places for a business type in a given location. Pages
    through results automatically to fetch more than the 20-per-request
    limit, up to Google's hard cap of 60 total.

    max_results here is the size of the RAW pool to fetch — later pipeline
    stages (dedup, filtering, scoring) may reduce this further before you
    see final results, so it's normal to ask for more raw results than the
    number of leads you actually want at the end.
    """
    query = f"{business_type} in {location}"
    target = min(max_results, GOOGLE_HARD_CAP)

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": FIELD_MASK + ",nextPageToken",
    }

    all_places = []
    page_token = None

    while len(all_places) < target:
        payload = {"textQuery": query, "maxResultCount": PAGE_SIZE}
        if page_token:
            payload["pageToken"] = page_token

        response = requests.post(PLACES_SEARCH_URL, headers=headers, json=payload, timeout=15)

        if response.status_code >= 400:
            # raise_for_status() alone only gives a generic "403 Forbidden" —
            # Google's actual reason (API not enabled, billing missing, key
            # restricted, etc.) is in the JSON body. Surface it directly so
            # a 403 is actually diagnosable instead of a dead end.
            try:
                detail = response.json().get("error", {})
                reason = detail.get("message") or detail.get("status") or response.text[:300]
            except ValueError:
                reason = response.text[:300]
            raise RuntimeError(f"Google Places API error ({response.status_code}): {reason}")

        data = response.json()

        all_places.extend(data.get("places", []))
        page_token = data.get("nextPageToken")

        if not page_token:
            break  # no more pages available
        time.sleep(PAGE_TOKEN_DELAY_SECONDS)  # token needs a moment to become valid

    return [_normalize(p) for p in all_places[:target]]
