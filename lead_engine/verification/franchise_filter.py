"""
Detects businesses that are large chains/franchises — these already have
enterprise-level marketing/web support (whether or not the individual
outlet has its own site), so they're a poor fit for KTZ's outreach.

Two real signals are used, never a guess:
1. OSM's "brand" tag — a genuine, sourced field OSM uses to mark chain
   locations (e.g. brand=McDonald's). See osm_client.py.
2. A known-chains name list — since Google Places has no equivalent field,
   this is a curated list of well-known South African / international
   franchise brands relevant to KTZ's target categories. It's necessarily
   incomplete; a business not on this list is simply not flagged, not
   assumed independent.

This list will need occasional additions as new categories/regions come up
— it is not, and can't be, exhaustive.
"""
import re

# Well-known chains across KTZ's target categories (restaurants, hotels,
# salons, security, car dealerships, etc.). Lowercase, matched as a whole
# word/phrase against the normalized business name.
KNOWN_FRANCHISE_NAMES = {
    # Fast food / restaurants
    "kfc", "mcdonalds", "nandos", "steers", "spur", "wimpy", "debonairs",
    "debonairs pizza", "chicken licken", "hungry lion", "ocean basket",
    "panarottis", "romans pizza", "fishaways", "st elmos", "mugg and bean",
    "vida e caffe", "starbucks", "burger king", "pizza hut", "dominos",
    "subway", "krispy kreme", "wakaberry", "kauai", "food lovers market",
    "pizza perfect",
    # Hotels / hospitality
    "protea hotel", "city lodge", "southern sun", "tsogo sun", "premier hotel",
    "holiday inn", "sun international", "aha hotels", "stay easy", "road lodge",
    # Salons / beauty
    "sorbet", "hairhouse warehouse",
    # Security
    "adt", "fidelity", "chubb", "bidvest protea coin",
    # Retail / other common SA franchises
    "pick n pay", "shoprite", "checkers", "woolworths", "spar", "clicks",
    "dischem", "cash crusaders", "pep", "ackermans", "mr price",
}

# Precompute normalized versions for matching
_NORMALIZE_PATTERN = re.compile(r"[^\w\s]")


def _normalize_for_matching(text: str) -> str:
    if not text:
        return ""
    text = text.lower()
    text = text.replace("'", "").replace("\u2019", "")  # drop apostrophes entirely, don't space them out
    text = _NORMALIZE_PATTERN.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def is_franchise(record: dict) -> tuple[bool, str]:
    """
    Determine whether a business is a known chain/franchise.
    Returns (is_franchise: bool, reason: str).
    """
    brand = (record.get("brand") or "").strip()
    if brand:
        return True, f"tagged as part of the '{brand}' chain (OSM brand data)"

    normalized_name = _normalize_for_matching(record.get("name", ""))
    for franchise_name in KNOWN_FRANCHISE_NAMES:
        if franchise_name in normalized_name:
            return True, f"name matches known franchise '{franchise_name}'"

    return False, ""
