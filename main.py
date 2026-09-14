"""
Entry point for the KTZ Lead Engine — sourcing, verification, website
analysis, and scoring, end to end.

Usage:
    python main.py "restaurants" "Johannesburg" --source google
    python main.py "restaurants" "Johannesburg" --source osm
    python main.py "restaurants" "Johannesburg" --source both
"""
import argparse
import json
import os
import sys
from datetime import datetime

from dotenv import load_dotenv

from lead_engine.sources.google_places import search_places
from lead_engine.sources.osm import search_osm
from lead_engine.verification.verify import build_lead_dataframe
from lead_engine.analysis.lead_report import build_report
from lead_engine.verification.franchise_filter import is_franchise
from lead_engine.outputs.sheets_client import push_to_sheet
from lead_engine.outputs.email_client import send_email_digest

load_dotenv()

# When someone asks for N leads, filtering (dedup, franchises, no-website-only,
# closed businesses) will always remove some — so we fetch a bigger raw pool
# up front and only trim down to N at the very end, after every filter has
# run. Without this, "give me 20 leads" often means "fetch 20, then watch
# most of them get filtered out."
RAW_FETCH_MULTIPLIER = 4
RAW_FETCH_CAP = 150  # OSM has no hard per-request limit like Google's 60, but this keeps queries reasonable


def maybe_push_to_sheet(df):
    """Push to Google Sheets if credentials are configured in .env; otherwise skip quietly."""
    service_account_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    sheet_id = os.getenv("GOOGLE_SHEET_ID")

    if not service_account_json or not sheet_id:
        print("Skipping Google Sheet push (GOOGLE_SERVICE_ACCOUNT_JSON / GOOGLE_SHEET_ID not set in .env).")
        return

    try:
        push_to_sheet(df, service_account_json, sheet_id)
        print("Pushed results to Google Sheet.")
    except Exception as e:
        print(f"WARNING: Google Sheet push failed: {e}")


def maybe_send_email(df, csv_path, business_type, location, source):
    """Send an email digest if SMTP credentials are configured in .env; otherwise skip quietly."""
    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = os.getenv("SMTP_PORT")
    smtp_user = os.getenv("SMTP_USER")
    smtp_password = os.getenv("SMTP_PASSWORD")
    email_to = os.getenv("EMAIL_TO")

    if not all([smtp_host, smtp_port, smtp_user, smtp_password, email_to]):
        print("Skipping email digest (SMTP settings not fully set in .env).")
        return

    try:
        send_email_digest(
            df, csv_path, smtp_host, int(smtp_port), smtp_user, smtp_password,
            email_to, business_type, location, source,
        )
        print(f"Sent email digest to {email_to}.")
    except Exception as e:
        print(f"WARNING: Email digest failed: {e}")


def fetch_records(source: str, business_type: str, location: str, max_results: int) -> list[dict]:
    """
    Fetch records from the requested source(s) for ONE location. For 'both',
    queries Google and OSM independently — if one fails or isn't configured,
    the other's results are still returned. A failure here never exits the
    process: when this is called in a loop over several locations (see
    main()), one bad/rate-limited location must not abort the rest of the
    batch — it just contributes zero records and the run continues.
    """
    records = []

    if source in ("google", "both"):
        api_key = os.getenv("GOOGLE_PLACES_API_KEY")
        if not api_key or api_key == "your_api_key_here":
            print("WARNING: GOOGLE_PLACES_API_KEY not set — skipping Google for this location.")
        else:
            try:
                google_records = search_places(business_type, location, api_key, max_results)
                records.extend(google_records)
            except Exception as e:
                print(f"WARNING: Google fetch failed for '{location}': {e}")

    if source in ("osm", "both"):
        try:
            osm_records = search_osm(business_type, location, max_results)
            records.extend(osm_records)
        except (ValueError, RuntimeError) as e:
            print(f"WARNING: OSM fetch failed for '{location}': {e}")

    return records


def main():
    parser = argparse.ArgumentParser(description="KTZ Lead Engine — fetch and verify business leads")
    parser.add_argument("business_type", help="e.g. 'restaurants', 'guest houses', 'construction companies'")
    parser.add_argument(
        "location",
        help=(
            "One or more locations, comma-separated, e.g. 'Tsakane' or "
            "'Tsakane, Kwa Thema, Springs, Benoni, Boksburg'. Google's Text "
            "Search API hard-caps at 60 raw results per single location, so "
            "reaching a large final count (e.g. 100) reliably requires "
            "searching several nearby suburbs and merging the results — "
            "one location alone usually can't get there after filtering."
        ),
    )
    parser.add_argument("--source", choices=["google", "osm", "both"], default="google",
                         help="Which data source(s) to use (default: google)")
    parser.add_argument("--max-results", type=int, default=20,
                         help="How many leads you want in the final results (a larger raw pool is "
                              "fetched automatically to account for filtering)")
    parser.add_argument("--no-website-only", action="store_true",
                         help="Only keep leads that don't already have a website (excludes weaker leads)")
    parser.add_argument("--exclude-franchises", action="store_true",
                         help="Exclude known chains/franchises (via OSM brand tags and a known-names list)")
    args = parser.parse_args()

    locations = [loc.strip() for loc in args.location.split(",") if loc.strip()]

    if args.source in ("google", "both"):
        api_key = os.getenv("GOOGLE_PLACES_API_KEY")
        if (not api_key or api_key == "your_api_key_here") and args.source == "google":
            print("ERROR: GOOGLE_PLACES_API_KEY not set. Copy .env.example to .env and add your key.")
            sys.exit(1)

    desired_count = args.max_results
    raw_fetch_count_per_location = min(desired_count * RAW_FETCH_MULTIPLIER, RAW_FETCH_CAP)

    print(f"Searching for '{args.business_type}' across {len(locations)} location(s) via {args.source}: "
          f"{', '.join(locations)}")
    print(f"Fetching a raw pool of up to {raw_fetch_count_per_location} per source per location "
          f"to reliably reach {desired_count} final leads.")

    records = []
    for location in locations:
        location_records = fetch_records(args.source, args.business_type, location, raw_fetch_count_per_location)
        print(f"  '{location}': {len(location_records)} raw result(s)")
        for rec in location_records:
            rec["_search_location"] = location
        records.extend(location_records)

    if not records:
        print("No results fetched from any source.")
        return

    df = build_lead_dataframe(records)
    if df.empty:
        print("No operational businesses found for this search.")
        return

    hot_count = (df["lead_quality"] == "hot_lead").sum()
    manual_count = (df["lead_quality"] == "needs_manual_contact").sum()
    low_priority_count = (df["lead_quality"] == "not_a_priority").sum()
    print(
        f"{len(df)} unique leads after merging/dedup — "
        f"{hot_count} hot leads, {manual_count} need manual contact, "
        f"{low_priority_count} not a priority (already have a website)."
    )

    if args.exclude_franchises:
        franchise_checks = df.apply(lambda row: is_franchise(row.to_dict()), axis=1)
        is_franchise_mask = franchise_checks.apply(lambda x: x[0])
        excluded_count = int(is_franchise_mask.sum())
        if excluded_count:
            excluded_names = df.loc[is_franchise_mask, "name"].tolist()
            print(f"Excluding {excluded_count} known chain/franchise businesses: {', '.join(excluded_names)}")
        df = df[~is_franchise_mask].reset_index(drop=True)
        if df.empty:
            print("No leads left after excluding franchises.")
            return

    print("Analyzing websites and scoring leads (this fetches each lead's website, may take a moment)...")
    report_df = build_report(df, args.business_type, ", ".join(locations))

    priority_counts = report_df["lead_priority"].value_counts()
    print(
        f"Scored {len(report_df)} leads — "
        f"{priority_counts.get('HOT', 0)} HOT, {priority_counts.get('HIGH', 0)} HIGH, "
        f"{priority_counts.get('MEDIUM', 0)} MEDIUM, {priority_counts.get('LOW', 0)} LOW."
    )

    if args.no_website_only:
        before_count = len(report_df)
        report_df = report_df[report_df["website_status"] == "Not Found"].reset_index(drop=True)
        print(f"Filtered to leads with no website: {len(report_df)} of {before_count} kept.")

        if report_df.empty:
            print("No leads left after filtering.")
            return

    # report_df is already sorted by lead_score (highest first) from build_report —
    # trim to what was actually asked for, now that every filter has run.
    available_count = len(report_df)
    report_df = report_df.head(desired_count).reset_index(drop=True)
    if available_count < desired_count:
        print(f"Only {available_count} qualifying leads were available (asked for {desired_count}) — "
              f"try a larger city, broader business type, or increase --max-results.")
    else:
        print(f"Returning top {desired_count} of {available_count} qualifying leads.")

    os.makedirs("output", exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_type = args.business_type.replace(" ", "_")
    safe_location = "_".join(locations).replace(" ", "_")[:60]
    base_filename = f"output/{args.source}_{safe_type}_{safe_location}_{timestamp}"

    csv_filename = f"{base_filename}.csv"
    json_filename = f"{base_filename}.json"

    report_df.to_csv(csv_filename, index=False)
    with open(json_filename, "w", encoding="utf-8") as f:
        json.dump(report_df.to_dict(orient="records"), f, indent=2, default=str)

    print(f"Saved to {csv_filename} and {json_filename}")

    maybe_push_to_sheet(report_df)
    maybe_send_email(report_df, csv_filename, args.business_type, ", ".join(locations), args.source)


if __name__ == "__main__":
    main()
