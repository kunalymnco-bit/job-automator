# Job Application Automator - India (Mumbai), any candidate

Upload a candidate's CV, press **FIND AND PREPARE ALL MATCHING JOB APPLICATIONS**, and the tool:

```
SEARCH JOBS (LinkedIn, Naukri, Indeed India, Foundit, company Greenhouse / Lever / Workday pages)
  -> filter by preferences (Mumbai, min CTC, posted within 26 days, full-time, no fee / MLM jobs)
  -> read title + full job description
  -> match score (role, skills, experience, qualification - every point explained)
  -> remove duplicates / already-applied jobs
  -> open application -> fill standard details -> upload CV -> write answers from the profile
  -> STOP  (the final Submit button can never be clicked by the tool)
  -> YOU review the open browser tab and click Submit
  -> the tracker records it as Submitted
```

## Per platform
| Platform | Search | Form filled | Your part |
|---|---|---|---|
| LinkedIn Easy Apply | yes | all steps up to "Submit application" | check, click Submit |
| LinkedIn / Naukri / Indeed jobs that apply on Greenhouse, Lever, Workday | yes | yes | check, click Submit |
| Indeed Apply | yes | all steps up to "Submit your application" | check, click Submit |
| Naukri, Foundit | yes | - (their "Apply" button submits in one click) | press Apply; Naukri follow-up questions are pre-typed for you |
| Other company websites | yes | best effort | finish what is left |

Questions the profile cannot answer truthfully (gender, consent boxes, "years with SAP" when SAP is not in the CV, US work
authorisation ...) are highlighted in yellow and listed in the review queue - the tool never guesses them.

**Important:** LinkedIn, Naukri and Indeed do not allow automated use in their terms. You chose to use it anyway; the tool runs
in a normal visible browser, at a slow human pace, logs in only when *you* log in, and pauses for any security check. Accounts
can still be restricted by these sites - keep runs moderate.

## Run on Windows
1. Install Python 3.11+ from python.org (tick "Add python.exe to PATH"). Google Chrome is used if installed.
2. Double-click `run_dashboard.bat` (first run installs everything, a few minutes).
3. Dashboard opens at http://localhost:8501

## For each candidate
1. Sidebar -> **Add candidate**.
2. **CV & Profile** -> **Download CV template**, fill it, upload (any CV works; the template is read exactly).
3. Fill what is still blank (CTC, notice period, designation ...) -> **Confirm profile**.
4. Dashboard -> **Log in to job sites (first time)** -> on the worker PC, log in to LinkedIn / Naukri / Foundit in the window that opens.
5. Dashboard -> **FIND AND PREPARE ...** -> watch progress -> review the tabs -> Submit the ones you want.
   Tip: tick **Search only** for a first run to see which jobs it finds without opening forms.

## Settings (Preferences tab)
Search words for freshers / experienced, minimum match score (default 55), how many applications stay open at a time
(default 15 - more are prepared as you finish), result pages per search, company career pages for Greenhouse / Lever / Workday.

Optional: set the environment variable `ANTHROPIC_API_KEY` (and `pip install anthropic`) for more natural written answers.
Without it, the built-in writer is used. Written answers only use facts from the confirmed profile.

## Online website (free, works without your PC)
```
 Phone / any device ──> https://<your-app>.streamlit.app  (Streamlit Community Cloud, free)
                                  │  login, admin, candidates, CVs, profile, tracker,
                                  │  career-site search (Greenhouse / Lever / Workday)
                                  ▼
                        Supabase Postgres (free) - all data
                                  ▲
 PC with run_worker.bat ──────────┘  LinkedIn / Naukri / Indeed / Foundit: opens the browser HERE when a run
 (only when you want applications prepared)    is started from the website; you review + Submit on this PC
```
Runs started while the PC worker is off simply wait and start as soon as `run_worker.bat` is opened.

### One-time setup (about 20 minutes)
**1. Supabase (database)** - supabase.com -> Start free -> New project (Region: **Mumbai / ap-south-1**,
   database password: letters + numbers only). When ready: **Connect** -> **Session pooler** -> copy the URI and put your
   database password in place of `[YOUR-PASSWORD]`.

**2. Your PC** - in this folder copy `cloud_settings.example.toml` to `cloud_settings.toml`, paste the URI between the quotes.
   Double-click `create_admin.bat` (it should say "Database: ONLINE") and choose your admin login + password.

**3. GitHub (code)** - github.com -> sign up -> **New repository** (name e.g. `job-automator`, **Private**) ->
   "uploading an existing file" -> drag in everything from this folder EXCEPT `.venv`, `data` and `cloud_settings.toml`
   -> Commit.

**4. Streamlit Community Cloud (website)** - share.streamlit.io -> sign in with GitHub -> **Create app** ->
   pick the repository, branch `main`, file `app.py`, choose a URL -> **Advanced settings -> Secrets**, paste:
   `DATABASE_URL = "<the same Supabase URI>"` -> Deploy. Open the link, log in as admin, create users in the **Admin** tab.

**5. Job-site automation** - on the PC double-click `run_worker.bat` whenever you want LinkedIn / Naukri / Indeed / Foundit
   applications prepared. The website shows "PC worker online". Review and Submit on that PC (from your phone you can use
   Chrome Remote Desktop).

### Free plan notes
* Streamlit Cloud puts the app to sleep after a few days without visitors - the next visitor clicks "wake up" (~30 s).
* Supabase free: 500 MB database (CVs are stored inside it - roughly 2,000 CVs), pauses after 1 week without use;
  restore it with one click in the Supabase dashboard.
* Candidates' CVs and details are stored on Supabase - get each candidate's consent.

### Updating the website later
Change / replace files in the GitHub repository (Add file -> Upload files, same names) -> Commit. Streamlit Cloud
redeploys automatically within a minute. Users, candidates and history stay in Supabase. On the PC, replace the program
files too (keep `data` and `cloud_settings.toml`).

### Only on your own PC (no internet setup)
Skip the steps above and double-click `run_dashboard.bat` - it uses a local database and starts the worker as well.

## Privacy
Online: users, candidates, CVs, tracker and logs are in your Supabase database; locally: `data/tracker.db`.
`data/browser/<name>/` on the worker PC holds each candidate's job-site logins (never uploaded). User passwords are stored only as hashes; job-site passwords are never stored by the tool.

## Tests
```
python -m pytest tests
```
Covers: never-submit guard, duplicate detection, CV template, filters, match score, answers, and the form filler on mock
LinkedIn-style and Greenhouse-style forms (checks the Submit button is never pressed).
