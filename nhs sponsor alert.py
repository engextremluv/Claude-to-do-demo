#!/usr/bin/env python3
"""
HealthJobsUK job alert.

Checks healthjobsuk.com across chosen sectors, keeps NEW adverts that are the
right band / contract / hours, drops only adverts that EXPLICITLY refuse visa
sponsorship, and emails the rest with a badge showing whether sponsorship was
mentioned. (HealthJobsUK usually doesn't state sponsorship, so most come
through as "not stated — check the advert", which you verify yourself.)

Runs twice a day via the GitHub Actions workflow.
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

BASE = "https://www.healthjobsuk.com"

# Sectors to watch (code -> name). Comment any out to drop that sector.
SECTORS = {
    "s1":   "Nursing & Midwifery",
    "s5":   "Health Science Services",
    "s6":   "Support Services",
    "s7":   "Administrative Services",
    "s2":   "Medical & Dental",
    "s3":   "Emergency Services",
    "s4":   "Allied Health Professions",
    "s119": "Personal Social Services",
}

RECIPIENT = "Adeyemiopeyemi1801@gmail.com"

REQUIRE_BANDS = ["3", "4", "5"]   # keep these bands; [] to allow any band
REQUIRE_CONTRACT = "permanent"    # substring match; None to disable
HOURS_KEYWORDS = ["full"]         # matches Full time / Full-time; [] to disable

# Per your choice: send everything and verify sponsorship yourself. We still
# drop adverts that EXPLICITLY say they won't sponsor (no point seeing those).
DROP_EXPLICIT_REFUSALS = True

# Newest adverts sit on the LAST pages of each sector list (the site sorts
# oldest-first). We read this many pages back from the end each run. If a run
# ever shows only stale jobs, flip READ_FROM to "start".
MAX_PAGES_BACK = 3
READ_FROM = "end"                 # "end" (newest last) or "start"
DETAIL_FETCH_CAP = 60             # max advert pages to open per run
DIGEST_CAP = 30                   # max jobs per email
REQUEST_DELAY = 1.0               # seconds between requests, be polite
SEND_WHEN_EMPTY = False
STATE_FILE = Path("seen_jobs.json")

# DEBUG: verbose per-advert logging for the first few, to confirm parsing.
# Set to False once the emails look right.
DEBUG = True

USER_AGENT = os.environ.get(
    "HJ_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
)

# --------------------------------------------------------------------------- #
#  Sponsorship classification (unchanged logic from the NHS Jobs bot)          #
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

JOB_HREF_RE = re.compile(r"/job/[^\"'>\s]*-v(\d+)")
BAND_RE = re.compile(r"Band\s*([1-9])", re.I)

HJ_STOP_LABELS = [
    "Main area", "Grade", "Contract", "Hours", "Job ref", "Employer",
    "Employer type", "Site", "Town", "Salary", "Salary period", "Closing",
    "Interview date", "Band", "Job overview", "Job summary", "Documents",
    "Apply online", "Person specification", "Date posted",
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


def label_value(text, labels):
    """Value after a label, stopping before the next field label."""
    for label in labels:
        m = re.search(rf"\b{re.escape(label)}\b\s*[:\-]?\s*(.+)", text, flags=re.IGNORECASE)
        if not m:
            continue
        val = re.sub(r"^(name|is|are|:)\s+", "", m.group(1), flags=re.I)
        cut = len(val)
        for s in HJ_STOP_LABELS:
            if s.lower() == label.lower():
                continue
            idx = val.lower().find(s.lower())
            if 0 <= idx < cut:
                cut = idx
        val = re.sub(r"\s{2,}", " ", val[:cut]).strip(" .,-|")
        if val:
            return val[:90]
    return None


def parse_list(html):
    """Return [(job_id, url, band_or_None)] for a sector list page."""
    soup = BeautifulSoup(html, "html.parser")
    out, seen_ids = [], set()
    for a in soup.find_all("a", href=True):
        m = JOB_HREF_RE.search(a["href"])
        if not m:
            continue
        jid = m.group(1)
        if jid in seen_ids:
            continue
        seen_ids.add(jid)
        text = a.get_text(" ", strip=True)
        bm = BAND_RE.search(text)
        band = bm.group(1) if bm else None
        out.append((jid, urljoin(BASE, a["href"]), band))
    return out


def last_page_of(html):
    pgs = [int(n) for n in re.findall(r"_pg=(\d+)", html)]
    return max(pgs) if pgs else 1


def parse_advert(html):
    soup = BeautifulSoup(html, "html.parser")
    title = ""
    if soup.title:
        tm = re.match(r"\s*Job vacancy:\s*(.+?),", soup.title.get_text(" ", strip=True))
        if tm:
            title = tm.group(1).strip()
    if not title:
        h = soup.find(["h1", "h2"])
        title = h.get_text(" ", strip=True) if h else ""

    norm = re.sub(r"[\s\u00a0]+", " ", soup.get_text(" ", strip=True))

    grade = label_value(norm, ["Grade"]) or ""
    bm = BAND_RE.search(grade) or BAND_RE.search(norm)
    band = bm.group(1) if bm else None

    contract = label_value(norm, ["Contract"])

    hours_raw = label_value(norm, ["Hours"]) or ""
    if re.search(r"full[\s\-]?time", hours_raw, re.I) or re.search(r"full[\s\-]?time", norm[:600], re.I):
        hours = "Full time"
    elif re.search(r"part[\s\-]?time", hours_raw, re.I):
        hours = "Part time"
    else:
        hours = hours_raw or None

    salary = label_value(norm, ["Salary"])
    employer = label_value(norm, ["Employer"])
    location = label_value(norm, ["Town", "Site"])
    clm = re.search(r"Closing\s*[:\-]?\s*(\d{1,2}/\d{1,2}/\d{4})", norm)
    closing = clm.group(1) if clm else label_value(norm, ["Closing"])

    verdict, phrase = classify_sponsorship(norm)
    return {
        "title": title, "band": band, "contract": contract, "hours": hours,
        "salary": salary, "employer": employer, "location": location,
        "closing": closing, "sponsorship": verdict, "sponsor_phrase": phrase,
        "_norm": norm,
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
            "yes": "✅ Sponsorship stated as available",
            "likely": "🟢 Sponsorship likely (standard welcome wording)",
            "unknown": "❔ Sponsorship not stated — check the advert",
        }.get(job["sponsorship"], "")
        rows = "".join(
            f"<tr><td style='padding:2px 10px 2px 0;color:#555'>{k}</td>"
            f"<td style='padding:2px 0'><b>{v or '—'}</b></td></tr>"
            for k, v in [
                ("Employer", job["employer"]), ("Location", job["location"]),
                ("Sector", job.get("sector")), ("Band", job["band"]),
                ("Contract", job["contract"]), ("Hours", job["hours"]),
                ("Salary", job["salary"]), ("Closing", job["closing"]),
            ]
        )
        return (
            f"<div style='border:1px solid #e2e2e2;border-radius:10px;"
            f"padding:14px 16px;margin:12px 0'>"
            f"<div style='font-size:16px;font-weight:700;margin-bottom:6px'>"
            f"<a href='{job['url']}' style='color:#005eb8;text-decoration:none'>"
            f"{job['title'] or 'Health job'}</a></div>"
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
        "<h2 style='color:#005eb8;margin-bottom:2px'>HealthJobsUK alert</h2>",
        f"<div style='color:#777;font-size:12px;margin-bottom:14px'>{when}</div>",
    ]
    if mentioned:
        parts.append(f"<h3>Sponsorship mentioned ({len(mentioned)})</h3>")
        parts += [card(j) for j in mentioned]
    if unstated:
        parts.append(f"<h3 style='margin-top:20px'>Sponsorship not stated — "
                     f"check each advert ({len(unstated)})</h3>")
        parts += [card(j) for j in unstated]
    if not mentioned and not unstated:
        parts.append("<p>No new matching jobs since the last check.</p>")
    parts.append("<hr style='border:none;border-top:1px solid #eee;margin:20px 0'>")
    parts.append("<div style='font-size:11px;color:#999'>Automated check of "
                 "HealthJobsUK. This site usually doesn't state visa sponsorship, "
                 "so confirm it on the advert before applying.</div></div>")
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

def collect_new_candidates(session, seen, debug=False):
    """Scan the newest pages of each sector; return new band-matching jobs."""
    candidates = []  # (jid, url, sector_name)
    for i, (sid, name) in enumerate(SECTORS.items()):
        try:
            p1 = get(session, f"{BASE}/job_list/{sid}",
                     debug_save="debug_list.html" if (debug and i == 0) else None)
        except Exception as e:
            print(f"  [{name}] page 1 fetch failed ({e})")
            continue
        last = last_page_of(p1)
        if READ_FROM == "end":
            pages = list(range(last, max(0, last - MAX_PAGES_BACK), -1)) or [1]
        else:
            pages = list(range(1, min(last, MAX_PAGES_BACK) + 1))
        new_here = 0
        for pg in pages:
            html = p1 if pg == 1 else None
            if html is None:
                try:
                    html = get(session, f"{BASE}/job_list/{sid}?_pg={pg}&_pgid=")
                except Exception as e:
                    print(f"  [{name}] page {pg} fetch failed ({e})")
                    continue
                time.sleep(REQUEST_DELAY)
            page_new = 0
            for jid, url, band in parse_list(html):
                if jid in seen:
                    continue
                page_new += 1
                if REQUIRE_BANDS and (band in REQUIRE_BANDS):
                    candidates.append((jid, url, name))
                elif REQUIRE_BANDS:
                    seen.add(jid)          # wrong band — don't reconsider
                else:
                    candidates.append((jid, url, name))
            if page_new == 0:
                break                       # reached already-seen territory
            new_here += page_new
        print(f"  [{name}] last page {last}; {new_here} new adverts scanned")
    return candidates


def run(debug=False):
    session = make_session()
    seen = load_seen()
    first_run = len(seen) == 0
    print(f"Loaded {len(seen)} previously-seen adverts"
          f"{' (first run)' if first_run else ''}.")

    candidates = collect_new_candidates(session, seen, debug=debug)
    print(f"{len(candidates)} new band-{'/'.join(REQUIRE_BANDS)} candidates to check"
          f" (cap {DETAIL_FETCH_CAP}).")

    mentioned, unstated = [], []
    for i, (jid, url, sector) in enumerate(candidates[:DETAIL_FETCH_CAP]):
        try:
            html = get(session, url,
                       debug_save="debug_advert.html" if (debug and i == 0) else None)
        except Exception as e:
            print(f"  v{jid}: fetch failed ({e})")
            continue
        job = parse_advert(html)
        job.pop("_norm", None)
        job.update(ref=jid, url=url, sector=sector)
        ok, reasons = passes_filters(job)
        seen.add(jid)
        tag = "KEEP" if ok else "skip(" + ",".join(reasons) + ")"
        print(f"  v{jid}: {str(job['title'])[:42]!r} band={job['band']} "
              f"contract={job['contract']} hours={job['hours']} "
              f"sponsor={job['sponsorship']} -> {tag}")
        if ok:
            (mentioned if job["sponsorship"] in ("yes", "likely") else unstated).append(job)
        time.sleep(REQUEST_DELAY)

    mentioned, unstated = mentioned[:DIGEST_CAP], unstated[:DIGEST_CAP]
    total = len(mentioned) + len(unstated)
    if total or SEND_WHEN_EMPTY:
        subject = (f"HealthJobsUK alert — {total} job{'s' if total != 1 else ''}"
                   if total else "HealthJobsUK alert — no new jobs")
        send_email(subject, build_email_html(mentioned, unstated))
    else:
        print("No new matches; no email sent.")

    save_seen(seen)
    print(f"Saved {len(seen)} seen adverts.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--debug", action="store_true", help="save raw HTML samples")
    args = ap.parse_args()
    run(debug=args.debug or DEBUG)
