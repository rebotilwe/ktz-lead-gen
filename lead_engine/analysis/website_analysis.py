"""
Fetches and analyzes a lead's website (if it has one), extracting only
signals actually present in the page — never guessing or fabricating.

Per the research rules this implements:
- Never claim a website is poor without evidence -> ambiguous cases return
  "Unable to determine" rather than a guessed verdict.
- Never invent an email address -> only emails found via mailto: links in
  the page are reported; nothing is constructed from a pattern.
"""
import re

import requests
from bs4 import BeautifulSoup

REQUEST_TIMEOUT = 10
USER_AGENT = "KTZ-Lead-Engine/1.0 (website analysis)"

SOCIAL_PATTERNS = {
    "facebook": re.compile(r"facebook\.com/[^\s\"'<>]+", re.IGNORECASE),
    "instagram": re.compile(r"instagram\.com/[^\s\"'<>]+", re.IGNORECASE),
    "linkedin": re.compile(r"linkedin\.com/[^\s\"'<>]+", re.IGNORECASE),
}
WHATSAPP_PATTERN = re.compile(r"(wa\.me/|api\.whatsapp\.com)", re.IGNORECASE)
MAILTO_PATTERN = re.compile(r'mailto:([^"\'?\s]+)', re.IGNORECASE)


def _fetch_html(url: str) -> tuple[str | None, int | None]:
    """Fetch a page's HTML. Returns (html, status_code); (None, None) on failure."""
    headers = {"User-Agent": USER_AGENT}
    try:
        response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        return response.text, response.status_code
    except requests.exceptions.RequestException:
        return None, None


def analyze_website(url: str) -> dict:
    """
    Analyze a business website. Returns a dict of findings — every field is
    either a real observation from the page or "Unable to determine" /
    "Not Found", never a guess.
    """
    result = {
        "website_status": "Not Found",
        "ssl": "Unable to determine",
        "mobile_friendly": "Unable to determine",
        "website_quality": "Unable to determine",
        "seo_opportunity": "Unable to determine",
        "contact_options": "Unable to determine",
        "whatsapp": False,
        "facebook": "Not Found",
        "instagram": "Not Found",
        "linkedin": "Not Found",
        "email": "Not Found",
        "email_status": "Not Found",
        "outdated_signals": "Unable to determine",
    }

    if not url:
        return result

    result["ssl"] = url.strip().lower().startswith("https://")

    html, status_code = _fetch_html(url)
    if html is None or status_code is None:
        # Our probe failed to connect at all (timeout, blocked by anti-bot
        # protection, DNS issue, SSL handshake failure, etc). Google still
        # lists a website for this business — this says nothing about
        # whether the site itself works, so it must NOT be labeled the same
        # as "no website" (that would falsely suggest this is a hot lead
        # when the business may have a perfectly working site we just
        # couldn't reach from here).
        result["website_status"] = "Could Not Verify"
        # Keep website_quality at its default "Unable to determine" (not "Poor") —
        # a connection failure is not evidence the site is bad, just that we
        # couldn't check it. This keeps scoring.py/lead_report.py's exact-string
        # checks against "Unable to determine" working correctly.
        return result

    if status_code >= 400:
        # The site itself responded with an error (404/500/etc) — this is
        # real evidence the domain is broken or abandoned, distinct from a
        # probe failure above. "Poor" here is intentional and must stay an
        # exact match — scoring.py checks for it verbatim.
        result["website_status"] = f"Broken (HTTP {status_code})"
        result["website_quality"] = "Poor"
        return result

    result["website_status"] = "Found"

    soup = BeautifulSoup(html, "html.parser")

    # Mobile-friendliness: presence of a viewport meta tag is a real, checkable signal
    viewport_tag = soup.find("meta", attrs={"name": "viewport"})
    result["mobile_friendly"] = bool(viewport_tag)

    # Basic SEO signals: title and meta description present?
    title_tag = soup.find("title")
    meta_description = soup.find("meta", attrs={"name": "description"})
    has_title = bool(title_tag and title_tag.get_text(strip=True))
    has_meta_description = bool(meta_description and meta_description.get("content", "").strip())

    if has_title and has_meta_description:
        result["seo_opportunity"] = "Low — title and meta description present"
    elif has_title or has_meta_description:
        result["seo_opportunity"] = "Medium — missing title or meta description"
    else:
        result["seo_opportunity"] = "High — missing both title and meta description"

    # WhatsApp: only true if an actual wa.me / api.whatsapp.com link is present
    result["whatsapp"] = bool(WHATSAPP_PATTERN.search(html))

    # Social links: only reported if an actual matching URL is found in the page
    for platform, pattern in SOCIAL_PATTERNS.items():
        match = pattern.search(html)
        result[platform] = f"https://{match.group(0)}" if match else "Not Found"

    # Email: only from a real mailto: link, never constructed
    mailto_match = MAILTO_PATTERN.search(html)
    if mailto_match:
        result["email"] = mailto_match.group(1)
        result["email_status"] = "Found"

    # Contact options: what's actually present (tel:, mailto:, contact form, WhatsApp)
    contact_signals = []
    if re.search(r'href=["\']tel:', html, re.IGNORECASE):
        contact_signals.append("phone link")
    if mailto_match:
        contact_signals.append("email link")
    if re.search(r'(contact.?us|contact.?form)', html, re.IGNORECASE):
        contact_signals.append("contact page/form")
    if result["whatsapp"]:
        contact_signals.append("WhatsApp")
    result["contact_options"] = ", ".join(contact_signals) if contact_signals else "None found"

    # Outdated-signs: only flagged on a real, checkable signal (old copyright year)
    # in the page footer text — never inferred from "the site just looks old."
    copyright_years = re.findall(r"(?:©|copyright)\D{0,10}(20\d{2}|19\d{2})", html, re.IGNORECASE)
    if copyright_years:
        latest_year = max(int(y) for y in copyright_years)
        result["outdated_signals"] = latest_year <= 2022  # more than a few years stale
    else:
        result["outdated_signals"] = "Unable to determine"

    # Overall quality: only a real, evidence-based rollup of the above — never
    # a guess when we don't have enough signals to say either way.
    have_enough_signal = has_title or has_meta_description or viewport_tag is not None
    if not have_enough_signal:
        result["website_quality"] = "Unable to determine"
    else:
        issues = sum([
            not result["mobile_friendly"],
            result["seo_opportunity"] != "Low — title and meta description present",
            result["outdated_signals"] is True,
        ])
        if issues == 0:
            result["website_quality"] = "Good"
        elif issues == 1:
            result["website_quality"] = "Fair"
        else:
            result["website_quality"] = "Poor"

    return result
