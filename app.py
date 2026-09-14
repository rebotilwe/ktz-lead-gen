"""
KTZ Lead Engine — web UI

A point-and-click front end for the pipeline: no terminal, no CLI flags,
no manually edited .env file. Search, review, download, and optionally
push to Sheets / email — all from a browser.

Run locally with:
    streamlit run app.py
(or double-click run.bat on Windows / run.command on Mac)
"""
import json
import os
import tempfile
from datetime import datetime

import streamlit as st
from dotenv import load_dotenv, set_key

from lead_engine.sources.google_places import search_places
from lead_engine.sources.osm import search_osm, BUSINESS_TYPE_TAGS
from lead_engine.verification.verify import build_lead_dataframe
from lead_engine.verification.franchise_filter import is_franchise
from lead_engine.analysis.lead_report import build_report
from lead_engine.outputs.sheets_client import push_to_sheet
from lead_engine.outputs.email_client import send_email_digest

ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")
if not os.path.exists(ENV_PATH):
    open(ENV_PATH, "a").close()
load_dotenv(ENV_PATH)


def get_setting(key: str, default: str = "") -> str:
    """
    Read a setting from Streamlit Cloud's Secrets manager if deployed there,
    otherwise fall back to the local .env file. This lets the same app.py
    work both locally (via .env) and once deployed (via Cloud Secrets),
    without needing separate code paths.
    """
    try:
        if key in st.secrets:
            return st.secrets[key]
    except Exception:
        pass  # no secrets.toml locally — that's expected, just fall through
    return os.getenv(key, default)


IS_CLOUD_DEPLOYMENT = False
try:
    IS_CLOUD_DEPLOYMENT = len(st.secrets) > 0
except Exception:
    pass

# When someone asks for N leads, filtering (dedup, franchises, no-website-only,
# closed businesses) will always remove some — so we fetch a bigger raw pool
# up front and only trim down to N at the very end, after every filter has run.
RAW_FETCH_MULTIPLIER = 4
RAW_FETCH_CAP = 150

st.set_page_config(page_title="KTZ Lead Engine", page_icon="🔍", layout="wide")


def _save_setting(key: str, value: str) -> None:
    """Persist one setting to .env so it's remembered next time the app opens."""
    set_key(ENV_PATH, key, value or "")


# ----------------------------------------------------------------------
# Sidebar — one-time setup, remembered across sessions via .env
# ----------------------------------------------------------------------
with st.sidebar:
    st.header("⚙️ Settings")
    st.caption("Fill these in once — they're saved on this computer for next time.")

    with st.expander("Google Places (required for the Google source)", expanded=False):
        google_key = st.text_input(
            "Google Places API Key",
            value=get_setting("GOOGLE_PLACES_API_KEY", ""),
            type="password",
            key="google_key",
        )

    with st.expander("Google Sheets (optional)", expanded=False):
        st.caption("Leave blank to skip pushing results to a Sheet.")
        sheet_id = st.text_input("Google Sheet ID", value=get_setting("GOOGLE_SHEET_ID", ""), key="sheet_id")
        service_account_path = st.text_input(
            "Service account JSON file path",
            value=get_setting("GOOGLE_SERVICE_ACCOUNT_JSON", ""),
            key="service_account_path",
        )

    with st.expander("Email digest (optional)", expanded=False):
        st.caption("Leave blank to skip emailing results.")
        smtp_host = st.text_input("SMTP host", value=get_setting("SMTP_HOST", "smtp.gmail.com"), key="smtp_host")
        smtp_port = st.text_input("SMTP port", value=get_setting("SMTP_PORT", "587"), key="smtp_port")
        smtp_user = st.text_input("SMTP user (your email)", value=get_setting("SMTP_USER", ""), key="smtp_user")
        smtp_password = st.text_input(
            "SMTP app password", value=get_setting("SMTP_PASSWORD", ""), type="password", key="smtp_password"
        )
        email_to = st.text_input("Send digest to", value=get_setting("EMAIL_TO", ""), key="email_to")

    if IS_CLOUD_DEPLOYMENT:
        st.caption(
            "Running on Streamlit Cloud — settings here are for this session only. "
            "To make them permanent, add them under your app's **Settings → Secrets** "
            "in the Streamlit Cloud dashboard instead."
        )
    elif st.button("💾 Save settings", use_container_width=True):
        _save_setting("GOOGLE_PLACES_API_KEY", google_key)
        _save_setting("GOOGLE_SHEET_ID", sheet_id)
        _save_setting("GOOGLE_SERVICE_ACCOUNT_JSON", service_account_path)
        _save_setting("SMTP_HOST", smtp_host)
        _save_setting("SMTP_PORT", smtp_port)
        _save_setting("SMTP_USER", smtp_user)
        _save_setting("SMTP_PASSWORD", smtp_password)
        _save_setting("EMAIL_TO", email_to)
        st.success("Saved. These will be pre-filled next time you open the app.")


# ----------------------------------------------------------------------
# Main search form
# ----------------------------------------------------------------------
st.title("🔍 KTZ Lead Engine")
st.caption("Find businesses that need a website, app, or digital marketing help — scored and ranked automatically.")

col1, col2 = st.columns([2, 1])
with col1:
    business_type = st.text_input("Business type", placeholder="e.g. restaurants, guest houses, lawyers")
with col2:
    source = st.selectbox(
        "Source", ["google", "osm", "both"],
        help="Google: any business type, needs an API key. OSM: free, limited business types. "
             "Both: combines and deduplicates results from each.",
    )

locations_text = st.text_area(
    "Location(s) — one per line",
    placeholder="Tsakane\nKwa Thema\nSprings\nBenoni\nBoksburg",
    help="Google's Text Search API hard-caps at 60 raw results per single location. To reliably "
         "reach a large number of leads (e.g. 100), search several nearby suburbs at once — one "
         "location alone usually can't get there after filtering out businesses that already have "
         "websites, franchises, and closed listings.",
)
locations = [loc.strip() for loc in locations_text.splitlines() if loc.strip()]

desired_count = st.slider("How many leads do you want?", min_value=5, max_value=200, value=20, step=5)
raw_fetch_count_per_location = min(desired_count * RAW_FETCH_MULTIPLIER, RAW_FETCH_CAP)
if locations:
    st.caption(f"A raw pool of up to {raw_fetch_count_per_location} per source is searched in EACH of "
               f"your {len(locations)} location(s) (up to {raw_fetch_count_per_location * len(locations)} "
               f"total before filtering/dedup) to reliably reach {desired_count} qualifying leads.")

if source in ("osm", "both"):
    with st.expander("ℹ️ Business types supported by the free (OSM) source"):
        st.write(", ".join(sorted(BUSINESS_TYPE_TAGS.keys())))

filter_col1, filter_col2 = st.columns(2)
with filter_col1:
    no_website_only = st.checkbox("Only show leads with no website")
with filter_col2:
    exclude_franchises = st.checkbox("Exclude known chains/franchises")

setting_col1, setting_col2 = st.columns(2)
with setting_col1:
    push_sheet = st.checkbox("Also push results to Google Sheet")
with setting_col2:
    send_email = st.checkbox("Also email me a digest")

search_clicked = st.button("🔎 Search for leads", type="primary", use_container_width=True)

if search_clicked:
    if not business_type.strip() or not locations:
        st.error("Please enter both a business type and at least one location.")
        st.stop()

    if source in ("google", "both") and (not google_key or google_key == "your_api_key_here"):
        if source == "google":
            st.error("Add a Google Places API key under Settings in the sidebar first.")
            st.stop()
        st.warning("No Google Places API key set — searching OSM only.")

    records = []
    with st.spinner(f"Searching for '{business_type}' across {len(locations)} location(s) via {source}..."):
        for loc in locations:
            location_records = []

            if source in ("google", "both") and google_key and google_key != "your_api_key_here":
                try:
                    google_records = search_places(business_type, loc, google_key, raw_fetch_count_per_location)
                    location_records.extend(google_records)
                except Exception as e:
                    # One suburb failing (rate limit, transient error) must not abort the
                    # rest of the batch — warn and move on to the next location.
                    st.warning(f"Google Places search failed for '{loc}', continuing: {e}")

            if source in ("osm", "both"):
                try:
                    osm_records = search_osm(business_type, loc, raw_fetch_count_per_location)
                    location_records.extend(osm_records)
                except (ValueError, RuntimeError) as e:
                    st.warning(f"OpenStreetMap search failed for '{loc}': {e}")

            # Tag each record with the suburb it was actually found in, so the
            # final report shows the real per-lead location instead of a single
            # location string across every row from a multi-suburb search.
            for rec in location_records:
                rec["_search_location"] = loc

            st.info(f"'{loc}': fetched {len(location_records)} raw result(s).")
            records.extend(location_records)

        if not records:
            st.warning("No results fetched from any source/location.")
            st.stop()

        df = build_lead_dataframe(records)

    if df.empty:
        st.warning("No operational businesses found for this search. Try a broader location or business type.")
        st.stop()

    st.success(f"{len(df)} unique leads found after merging/deduplication.")

    if exclude_franchises:
        franchise_checks = df.apply(lambda row: is_franchise(row.to_dict()), axis=1)
        is_franchise_mask = franchise_checks.apply(lambda x: x[0])
        excluded_count = int(is_franchise_mask.sum())
        if excluded_count:
            excluded_names = df.loc[is_franchise_mask, "name"].tolist()
            st.info(f"Excluded {excluded_count} known chain/franchise business(es): {', '.join(excluded_names)}")
        df = df[~is_franchise_mask].reset_index(drop=True)
        if df.empty:
            st.warning("No leads left after excluding franchises.")
            st.stop()

    with st.spinner("Analyzing websites and scoring leads — this fetches each lead's website, may take a moment..."):
        report_df = build_report(df, business_type, ", ".join(locations))

    if no_website_only:
        before_count = len(report_df)
        report_df = report_df[report_df["website_status"] == "Not Found"].reset_index(drop=True)
        st.info(f"Filtered to leads with no website: {len(report_df)} of {before_count} kept.")
        if report_df.empty:
            st.warning("No leads left after filtering.")
            st.stop()

    # report_df is already sorted by lead_score (highest first) from build_report —
    # trim to what was actually asked for, now that every filter has run.
    available_count = len(report_df)
    report_df = report_df.head(desired_count).reset_index(drop=True)
    if available_count < desired_count:
        st.warning(f"Only {available_count} qualifying leads were available (asked for {desired_count}) — "
                   f"try a larger city, broader business type, or raise the slider.")
    else:
        st.caption(f"Showing top {desired_count} of {available_count} qualifying leads.")

    priority_counts = report_df["lead_priority"].value_counts()
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("🔥 HOT", int(priority_counts.get("HOT", 0)))
    m2.metric("⭐ HIGH", int(priority_counts.get("HIGH", 0)))
    m3.metric("➖ MEDIUM", int(priority_counts.get("MEDIUM", 0)))
    m4.metric("⬇️ LOW", int(priority_counts.get("LOW", 0)))

    st.dataframe(report_df, use_container_width=True, hide_index=True)

    safe_type = business_type.strip().replace(" ", "_")
    safe_location = "_".join(locations).replace(" ", "_")[:60]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = f"{source}_{safe_type}_{safe_location}_{timestamp}"
    csv_bytes = report_df.to_csv(index=False).encode("utf-8")
    json_bytes = json.dumps(report_df.to_dict(orient="records"), indent=2, default=str).encode("utf-8")

    dl_col1, dl_col2 = st.columns(2)
    with dl_col1:
        st.download_button("⬇️ Download CSV", csv_bytes, file_name=f"{base_name}.csv",
                            mime="text/csv", use_container_width=True)
    with dl_col2:
        st.download_button("⬇️ Download JSON", json_bytes, file_name=f"{base_name}.json",
                            mime="application/json", use_container_width=True)

    if push_sheet:
        if sheet_id and service_account_path:
            try:
                push_to_sheet(report_df, service_account_path, sheet_id)
                st.success("Pushed results to Google Sheet.")
            except Exception as e:
                st.error(f"Google Sheet push failed: {e}")
        else:
            st.warning("Add a Sheet ID and service account file path under Settings to push to Sheets.")

    if send_email:
        if all([smtp_host, smtp_port, smtp_user, smtp_password, email_to]):
            try:
                with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as tmp:
                    report_df.to_csv(tmp.name, index=False)
                    tmp_path = tmp.name
                send_email_digest(
                    report_df, tmp_path, smtp_host, int(smtp_port), smtp_user, smtp_password,
                    email_to, business_type, ", ".join(locations), source,
                )
                st.success(f"Sent email digest to {email_to}.")
            except Exception as e:
                st.error(f"Email digest failed: {e}")
        else:
            st.warning("Fill in all SMTP settings under Settings to send an email digest.")