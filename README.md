# KTZ Lead Engine

An automated lead-sourcing tool for KTZ Media. It finds businesses in a
given city, verifies they're real and operating, and classifies each one
by how good a fit it is for KTZ's services (websites, apps, digital
marketing) — then saves the results to CSV, and optionally pushes them to
a Google Sheet and/or emails a digest.

## How it decides what's a good lead

KTZ sells websites, apps, and digital marketing — so the ideal lead is a
business that **doesn't have a website yet** (they need what KTZ sells)
and is **reachable** (has a phone number on file). Having a website
actually makes a business a *weaker* lead for this purpose.

Every business found gets classified as one of:

| Lead quality | Meaning |
|---|---|
| `hot_lead` | No website, has a phone number — best, actionable target |
| `needs_manual_contact` | No website, no phone on file — still a good target, just needs another way to reach them |
| `not_a_priority` | Already has a website — weak fit for this campaign |

Results are sorted with hot leads first.

## Data sources

You can search either or both:

| Source | Coverage | Cost | Business types |
|---|---|---|---|
| **Google Places** (`--source google`) | Best data quality/completeness for South Africa | Needs a Google Cloud API key (free $200/month credit covers a lot) | Any — free-text search, no restrictions |
| **OpenStreetMap** (`--source osm`) | Sparser (crowdsourced), but free | Free, no key needed | Fixed list — see below |
| **Both** (`--source both`) | Combines and deduplicates results from both | — | Google's free-text + OSM's fixed list |

When the same business is found by both sources, the Google Places copy
is kept (it usually has richer data).

### OSM-supported business types

```
restaurants, guest houses, hotels, salons, car dealerships, lawyers,
accountants, construction companies, security companies,
medical practices, schools, tour operators
```

Google Places has no such restriction — type anything (e.g. "plumbers",
"gyms", "veterinarians").

## Setup

1. **Install dependencies:**
   ```
   pip install -r requirements.txt
   ```

2. **Copy the environment template and fill it in:**
   ```
   cp .env.example .env
   ```
   Then open `.env` and add your real values (see below).

### Google Places (required for `--source google` / `--source both`)

1. Go to [console.cloud.google.com](https://console.cloud.google.com) (this
   is different from Google AI Studio / Gemini keys — those won't work here)
2. Create or select a project
3. Enable **"Places API (New)"** under APIs & Services → Library
4. Link a billing account (needed even for the free tier — you get ~$200/month
   free credit)
5. Create an API key under APIs & Services → Credentials
6. Put it in `.env` as `GOOGLE_PLACES_API_KEY`

### Google Sheets output (optional)

1. In the same Google Cloud project, enable the **Google Sheets API**
2. Create a **Service Account** under Credentials, then generate and download
   a JSON key for it
3. Create a Google Sheet, and share it with the service account's email
   (found in the JSON file as `client_email`), giving it Editor access
4. Put the JSON file's path in `GOOGLE_SERVICE_ACCOUNT_JSON`, and the Sheet's
   ID (from its URL) in `GOOGLE_SHEET_ID`

Leave these blank to skip — the script will just save to CSV.

### Email digest output (optional)

Works with any SMTP provider. Set in `.env`:
- `SMTP_HOST`, `SMTP_PORT` — e.g. `smtp.ktzmedia.co.za`, `465`
- `SMTP_USER`, `SMTP_PASSWORD` — your mailbox credentials (wrap in quotes
  if the password contains a `#`, e.g. `SMTP_PASSWORD="my#pass"`)
- `EMAIL_TO` — where the digest should be sent

Port `465` uses SSL automatically; any other port uses STARTTLS (e.g. Gmail's
`587`, with an [app password](https://myaccount.google.com/apppasswords)
instead of your normal password).

Leave these blank to skip.

## Usage

```
python main.py "<business type>" "<location>" --source <google|osm|both> --max-results <number>
```

`--source` defaults to `google`. `--max-results` defaults to 20 (per source).

**Examples:**
```
python main.py "restaurants" "Johannesburg" --source google --max-results 20
python main.py "restaurants" "Cape Town" --source osm --max-results 10
python main.py "salons" "Pretoria" --source both --max-results 15
```

## Output

Every run:
1. Prints a summary (hot leads / needs manual contact / not a priority counts)
2. Saves a CSV to `output/`, named like
   `google_restaurants_Johannesburg_20260829_171345.csv`
3. Pushes to your Google Sheet, if configured
4. Sends an email digest with the CSV attached, if configured

## Project structure

```
main.py            entry point — parses args, orchestrates the run
places_client.py   Google Places API integration
osm_client.py       OpenStreetMap (Nominatim + Overpass) integration
verify.py           dedup, cross-source merge, lead classification
sheets_client.py    Google Sheets push
email_client.py     email digest sending
requirements.txt    Python dependencies
.env.example        template for required/optional settings
```

## Known limitations

- OSM data is crowdsourced — many businesses are missing phone numbers or
  websites even when they exist and are real
- OSM search is restricted to a fixed list of business-type tags (see above);
  Google Places has no such restriction
- Free public OSM servers (Nominatim, Overpass) can occasionally be slow or
  return temporary errors under load — the script retries across multiple
  Overpass mirrors automatically, but Nominatim geocoding has no fallback yet
