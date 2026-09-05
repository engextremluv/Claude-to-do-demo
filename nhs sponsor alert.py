#!/usr/bin/env python3
"""
NHS Jobs sponsorship alert.

Checks jobs.nhs.uk for a saved search, keeps only NEW adverts that:
  - match one of your target job titles,
  - are the right Band / contract type / hours (best-effort), and
  - confirm Skilled Worker visa sponsorship,
then emails you a digest. Runs twice a day (see the GitHub Actions workflow
or your own cron / Task Scheduler).

Because jobs.nhs.uk blocks automated inspection, the HTML selectors here are
best-effort. Run `python nhs_sponsor_alert.py --debug` once: it dumps the raw
search + advert HTML and prints what it parsed, so the field extraction can be
tuned to the exact page markup if anything comes back blank.
"""

import argparse
import json
import os
import re
import smtplib
import sys
import time
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# --------------------------------------------------------------------------- #
#  SETTINGS — edit these                                                       #
# --------------------------------------------------------------------------- #

# The saved search you gave me (keyword search). Filters below are applied in
# code against each advert, so you don't have to encode them in the URL.
SEARCH_URL = (
    "https://www.jobs.nhs.uk/candidate/search/results"
    "?keyword=Healthcare%20or%20nurse%2C%20anything%20medical&language=en"
)

# Where the digest is sent.
RECIPIENT = "Adeyemiopeyemi1801@gmail.com"

# Title filter — any healthcare / medical line. A job is kept if its title
# contains one of these (case-insensitive). This is broad on purpose so it
# catches the whole clinical spectrum but still drops corporate roles
# (finance, IT, HR, procurement, estates). Empty the list -> keep EVERY title
# at Band 3, no matter the field.
TITLE_KEYWORDS = [
    "nurse", "nursing", "healthcare", "health care", "hca",
    "care assistant", "care worker", "care support", "support worker",
    "clinical", "medical", "patient", "ward", "theatre",
    "midwif", "maternity", "therapy", "therapist", "physio",
    "occupational therap", "phlebotom", "radiograph", "sonograph",
    "pharmac", "dental", "dentist", "dietit", "nutrition",
    "paramedic", "ambulance", "odp", "operating department",
    "mental health", "psychiatr", "rehab", "podiat", "optom",
    "orthopt", "audiolog", "speech and language", "biomedical",
    "laboratory", "pathology", "cardiac", "cardiology", "radiology",
    "oncology", "health visitor", "district nurs", "community health",
    "occupational health", "assistant practitioner", "nursing associate",
    "healthcare support", "clinical support",
]

# Best-effort structured filters. Set any to None / [] to disable it.
REQUIRE_BAND = "3"              # keeps Band 3 only; None to allow any band
REQUIRE_CONTRACT = "permanent"  # substring match on contract type
HOURS_KEYWORDS = ["full time"]  # matches "Full time" (36 or 37.5 hrs); see note
REQUIRE_SPONSORSHIP = True      # keep only adverts that confirm sponsorship

# If a field (band / contract / hours) can't be parsed from the page, DON'T
# drop the job for it — show it with the field marked "unknown". Better a
# maybe than a miss. Set True to drop jobs whose fields can't be confirmed.
STRICT_FIELD_FILTERS = False

# Include adverts where sponsorship is unclear (only the generic NHS footer,
# no trust-specific statement) in a separate "verify manually" section.
INCLUDE_UNKNOWN_SPONSORSHIP = False

MAX_PAGES = 5                   # search-result pages to scan (about 20 jobs each)
REQUEST_DELAY = 1.5             # seconds between requests, be polite
SEND_WHEN_EMPTY = False         # email even when there are no new matches
STATE_FILE = Path("seen_jobs.json")

USER_AGENT = os.environ.get(
    "NHS_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
)

# --------------------------------------------------------------------------- #
#  Sponsorship classification                                                  #
#                                                                              #
#  NHS adverts often carry a GENERIC footer ("Applications from job seekers    #
#  who require current Skilled worker sponsorship ... are welcome") even when  #
#  the trust states elsewhere it will NOT sponsor. So that footer is NOT a     #
#  reliable positive. We trust only trust-specific statements, and any         #
#  negative statement overrides everything.                                    #
# --------------------------------------------------------------------------- #

NEGATIVE_PATTERNS = [
    "unable to provide skilled worker",
    "unable to offer skilled worker",
    "unable to provide sponsorship",
    "unable to offer sponsorship",
    "not able to offer sponsorship",
    "cannot offer sponsorship",
    "can not offer sponsorship",
    "do not offer sponsorship",
    "does not offer sponsorship",
    "not offering sponsorship",
    "unable to sponsor",
    "not able to sponsor",
    "cannot sponsor",
    "will not require sponsorship",
    "not require sponsorship if successful",
    "does not meet the criteria for sponsorship",
    "unlikely that the criteria required to support skilled worker",
    "unlikely to meet the criteria required to support skilled worker",
    "not eligible for skilled worker visa sponsorship",
    "not eligible for sponsorship",
    "sponsorship is not available",
    "sponsorship will not be available",
    "we are not a licensed sponsor",
    "this role does not qualify for sponsorship",
    "this post does not attract",  # "...does not attract a certificate of sponsorship"
]

# Trust-WRITTEN positives (not the generic footer).
POSITIVE_PATTERNS = [
    "we welcome applications from candidates who require skilled worker visa sponsorship",
    "welcome applications from candidates who require skilled worker visa sponsorship",
    "candidates who require skilled worker visa sponsorship to work in the uk, and these will be considered",
    "we are able to offer sponsorship",
    "we can offer skilled worker",
    "we can offer sponsorship",
    "happy to offer sponsorship",
    "able to offer skilled worker visa sponsorship",
    "this role is eligible for skilled worker visa sponsorship",
    "this post is eligible for sponsorship",
    "sponsorship is available for this",
    "sponsorship may be available for this",
    "we are a licensed sponsor and can",
]


def classify_sponsorship(text: str):
    """Return ('yes'|'no'|'unknown', matched_phrase)."""
    low = re.sub(r"\s+", " ", text.lower())
    for p in NEGATIVE_PATTERNS:
        if p in low:
            return "no", p
    for p in POSITIVE_PATTERNS:
        if p in low:
            return "yes", p
    return "unknown", ""


# --------------------------------------------------------------------------- #
#  Fetching / parsing                                                          #
# --------------------------------------------------------------------------- #

BASE = "https://www.jobs.nhs.uk"
ADVERT_RE = re.compile(r"/candidate/jobadvert/([A-Za-z0-9][A-Za-z0-9\-]+)")


def make_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-GB,en;q=0.9",
    })
    return s


def get(session, url, debug_save=None):
    resp = session.get(url, timeout=30)
    resp.raise_for_status()
    if debug_save:
        Path(debug_save).write_text(resp.text, encoding="utf-8")
    return resp.text


def find_advert_refs(html):
    """Extract unique advert references from a search-results page, in order."""
    refs = []
    seen = set()
    for m in ADVERT_RE.finditer(html):
        ref = m.group(1)
        if ref not in seen:
            seen.add(ref)
            refs.append(ref)
    return refs


def label_value(text, labels):
    """Find 'Label: value' or 'Label value' for any of the given labels."""
    for label in labels:
        m = re.search(
            rf"{re.escape(label)}\s*[:\-]?\s*([^\n\r|]+)",
            text, flags=re.IGNORECASE,
        )
        if m:
            val = m.group(1).strip()
            val = re.split(r"\s{2,}", val)[0].strip(" .")
            if val:
                return val
    return None


def parse_advert(html):
    soup = BeautifulSoup(html, "html.parser")
    title_tag = soup.find(["h1", "h2"])
    title = title_tag.get_text(" ", strip=True) if title_tag else ""
    full_text = soup.get_text("\n", strip=True)

    band = label_value(full_text, ["Grade", "Band", "AfC banding", "Pay scheme and Band"])
    if band:
        bm = re.search(r"(\d)", band)
        band = bm.group(1) if bm else band

    contract = label_value(full_text, ["Contract"])
    hours = label_value(full_text, ["Working pattern", "Hours", "Hours per week"])
    salary = label_value(full_text, ["Salary", "Pay"])
    if not salary:
        sm = re.search(r"£[\d,]+(?:\.\d+)?(?:\s*(?:to|-)\s*£[\d,]+(?:\.\d+)?)?", full_text)
        salary = sm.group(0) if sm else None
    employer = label_value(full_text, ["Employer", "Organisation"])
    location = label_value(full_text, ["Town", "Location", "Site"])
    closing = label_value(full_text, ["Closing date", "Closing"])

    verdict, phrase = classify_sponsorship(full_text)
    return {
        "title": title, "band": band, "contract": contract, "hours": hours,
        "salary": salary, "employer": employer, "location": location,
        "closing": closing, "sponsorship": verdict, "sponsor_phrase": phrase,
    }


# --------------------------------------------------------------------------- #
#  Filtering                                                                    #
# --------------------------------------------------------------------------- #

def title_ok(title):
    if not TITLE_KEYWORDS:
        return True
    low = title.lower()
    return any(k in low for k in TITLE_KEYWORDS)


def field_ok(value, required, contains_list=None):
    """True if value matches. Unknown value -> depends on STRICT_FIELD_FILTERS."""
    if value is None:
        return not STRICT_FIELD_FILTERS
    low = str(value).lower()
    if contains_list is not None:
        return any(str(c).lower() in low for c in contains_list)
    return str(required).lower() in low


def passes_filters(job):
    reasons = []
    if not title_ok(job["title"]):
        reasons.append("title")
    if REQUIRE_BAND is not None and not field_ok(job["band"], REQUIRE_BAND):
        reasons.append(f"band!={REQUIRE_BAND}")
    if REQUIRE_CONTRACT is not None and not field_ok(job["contract"], REQUIRE_CONTRACT):
        reasons.append("contract")
    if HOURS_KEYWORDS and not field_ok(job["hours"], None, contains_list=HOURS_KEYWORDS):
        reasons.append("hours")
    if REQUIRE_SPONSORSHIP:
        if job["sponsorship"] == "no":
            reasons.append("no-sponsorship")
        elif job["sponsorship"] == "unknown" and not INCLUDE_UNKNOWN_SPONSORSHIP:
            reasons.append("sponsorship-unknown")
    return (len(reasons) == 0), reasons


# --------------------------------------------------------------------------- #
#  Email                                                                        #
# --------------------------------------------------------------------------- #

def build_email_html(confirmed, unclear):
    def card(job):
        badge = ("✅ Sponsorship confirmed" if job["sponsorship"] == "yes"
                 else "❔ Sponsorship unclear — verify")
        rows = "".join(
            f"<tr><td style='padding:2px 10px 2px 0;color:#555'>{k}</td>"
            f"<td style='padding:2px 0'><b>{v or '—'}</b></td></tr>"
            for k, v in [
                ("Employer", job["employer"]), ("Location", job["location"]),
                ("Band", job["band"]), ("Contract", job["contract"]),
                ("Hours", job["hours"]), ("Salary", job["salary"]),
                ("Closing", job["closing"]),
            ]
        )
        return (
            f"<div style='border:1px solid #e2e2e2;border-radius:10px;"
            f"padding:14px 16px;margin:12px 0'>"
            f"<div style='font-size:16px;font-weight:700;margin-bottom:6px'>"
            f"<a href='{job['url']}' style='color:#005eb8;text-decoration:none'>"
            f"{job['title'] or 'NHS role'}</a></div>"
            f"<div style='display:inline-block;font-size:12px;background:#e8f4ff;"
            f"color:#005eb8;padding:2px 8px;border-radius:20px;margin-bottom:8px'>"
            f"{badge}</div>"
            f"<table style='font-size:13px;border-collapse:collapse'>{rows}</table>"
            f"<div style='margin-top:8px'><a href='{job['url']}' "
            f"style='font-size:13px;color:#005eb8'>View &amp; apply →</a></div></div>"
        )

    when = datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC")
    parts = [
        "<div style='font-family:Arial,Helvetica,sans-serif;max-width:640px;"
        "margin:0 auto;color:#222'>",
        "<h2 style='color:#005eb8;margin-bottom:2px'>NHS sponsorship job alert</h2>",
        f"<div style='color:#777;font-size:12px;margin-bottom:14px'>{when}</div>",
    ]
    if confirmed:
        parts.append(f"<h3>Sponsorship confirmed ({len(confirmed)})</h3>")
        parts += [card(j) for j in confirmed]
    if unclear:
        parts.append(f"<h3 style='margin-top:20px'>Worth checking — sponsorship "
                     f"not stated clearly ({len(unclear)})</h3>")
        parts += [card(j) for j in unclear]
    if not confirmed and not unclear:
        parts.append("<p>No new matching jobs since the last check.</p>")
    parts.append("<hr style='border:none;border-top:1px solid #eee;margin:20px 0'>")
    parts.append("<div style='font-size:11px;color:#999'>Automated check of your saved "
                 "NHS Jobs search. Always confirm sponsorship on the advert before "
                 "applying.</div></div>")
    return "\n".join(parts)


def send_email(subject, html):
    sender = os.environ.get("GMAIL_ADDRESS")
    password = os.environ.get("GMAIL_APP_PASSWORD")
    if not sender or not password:
        print("ERROR: set GMAIL_ADDRESS and GMAIL_APP_PASSWORD environment "
              "variables (Gmail App Password, not your normal password).")
        sys.exit(1)
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = RECIPIENT
    msg.attach(MIMEText("Open in an HTML-capable client to view the jobs.", "plain"))
    msg.attach(MIMEText(html, "html"))
    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(sender, password)
        server.sendmail(sender, [RECIPIENT], msg.as_string())
    print(f"Email sent to {RECIPIENT}.")


# --------------------------------------------------------------------------- #
#  State                                                                        #
# --------------------------------------------------------------------------- #

def load_seen():
    if STATE_FILE.exists():
        try:
            return set(json.loads(STATE_FILE.read_text()))
        except Exception:
            return set()
    return set()


def save_seen(seen):
    STATE_FILE.write_text(json.dumps(sorted(seen)), encoding="utf-8")


# --------------------------------------------------------------------------- #
#  Main                                                                         #
# --------------------------------------------------------------------------- #

def run(debug=False):
    session = make_session()
    seen = load_seen()
    print(f"Loaded {len(seen)} previously-seen adverts.")

    # 1. Collect advert refs across search pages.
    all_refs = []
    for page in range(1, MAX_PAGES + 1):
        sep = "&" if "?" in SEARCH_URL else "?"
        url = f"{SEARCH_URL}{sep}page={page}"
        try:
            html = get(session, url,
                       debug_save="debug_search.html" if (debug and page == 1) else None)
        except Exception as e:
            print(f"  page {page}: fetch failed ({e})")
            break
        refs = find_advert_refs(html)
        print(f"  page {page}: {len(refs)} adverts")
        if not refs:
            break
        for r in refs:
            if r not in all_refs:
                all_refs.append(r)
        time.sleep(REQUEST_DELAY)

    new_refs = [r for r in all_refs if r not in seen]
    print(f"Found {len(all_refs)} adverts, {len(new_refs)} new.")

    # 2. Enrich + filter new adverts.
    confirmed, unclear = [], []
    for i, ref in enumerate(new_refs):
        url = urljoin(BASE, f"/candidate/jobadvert/{ref}")
        try:
            html = get(session, url,
                       debug_save="debug_advert.html" if (debug and i == 0) else None)
        except Exception as e:
            print(f"  {ref}: fetch failed ({e})")
            continue
        job = parse_advert(html)
        job["ref"] = ref
        job["url"] = url
        ok, reasons = passes_filters(job)
        seen.add(ref)  # mark seen once successfully fetched
        tag = "KEEP" if ok else "skip(" + ",".join(reasons) + ")"
        print(f"  {ref}: {job['title'][:48]!r} band={job['band']} "
              f"hours={job['hours']} sponsor={job['sponsorship']} -> {tag}")
        if debug:
            print(f"     parsed: {json.dumps(job, ensure_ascii=False)}")
        if ok:
            (confirmed if job["sponsorship"] == "yes" else unclear).append(job)
        time.sleep(REQUEST_DELAY)

    # 3. Email + persist.
    total = len(confirmed) + len(unclear)
    if total or SEND_WHEN_EMPTY:
        subject = (f"NHS sponsorship alert — {len(confirmed)} confirmed"
                   + (f", {len(unclear)} to check" if unclear else "")
                   if total else "NHS sponsorship alert — no new jobs")
        send_email(subject, build_email_html(confirmed, unclear))
    else:
        print("No new matches; no email sent.")

    save_seen(seen)
    print(f"Saved {len(seen)} seen adverts.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--debug", action="store_true",
                    help="save raw HTML + print parsed fields for tuning")
    args = ap.parse_args()
    run(debug=args.debug)
