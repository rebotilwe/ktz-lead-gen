"""
Assembles the final, fully structured lead report matching the required
output schema — combining sourced/verified data, website analysis, and
scoring into one record per business.
"""
from datetime import datetime, timezone

import pandas as pd

from .website_analysis import analyze_website
from .scoring import score_lead

# KTZ operates within South Africa — this is a fixed fact about the business's
# market, not a guessed attribute of any individual lead.
COUNTRY = "South Africa"

REPORT_COLUMNS = [
    "business_name", "industry", "country", "province_or_state", "city", "address",
    "website", "website_status", "phone", "phone_status", "email", "email_status",
    "whatsapp", "facebook", "instagram", "linkedin", "website_quality",
    "mobile_friendly", "ssl", "seo_opportunity", "digital_presence",
    "recommended_service", "lead_score", "lead_priority", "opportunity_reason",
    "data_confidence", "source_information", "research_date",
]


def _phone_status(record: dict) -> str:
    """
    Contact-quality rule: never claim a number is verified just because it
    exists. Google's structured field is treated as reliable; OSM's
    crowdsourced tag is treated as unverified unless corroborated (in which
    case the earlier cross-source merge already kept the Google copy).
    """
    if not record.get("phone"):
        return "Not Found"
    return "Unverified" if record.get("source") == "osm" else "Found"


def build_report(df: pd.DataFrame, business_type: str, location: str) -> pd.DataFrame:
    """
    Takes the verified/classified lead DataFrame (from verify.py) and
    produces the full structured report: one row per lead, matching
    REPORT_COLUMNS, with website analysis and scoring applied.
    """
    if df.empty:
        return pd.DataFrame(columns=REPORT_COLUMNS)

    research_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows = []

    for _, record in df.iterrows():
        record = record.to_dict()
        website = record.get("website", "")

        analysis = analyze_website(website) if website else {
            "website_status": "Not Found", "ssl": False, "mobile_friendly": "Unable to determine",
            "website_quality": "Unable to determine", "seo_opportunity": "Unable to determine",
            "contact_options": "Unable to determine", "whatsapp": False, "email": "Not Found",
            "email_status": "Not Found", "facebook": "Not Found", "instagram": "Not Found",
            "linkedin": "Not Found", "outdated_signals": "Unable to determine",
        }

        scoring_result = score_lead(record, analysis)

        has_social = any(analysis.get(p, "Not Found") != "Not Found" for p in ("facebook", "instagram", "linkedin"))
        digital_presence = "Present" if (website or has_social) else "Weak/None found"

        rows.append({
            "business_name": record.get("name", ""),
            "industry": record.get("category") or business_type,
            "country": COUNTRY,
            "province_or_state": "Not Found",  # not reliably parseable from source addresses
            # Use the suburb this specific lead was actually found in (tagged at fetch
            # time for multi-location searches) rather than the combined search string.
            "city": record.get("_search_location") or location,
            "address": record.get("address", ""),
            "website": website if website else "Not Found",
            "website_status": analysis["website_status"],
            "phone": record.get("phone") or "Not Found",
            "phone_status": _phone_status(record),
            "email": analysis.get("email", "Not Found"),
            "email_status": analysis.get("email_status", "Not Found"),
            "whatsapp": analysis.get("whatsapp", False),
            "facebook": analysis.get("facebook", "Not Found"),
            "instagram": analysis.get("instagram", "Not Found"),
            "linkedin": analysis.get("linkedin", "Not Found"),
            "website_quality": analysis.get("website_quality", "Unable to determine"),
            "mobile_friendly": analysis.get("mobile_friendly", "Unable to determine"),
            "ssl": analysis.get("ssl", "Unable to determine"),
            "seo_opportunity": analysis.get("seo_opportunity", "Unable to determine"),
            "digital_presence": digital_presence,
            "recommended_service": scoring_result["recommended_service"],
            "lead_score": scoring_result["lead_score"],
            "lead_priority": scoring_result["lead_priority"],
            "opportunity_reason": scoring_result["opportunity_reason"],
            "data_confidence": scoring_result["data_confidence"],
            "source_information": record.get("source", ""),
            "research_date": research_date,
        })

    report_df = pd.DataFrame(rows, columns=REPORT_COLUMNS)
    report_df = report_df.sort_values("lead_score", ascending=False).reset_index(drop=True)
    return report_df
