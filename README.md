# NHS sponsorship job alert

Checks your saved NHS Jobs search twice a day, keeps only **new** adverts that
match your filters **and** confirm Skilled Worker visa sponsorship, and emails
you a digest.

## What it filters on
- **Title** — any healthcare / medical line (broad list of clinical terms;
  keeps nurse, HCA, therapy, pharmacy, midwifery, mental health, lab, etc. and
  drops corporate roles like finance / IT / HR). Empty the list to keep every
  Band 3 role regardless of field.
- **Band** — 3
- **Contract** — permanent
- **Hours** — matches **Full time** (so 36 *or* 37.5 hours both qualify)
- **Sponsorship** — only adverts where the trust confirms sponsorship. Adverts
  that say *"unable to provide sponsorship"*, *"unlikely to meet the criteria"*
  or *"will not require sponsorship"* are rejected even if they also carry the
  standard NHS "sponsorship welcome" footer.

All of these live at the top of `nhs_sponsor_alert.py` under `SETTINGS`.

## One-time setup

### 1. Gmail App Password (for sending)
The digest is sent from a Gmail account over SMTP. Gmail needs an **App
Password**, not your normal password:
1. Turn on 2-Step Verification on the Google account.
2. Go to Google Account → Security → App passwords → create one.
3. You get a 16-character code. That's `GMAIL_APP_PASSWORD`.
`GMAIL_ADDRESS` is the sending account (can be the same inbox you receive to).

### 2a. Run in the cloud (recommended — GitHub Actions)
Runs on schedule even when your laptop is off.
1. Put these files in a **private** GitHub repo.
2. Repo → Settings → Secrets and variables → Actions → add two secrets:
   `GMAIL_ADDRESS` and `GMAIL_APP_PASSWORD`.
3. The workflow (`.github/workflows/nhs-alert.yml`) runs at 06:00 and 16:00 UTC
   and commits `seen_jobs.json` back so it remembers what it already sent.
4. Trigger a first run manually: Actions tab → *NHS sponsorship alert* → *Run workflow*.

### 2b. Run locally (cron / Task Scheduler)
```bash
pip install -r requirements.txt
export GMAIL_ADDRESS="you@gmail.com"
export GMAIL_APP_PASSWORD="xxxxxxxxxxxxxxxx"
python nhs_sponsor_alert.py
```
Then schedule it twice a day:
- **Linux/macOS cron:** `0 6,16 * * * cd /path/to/nhs-sponsor-alert && GMAIL_ADDRESS=... GMAIL_APP_PASSWORD=... /usr/bin/python3 nhs_sponsor_alert.py`
- **Windows:** Task Scheduler → two daily triggers → action runs `python nhs_sponsor_alert.py`.

## First run / tuning
jobs.nhs.uk blocks automated inspection, so the page-parsing selectors are
best-effort. If a run prints `band=None hours=None` for everything, run:
```bash
python nhs_sponsor_alert.py --debug
```
It saves `debug_search.html` and `debug_advert.html` and prints the parsed
fields. Send me those two files and I'll pin the selectors to the exact markup.

## If the cloud IP gets blocked
GitHub's servers use datacenter IPs. If NHS blocks them, run locally instead
(2b) — a home IP with the browser `User-Agent` behaves like a normal visitor.
You can also override the agent with the `NHS_USER_AGENT` environment variable.
