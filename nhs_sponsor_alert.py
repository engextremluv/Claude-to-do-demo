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
HOURS_KEYWORDS = ["full"]       # matches Full time / Full-time (36 or 37.5), not part-time
REQUIRE_SPONSORSHIP = True      # keep only adverts that confirm sponsorship

# If a field (band / contract / hours) can't be parsed from the page, DON'T
# drop the job for it — show it with the field marked "unknown". Better a
# maybe than a miss. Set True to drop jobs whose fields can't be confirmed.
STRICT_FIELD_FILTERS = False

# Include adverts where sponsorship is unclear (only the generic NHS footer,
# no trust-specific statement) in a separate "verify manually" section.
INCLUDE_UNKNOWN_SPONSORSHIP = False

MAX_PAGES = 5                   # search-result pages to scan (about 20 jobs each)

# DEBUG: while True, the script re-checks every advert (ignores the "seen" list),
# includes jobs whose sponsorship is unclear so you get a real digest to eyeball,
# caps that digest, and prints a couple of diagnostic lines per advert into the
# log. Flip to False once the emails look right, to go back to strict mode.
DEBUG = False
DIGEST_CAP = 15                 # max jobs per email while DEBUG is on

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

# The GENERIC NHS footer. Present on many adverts. It means the trust hasn't
# ruled sponsorship out (no negative statement) — a "likely / open", not a
# guaranteed yes. Only counts when no negative pattern is found.
LIKELY_PATTERNS = [
    "applications from job seekers who require current skilled worker sponsorship to work in the uk are welcome",
    "certificate of sponsorship applications from job seekers who require current skilled worker",
    "sponsorship to work in the uk are welcome and will be considered alongside all other applications",
]


def classify_sponsorship(text: str):
    """Return ('yes'|'likely'|'no'|'unknown', matched_phrase)."""
    low = re.sub(r"\s+", " ", text.lower())
    for p in NEGATIVE_PATTERNS:
        if p in low:
            return "no", p
    for p in POSITIVE_PATTERNS:
        if p in low:
            return "yes", p
    for p in LIKELY_PATTERNS:
        if p in low:
            return "likely", p
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


STOP_LABELS = [
    "Employer", "Employer name", "Organisation", "Town", "Location", "Site",
    "Address", "Salary", "Pay", "Contract", "Working pattern", "Working",
    "Hours", "Pay scheme", "Band", "Grade", "Closing", "Date posted",
    "Reference", "Job reference", "Job description", "Job summary", "Job type",
    "Documents", "Main area", "Contract type", "We welcome", "Applications from",
    "Interview", "Apply for this job", "name",
]


def label_value(text, labels):
    """Find the value after a label, stopping before the next field label."""
    for label in labels:
        m = re.search(rf"{re.escape(label)}\s*[:\-]?\s*(.+)", text, flags=re.IGNORECASE)
        if not m:
            continue
        val = re.sub(r"^(name|is|are|:)\s+", "", m.group(1), flags=re.I)
        cut = len(val)
        for s in STOP_LABELS:
            if s.lower() == label.lower():
                continue
            idx = val.lower().find(s.lower())
            if 0 <= idx < cut:
                cut = idx
        val = re.sub(r"\s{2,}", " ", val[:cut]).strip(" .,-|")
        if val:
            return val[:60]
    return None


def parse_advert(html):
    soup = BeautifulSoup(html, "html.parser")

    # Title: skip the cookie-consent banner heading, take the first real one.
    title = ""
    for h in soup.find_all(["h1", "h2"]):
        t = h.get_text(" ", strip=True)
        if t and "cookies on nhs" not in t.lower():
            title = t
            break
    if not title and soup.title:
        title = soup.title.get_text(strip=True).split(" - ")[0].strip()

    # Whitespace/newlines collapsed to single spaces so labels and their values
    # sit next to each other (the site puts them on separate lines).
    norm = re.sub(r"[\s\u00a0]+", " ", soup.get_text(" ", strip=True))

    bm = re.search(r"\bBand\s*([1-9])\b", norm, re.I)
    band = bm.group(1) if bm else None

    cm = re.search(
        r"Contract\s*[:\-]?\s*"
        r"(Permanent|Fixed[\- ]term|Fixed term|Bank|Locum|Secondment|"
        r"Apprenticeship|Temporary|Training)",
        norm, re.I,
    )
    contract = cm.group(1) if cm else None

    if re.search(r"full[\s\-]?time", norm, re.I):
        hours = "Full time"
    elif re.search(r"part[\s\-]?time", norm, re.I):
        hours = "Part time"
    else:
        wm = re.search(r"(\d{2}(?:\.\d)?)\s*hours", norm, re.I)
        hours = f"{wm.group(1)} hours" if wm else None

    sm = re.search(r"£[\d,]+(?:\.\d+)?(?:\s*(?:to|-|–|—)\s*£[\d,]+(?:\.\d+)?)?", norm)
    salary = sm.group(0) if sm else None
    employer = label_value(norm, ["Employer name", "Employer", "Organisation"])
    location = label_value(norm, ["Town", "Address", "Location", "Site"])
    clm = re.search(r"[Cc]losing date[^\d]{0,12}(\d{1,2}\s+[A-Za-z]+\s+\d{4})", norm)
    closing = clm.group(1) if clm else label_value(norm, ["Closing date", "Closing"])

    verdict, phrase = classify_sponsorship(norm)
    return {
        "title": title, "band": band, "contract": contract, "hours": hours,
        "salary": salary, "employer": employer, "location": location,
        "closing": closing, "sponsorship": verdict, "sponsor_phrase": phrase,
        "_norm": norm,  # kept only for debug context; not shown in the email
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


def passes_filters(job, include_unknown=INCLUDE_UNKNOWN_SPONSORSHIP):
    reasons = []
    if not title_ok(job["title"]):
        reasons.append("title")
    if REQUIRE_BAND is not None:  # strict: an unconfirmed band is not Band 3
        if job["band"] is None or str(job["band"]) != str(REQUIRE_BAND):
            reasons.append(f"band!={REQUIRE_BAND}")
    if REQUIRE_CONTRACT is not None and not field_ok(job["contract"], REQUIRE_CONTRACT):
        reasons.append("contract")
    if HOURS_KEYWORDS and not field_ok(job["hours"], None, contains_list=HOURS_KEYWORDS):
        reasons.append("hours")
    if REQUIRE_SPONSORSHIP:
        s = job["sponsorship"]
        if s == "no":
            reasons.append("no-sponsorship")
        elif s == "unknown" and not include_unknown:
            reasons.append("sponsorship-unknown")
        # "yes" (confirmed) and "likely" (generic footer, no refusal) both pass
    return (len(reasons) == 0), reasons


# --------------------------------------------------------------------------- #
#  Email                                                                        #
# --------------------------------------------------------------------------- #

def build_email_html(confirmed, unclear):
    def card(job):
        badge = {
            "yes": "✅ Sponsorship confirmed",
            "likely": "🟢 Likely — open to sponsorship applications",
            "unknown": "❔ Sponsorship unclear — verify",
        }.get(job["sponsorship"], "")
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
        parts.append(f"<h3>Sponsorship confirmed or likely ({len(confirmed)})</h3>")
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

    new_refs = all_refs if DEBUG else [r for r in all_refs if r not in seen]
    print(f"Found {len(all_refs)} adverts, {len(new_refs)} to check"
          f"{' (DEBUG: ignoring seen list)' if DEBUG else ' new'}.")

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
        norm = job.pop("_norm", "")
        job["ref"] = ref
        job["url"] = url
        include_unknown = INCLUDE_UNKNOWN_SPONSORSHIP or DEBUG
        ok, reasons = passes_filters(job, include_unknown=include_unknown)
        seen.add(ref)
        tag = "KEEP" if ok else "skip(" + ",".join(reasons) + ")"
        print(f"  {ref}: {job['title'][:46]!r} band={job['band']} "
              f"hours={job['hours']} sponsor={job['sponsorship']} -> {tag}")
        if DEBUG and i < 3:
            ctx = re.search(r".{0,90}sponsor.{0,180}", norm, re.I)
            print(f"     SPONSOR-CONTEXT: {ctx.group(0).strip() if ctx else 'no sponsorship text found'}")
        if ok:
            (unclear if job["sponsorship"] == "unknown" else confirmed).append(job)
        time.sleep(REQUEST_DELAY)

    if DEBUG:  # keep the test email a manageable size
        confirmed, unclear = confirmed[:DIGEST_CAP], unclear[:DIGEST_CAP]

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
