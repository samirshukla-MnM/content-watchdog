# Content Watchdog

Monitors your report pages and their competitor equivalents. When wording changes — even slightly — it emails you and your boss a highlighted **previous vs updated** comparison.

Handles React/JS-rendered pages and Cloudflare-protected pages automatically.

---

## How it works

```
Excel sheet → URL list → fetch → extract clean text → hash → compare
                                                              ↓
                                              changed? → email alert
```

**Fetching escalates in three tiers, automatically per URL:**

| Tier | Method | Handles |
|---|---|---|
| 1 | `requests` | Normal static HTML — fast, ~1s |
| 2 | Playwright (headless Chromium) | React / Vue / Angular pages that need JS |
| 3 | Playwright + stealth patches | Cloudflare "Checking your browser" |

Each URL remembers the tier that worked, so later runs skip the failed attempts.

**Why you won't get spam alerts.** Raw HTML changes constantly for reasons that aren't content: rotating ads, CSRF tokens, "updated 3 minutes ago", view counters, build hashes. All of it is stripped and normalized before comparison. This was verified against a live page whose raw HTML differed between two fetches seconds apart — extracted content was identical, no alert.

---

## Setup (about 15 minutes)

### 1. Create the repo

```bash
git init
git add .
git commit -m "Content watchdog"
git remote add origin https://github.com/YOUR_NAME/content-watchdog.git
git push -u origin main
```

### 2. Get a Gmail App Password

Regular Gmail passwords will not work with SMTP.

1. Enable 2-Step Verification: <https://myaccount.google.com/security>
2. Go to <https://myaccount.google.com/apppasswords>
3. Create one named "Watchdog" — you get a 16-character code

### 3. Add GitHub Secrets

Repo → **Settings → Secrets and variables → Actions → New repository secret**

| Secret | Value |
|---|---|
| `SMTP_HOST` | `smtp.gmail.com` |
| `SMTP_PORT` | `587` |
| `SMTP_USER` | `you@gmail.com` |
| `SMTP_PASSWORD` | the 16-char app password |
| `SMTP_FROM` | `you@gmail.com` |
| `ALERT_RECIPIENTS` | `you@company.com,boss@company.com` |

Outlook/Office365: host `smtp.office365.com`, port `587`.

### 4. Deploy the dashboard on Streamlit Cloud (free)

1. <https://share.streamlit.io> → sign in with GitHub
2. **New app** → pick your repo → main file `app.py` → Deploy
3. In **Advanced settings → Secrets**, paste the same values in TOML form:

```toml
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = "587"
SMTP_USER = "you@gmail.com"
SMTP_PASSWORD = "your16charapppassword"
ALERT_RECIPIENTS = "you@company.com,boss@company.com"
```

### 5. Import your sheet

Open the app → **Import** tab → download the template or upload your own. Columns are auto-detected:

| Report Name | Our URL | Competitor 1 | Competitor 2 | Competitor 3 |
|---|---|---|---|---|
| Global EV Battery Market | https://you.com/ev | https://a.com/ev | https://b.com/ev | https://c.com/ev |

Then click **Run check now** to capture baselines.

> The first run never sends email — it's recording the starting state. Changes are detected from the second run onward.

### 6. Set the schedule

Edit `.github/workflows/monitor.yml`:

```yaml
on:
  schedule:
    - cron: '0 3 * * *'      # daily 03:00 UTC = 08:30 IST
```

| Schedule | Cron |
|---|---|
| Daily 8:30 AM IST | `0 3 * * *` |
| Twice daily | `0 3,15 * * *` |
| Every Monday | `0 3 * * 1` |
| Hourly | `0 * * * *` |

Cron is always UTC. IST = UTC + 5:30. Commit the change and Actions picks it up. You can also trigger a run manually from the **Actions** tab → *Content Monitor* → *Run workflow*.

---

## Important: where the schedule runs

The **GitHub Actions workflow** does the scheduled checking and emailing. The **Streamlit app** is the control panel — importing URLs, reviewing diffs, adjusting settings.

Streamlit Cloud sleeps inactive apps, so don't rely on it for scheduling. Actions runs regardless. Both share the same `data/watchdog.db`, which the workflow commits back to the repo after each run so history survives between runs.

If you import URLs in the Streamlit app, commit `data/watchdog.db` (or re-import there) so Actions sees the same list.

---

## Tuning

**Sensitivity** — sidebar slider. `0.0` alerts on every character. Raise to `0.5–1%` if a site is chatty.

**Monitoring only part of a page** — set a CSS selector on a URL to watch just `.report-summary` and ignore the rest.

**A site still returns nothing** — it may need a longer wait or be behind a login. Check the Run history tab for the error.

---

## Local development

```bash
pip install -r requirements.txt
playwright install chromium
streamlit run app.py
```

Run a check from the command line:

```bash
python -m core.monitor
```

Run the test suite (no network needed):

```bash
python test_pipeline.py
```

---

## Cost

Free. GitHub Actions gives 2,000 minutes/month on free accounts and is unlimited for public repos; a daily 30-URL check uses roughly 150 min/month. Streamlit Community Cloud is free.

---

## Files

```
app.py                        Streamlit dashboard
core/fetcher.py               3-tier fetching (JS + Cloudflare)
core/extractor.py             HTML → clean comparable text
core/differ.py                word-level diffs + HTML highlighting
core/notifier.py              email builder + SMTP sender
core/monitor.py               run loop
core/db.py                    SQLite storage
core/importer.py              Excel import + column detection
.github/workflows/monitor.yml scheduler
test_pipeline.py              end-to-end tests
```
