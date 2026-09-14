"""
Verification and lead-classification logic: works on already-normalized
lead records (regardless of which source they came from — Google Places,
OSM, etc.) and turns them into a clean, structured DataFrame classified by
how good a fit each business is for KTZ's own outreach.

Every source module (places_client.py, osm_client.py) is responsible for
producing records in this common shape:
    name, address, phone, website, rating, review_count,
    business_status, category, place_id, source

KTZ sells websites/apps/digital marketing, so the ideal lead is a business
with NO website (they need what KTZ sells) that's also reachable (has a
phone number). A business that already has a website is a weak lead for
this purpose — the "no website" condition is what actually makes someone
a prospect in the first place.
"""
import re

import pandas as pd

# When the same business appears from more than one source, keep the copy
# from whichever source comes first in this list (Google has richer data —
# reviews, ratings, verified contact info — so it's preferred over OSM).
SOURCE_PRIORITY = {"google": 0, "osm": 1}

# Common business-name suffixes that cause otherwise-identical businesses to
# look different when comparing names across sources (one might tag "Nando's",
# another "Nando's (Pty) Ltd"). Stripped out before comparing.
NAME_SUFFIXES = re.compile(
    r"\b(pty ltd|pty\.? ltd\.?|\(pty\) ltd|cc|inc\.?|ltd\.?)\b", re.IGNORECASE
)


def _normalize_name(name: str) -> str:
    """Reduce a business name to a comparable key for cross-source deduplication."""
    if not name:
        return ""
    name = name.lower()
    name = name.replace("'", "").replace("\u2019", "")  # drop apostrophes entirely, don't space them out
    name = re.sub(r"[^\w\s]", " ", name)  # strip remaining punctuation (parens, etc.)
    name = re.sub(r"\s+", " ", name).strip()
    name = NAME_SUFFIXES.sub("", name)  # now suffix words are cleanly space-separated
    name = re.sub(r"\s+", " ", name).strip()
    return name


def classify_lead(record: dict) -> tuple[str, str]:
    """
    Classify a business by how good a lead it is for KTZ.
    Returns (lead_quality, reason).

    lead_quality is one of:
      - "hot_lead":             no website, has a phone — best, actionable
      - "needs_manual_contact": no website, no phone — good target, but
                                 needs another way to reach them
      - "not_a_priority":       already has a website — weak fit right now
    """
    has_website = bool(record.get("website"))
    has_phone = bool(record.get("phone"))

    if has_website:
        return "not_a_priority", "already has a website"

    if has_phone:
        return "hot_lead", "no website, reachable by phone"

    return "needs_manual_contact", "no website, no phone on file"


def build_lead_dataframe(records: list[dict]) -> pd.DataFrame:
    """
    Takes a list of already-normalized records (possibly from multiple
    sources), filters out closed businesses, dedupes both within a source
    and across sources, classifies lead quality, and returns a clean
    DataFrame.
    """
    df = pd.DataFrame(records)
    if df.empty:
        return df

    # Drop permanently/temporarily closed businesses
    df = df[df["business_status"] == "OPERATIONAL"].copy()

    # Dedupe within a single source on place_id (unique per source)
    df = df.drop_duplicates(subset="place_id")

    # Cross-source dedupe: same business found via both Google and OSM.
    # Sort so the preferred source's copy comes first, then drop later
    # duplicates matching on normalized name.
    df["_name_key"] = df["name"].apply(_normalize_name)
    df["_source_rank"] = df["source"].map(SOURCE_PRIORITY).fillna(99)
    df = df.sort_values("_source_rank")
    df = df.drop_duplicates(subset="_name_key", keep="first")
    df = df.drop(columns=["_name_key", "_source_rank"])

    # Classify each lead by fit for KTZ's outreach
    classifications = df.apply(lambda row: classify_lead(row.to_dict()), axis=1)
    df["lead_quality"] = classifications.apply(lambda x: x[0])
    df["lead_notes"] = classifications.apply(lambda x: x[1])

    # Hot leads first, then needs-manual-contact, then low priority
    quality_order = {"hot_lead": 0, "needs_manual_contact": 1, "not_a_priority": 2}
    df["_quality_rank"] = df["lead_quality"].map(quality_order)
    df = df.sort_values("_quality_rank").drop(columns=["_quality_rank"])

    return df.reset_index(drop=True)
