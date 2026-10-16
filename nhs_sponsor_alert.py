#!/usr/bin/env python3
"""
NHS Jobs (jobs.nhs.uk) sponsorship alert — widened search.

Runs several clean role searches, keeps NEW adverts that are Band 3/4/5,
permanent and full time, drops only adverts that EXPLICITLY refuse visa
sponsorship, and emails the rest with a badge showing whether sponsorship was
confirmed, likely, or not stated. Runs twice a day via GitHub Actions.
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
from urllib.parse import quote, urljoin

import requests
from bs4 import BeautifulSoup

# --------------------------------------------------------------------------- #
#  SETTINGS — edit these                                                       #
# --------------------------------------------------------------------------- #

BASE = "https://www.jobs.nhs.uk"

# WIDENED: several clean role searches instead of one messy phrase. Their
# results are combined. Add or remove keywords freely.
SEARCH_KEYWORDS = [
    "healthcare assistant",
    "nursing assistant",
    "nursing associate",
    "assistant practitioner",
    "clinical support worker",
    "healthcare support worker",
    "support worker",
    "care assistant",
    "therapy assistant",
    "maternity support worker",
    "phlebotomist",
]

RECIPIENT = "Adeyemiopeyemi1801@gmail.com"

REQUIRE_BANDS = ["3", "4", "5"]   # keep these bands; [] to allow any band
REQUIRE_CONTRACT = "permanent"    # substring match; None to disable
HOURS_KEYWORDS = ["full"]         # matches Full time / Full-time; [] to disable

# Send everything and verify sponsorship yourself — but still drop adverts that
# EXPLICITLY say they won't sponsor (no point seeing those).
DROP_EXPLICIT_REFUSALS = True

MAX_PAGES = 3                     # search-result pages per keyword
DETAIL_FETCH_CAP = 80             # max advert pages to open per run
DIGEST_CAP = 30                   # max jobs per email
REQUEST_DELAY = 1.0               # seconds between requests, be polite
SEND_WHEN_EMPTY = False
STATE_FILE = Path("seen_jobs.json")

# DEBUG: verbose logging + saves a sample advert page. Set False once happy.
DEBUG = False

USER_AGENT = os.environ.get(
    "NHS_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
)

# --------------------------------------------------------------------------- #
#  Sponsorship classification                                                  #
# --------------------------------------------------------------------------- #

NEGATIVE_PATTERNS = [
    "unable to provide skilled worker", "unable to offer skilled worker",
    "unable to provide sponsorship", "unable to offer sponsorship",
    "not able to offer sponsorship", "cannot offer sponsorship",
    "do not offer sponsorship", "does not offer sponsorship",
    "not offering sponsorship", "unable to sponsor", "not able to sponsor",
    "cannot sponsor", "will not require sponsorship",
    "not require sponsorship if successful",
    "does not meet the criteria for sponsorship",
    "unlikely that the criteria required to support skilled worker",
    "unlikely to meet the criteria required to support skilled worker",
    "not eligible for skilled worker visa sponsorship",
    "not eligible for sponsorship", "sponsorship is not available",
    "sponsorship will not be available", "we are not a licensed sponsor",
    "this role does not qualify for sponsorship", "this post does not attract",
    "not able to provide sponsorship", "does not attract sponsorship",
    "unable to support sponsorship", "not in a position to offer sponsorship",
]

NEGATIVE_REGEXES = [re.compile(p) for p in [
    r"not\s+(?:be\s+)?eligible\s+for\s+(?:[a-z ]{0,40}?)?sponsorship",
    r"not\s+(?:be\s+)?eligible\s+for\s+(?:a\s+)?skilled worker",
    r"(?:doesn['’]?t|does not|do not|not|won['’]?t|will not)\s+meet\s+"
    r"the\s+(?:minimum\s+)?salary\s+(?:threshold|requirement)",
    r"below\s+the\s+(?:minimum\s+)?salary\s+(?:threshold|requirement)",
    r"will\s+not\s+(?:be\s+)?(?:able\s+to\s+)?(?:offer|provide|sponsor)"
    r"(?:\s+(?:skilled worker\s+)?(?:visa\s+)?sponsorship)?",
    r"(?:unable|not\s+able)\s+to\s+(?:offer|provide|sponsor)",
    r"(?:cannot|can\s*not|can['’]t)\s+(?:offer|provide|sponsor)\s+"
    r"(?:[a-z ]{0,20}?)?sponsorship",
    r"not\s+(?:a\s+)?(?:licen[sc]ed|registered)\s+sponsor",
    r"does\s+not\s+(?:qualify|meet\s+the\s+criteria)\s+for\s+sponsorship",
]]

POSITIVE_PATTERNS = [
    "we welcome applications from candidates who require skilled worker visa sponsorship",
    "welcome applications from candidates who require skilled worker visa sponsorship",
    "candidates who require skilled worker visa sponsorship to work in the uk, and these will be considered",
    "we are able to offer sponsorship", "we can offer skilled worker",
    "we can offer sponsorship", "happy to offer sponsorship",
    "able to offer skilled worker visa sponsorship",
    "this role is eligible for skilled worker visa sponsorship",
    "this post is eligible for sponsorship", "sponsorship is available for this",
    "sponsorship may be available for this", "we are a licensed sponsor and can",
]

LIKELY_PATTERNS = [
    "applications from job seekers who require current skilled worker sponsorship to work in the uk are welcome",
    "certificate of sponsorship applications from job seekers who require current skilled worker",
    "sponsorship to work in the uk are welcome and will be considered alongside all other applications",
]


def classify_sponsorship(text):
    """Return ('yes'|'likely'|'no'|'unknown', matched_phrase)."""
    low = re.sub(r"\s+", " ", text.lower())
    for rx in NEGATIVE_REGEXES:
        m = rx.search(low)
        if m:
            return "no", m.group(0)[:80]
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

ADVERT_RE = re.compile(r"/candidate/jobadvert/([A-Za-z0-9][A-Za-z0-9\-]+)")
BAND_RE = re.compile(r"\bBand\s*([1-9])\b", re.I)

STOP_LABELS = [
    "Employer", "Employer name", "Organisation", "Town", "Location", "Site",
    "Address", "Salary", "Pay", "Contract", "Working pattern", "Working",
    "Hours", "Pay scheme", "Band", "Grade", "Closing", "Date posted",
    "Reference", "Job reference", "Job description", "Job summary", "Job type",
    "Documents", "Main area", "Contract type", "We welcome", "Applications from",
    "Interview", "Apply for this job", "name",
]


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
    refs, seen = [], set()
    for m in ADVERT_RE.finditer(html):
        r = m.group(1)
        if r not in seen:
            seen.add(r)
            refs.append(r)
    return refs


def label_value(text, labels):
    for label in labels:
        m = re.search(rf"\b{re.escape(label)}\b\s*[:\-]?\s*(.+)", text, flags=re.IGNORECASE)
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
            return val[:90]
    return None


def parse_advert(html):
    soup = BeautifulSoup(html, "html.parser")
    title = ""
    for h in soup.find_all(["h1", "h2"]):
        t = h.get_text(" ", strip=True)
        if t and "cookies on nhs" not in t.lower():
            title = t
            break
    if not title and soup.title:
        title = soup.title.get_text(strip=True).split(" - ")[0].strip()

    norm = re.sub(r"[\s\u00a0]+", " ", soup.get_text(" ", strip=True))

    bm = BAND_RE.search(norm)
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
    }


# --------------------------------------------------------------------------- #
#  Filtering                                                                    #
# --------------------------------------------------------------------------- #

def passes_filters(job):
    reasons = []
    if REQUIRE_BANDS:
        if job["band"] is None or str(job["band"]) not in REQUIRE_BANDS:
            reasons.append(f"band!={'/'.join(REQUIRE_BANDS)}")
    if REQUIRE_CONTRACT is not None:
        c = job["contract"]
        if c is None or REQUIRE_CONTRACT.lower() not in c.lower():
            reasons.append("contract")
    if HOURS_KEYWORDS:
        h = job["hours"]
        if h is None or not any(k.lower() in h.lower() for k in HOURS_KEYWORDS):
            reasons.append("hours")
    if DROP_EXPLICIT_REFUSALS and job["sponsorship"] == "no":
        reasons.append("refuses-sponsorship")
    return (len(reasons) == 0), reasons


# --------------------------------------------------------------------------- #
#  Email                                                                        #
# --------------------------------------------------------------------------- #

def build_email_html(mentioned, unstated):
    def card(job):
        badge = {
            "yes": "✅ Sponsorship confirmed",
            "likely": "🟢 Sponsorship likely (standard welcome wording)",
            "unknown": "❔ Sponsorship not stated — check the advert",
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
            f"<div style='display:inline-block;font-size:12px;background:#eef3f8;"
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
        "<h2 style='color:#005eb8;margin-bottom:2px'>NHS Jobs alert</h2>",
        f"<div style='color:#777;font-size:12px;margin-bottom:14px'>{when}</div>",
    ]
    if mentioned:
        parts.append(f"<h3>Sponsorship confirmed or likely ({len(mentioned)})</h3>")
        parts += [card(j) for j in mentioned]
    if unstated:
        parts.append(f"<h3 style='margin-top:20px'>Sponsorship not stated — "
                     f"check each advert ({len(unstated)})</h3>")
        parts += [card(j) for j in unstated]
    if not mentioned and not unstated:
        parts.append("<p>No new matching jobs since the last check.</p>")
    parts.append("<hr style='border:none;border-top:1px solid #eee;margin:20px 0'>")
    parts.append("<div style='font-size:11px;color:#999'>Automated check of NHS "
                 "Jobs. Always confirm sponsorship on the advert before applying."
                 "</div></div>")
    return "\n".join(parts)


def send_email(subject, html):
    sender = os.environ.get("GMAIL_ADDRESS")
    password = os.environ.get("GMAIL_APP_PASSWORD")
    if not sender or not password:
        print("ERROR: set GMAIL_ADDRESS and GMAIL_APP_PASSWORD environment variables.")
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

def collect_refs(session):
    """Run every keyword search, return combined unique advert refs."""
    all_refs = []
    for kw in SEARCH_KEYWORDS:
        found = 0
        for pg in range(1, MAX_PAGES + 1):
            url = (f"{BASE}/candidate/search/results"
                   f"?keyword={quote(kw)}&language=en&page={pg}")
            try:
                html = get(session, url)
            except Exception as e:
                print(f"  [{kw}] page {pg} fetch failed ({e})")
                break
            refs = find_advert_refs(html)
            if not refs:
                break
            for r in refs:
                if r not in all_refs:
                    all_refs.append(r)
                    found += 1
            time.sleep(REQUEST_DELAY)
        print(f"  [{kw}] +{found} new refs")
    return all_refs


def run(debug=False):
    session = make_session()
    seen = load_seen()
    print(f"Loaded {len(seen)} previously-seen adverts"
          f"{' (first run)' if not seen else ''}.")

    all_refs = collect_refs(session)
    new_refs = [r for r in all_refs if r not in seen]
    print(f"{len(all_refs)} adverts found across searches, {len(new_refs)} new "
          f"(will open up to {DETAIL_FETCH_CAP}).")

    mentioned, unstated = [], []
    for i, ref in enumerate(new_refs[:DETAIL_FETCH_CAP]):
        url = urljoin(BASE, f"/candidate/jobadvert/{ref}")
        try:
            html = get(session, url,
                       debug_save="debug_advert.html" if (debug and i == 0) else None)
        except Exception as e:
            print(f"  {ref}: fetch failed ({e})")
            continue
        job = parse_advert(html)
        job.update(ref=ref, url=url)
        ok, reasons = passes_filters(job)
        seen.add(ref)
        tag = "KEEP" if ok else "skip(" + ",".join(reasons) + ")"
        print(f"  {ref}: {str(job['title'])[:42]!r} band={job['band']} "
              f"contract={job['contract']} hours={job['hours']} "
              f"sponsor={job['sponsorship']} -> {tag}")
        if ok:
            (mentioned if job["sponsorship"] in ("yes", "likely") else unstated).append(job)
        time.sleep(REQUEST_DELAY)

    mentioned, unstated = mentioned[:DIGEST_CAP], unstated[:DIGEST_CAP]
    total = len(mentioned) + len(unstated)
    if total or SEND_WHEN_EMPTY:
        subject = (f"NHS Jobs alert — {total} job{'s' if total != 1 else ''}"
                   if total else "NHS Jobs alert — no new jobs")
        send_email(subject, build_email_html(mentioned, unstated))
    else:
        print("No new matches; no email sent.")

    save_seen(seen)
    print(f"Saved {len(seen)} seen adverts.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()
    run(debug=args.debug or DEBUG)
