"""The run started by "FIND AND PREPARE ALL MATCHING JOB APPLICATIONS".

    python -m core.runner --candidate <id> --run <run_id> [--search-only] [--login-only]

SEARCH -> filter -> read full description -> match score -> remove duplicates / already applied
-> open application -> fill standard details -> upload CV -> write answers -> STOP
-> you review and click Submit -> the tracker records it.

A real browser window opens (one profile per candidate, so logins are remembered).
Prepared applications stay open as tabs; more are prepared as you finish them.
"""
import argparse
import asyncio
import json
import random
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import config as cfg, db                      # noqa: E402
from core.matcher import score_job                     # noqa: E402
from core.filters import parse_salary_inr              # noqa: E402
from core.safety import AUTO_SUBMIT_ENABLED            # noqa: E402

assert AUTO_SUBMIT_ENABLED is False


_STOP_CACHE = {}


def is_stopped(run_id):
    """Stop requested from the dashboard (checked in the database at most every 5 seconds)."""
    t, v = _STOP_CACHE.get(run_id, (0, False))
    if not v and time.time() - t > 5:
        try:
            v = db.stop_requested(run_id)
        except Exception:
            v = False
        _STOP_CACHE[run_id] = (time.time(), v)
    return v


def save_prepared(job_id, data):
    db.kv_set(f"prepared:{job_id}", data)


def load_prepared(job_id):
    return db.kv_get(f"prepared:{job_id}", {}) or {}


def build_queries(prefs, profile):
    yrs = profile.get("total_experience_years")
    kw = prefs.get("search_keywords", {})
    fresher = yrs in (None, "") or float(yrs) < 1
    q = list(kw.get("fresher" if fresher else "experienced", []))
    if not fresher and float(yrs) < 2:
        q += [k for k in kw.get("fresher", [])[:3] if k not in q]
    return q


async def launch_browser(pw, cid, log):
    udir = cfg.DATA_DIR / "browser" / cid
    udir.mkdir(parents=True, exist_ok=True)
    import os
    headless = os.environ.get("JAA_HEADLESS") == "1"           # only used by the automated tests
    opts = dict(user_data_dir=str(udir), headless=headless, viewport=None if not headless else {"width": 1300, "height": 900},
                args=[] if headless else ["--start-maximized"], accept_downloads=False)
    for channel in (("chrome", "msedge", None) if not headless else (None,)):
        try:
            ctx = await pw.chromium.launch_persistent_context(**({**opts, "channel": channel} if channel else opts))
            log(f"Browser opened ({channel or 'built-in Chromium'}). Keep this window open while the run works.")
            try:                                     # a permanent first tab, so closing other tabs never closes the browser
                first = ctx.pages[0] if ctx.pages else await ctx.new_page()
                await first.goto(CONTROL_PAGE)
            except Exception:
                pass
            return ctx
        except Exception as e:
            last = e
    raise RuntimeError(f"Could not open a browser: {last}")


def load_login_status(cid):
    try:
        return db.kv_get(f"logins:{cid}", {}) or {}
    except Exception:
        return {}


async def login_all(ctx, adapters, cid, log, minutes=4):
    """Check every platform that needs a login; open ALL missing login pages at once as tabs and wait
    while the user logs in to them (in any order). The tool never types or stores passwords."""
    from core.form_filler import show_banner
    status, waiting = {}, {}
    for a in adapters:
        if not getattr(a, "login_url", None):
            continue
        page = await ctx.new_page()
        try:
            ok = await a._logged_in(page)
        except Exception:
            ok = False
        if ok:
            status[a.name] = True
            await page.close()
            continue
        try:
            await page.goto(a.login_url, wait_until="domcontentloaded")
        except Exception:
            pass
        await show_banner(page, f"Job Automator: log in to {a.name} for this candidate.\n"
                                "Quickest: 'Continue with Google' if the candidate uses Gmail.\n"
                                "Once done, this tab closes by itself. Passwords are not saved by the tool.")
        waiting[a.name] = (a, page)
    if waiting:
        log(f"Log in needed: {', '.join(waiting)}. All login pages are open as tabs in the browser - log in to each "
            f"(any order). Waiting up to {minutes} minutes...", "WARN")
        for _ in range(minutes * 12):
            if not waiting:
                break
            await asyncio.sleep(5)
            if browser_closed(ctx):
                log("The browser window was closed before the logins were finished. Start 'Log in to job sites' "
                    "again and log in inside the tabs - please do not close the browser.", "WARN")
                break
            for name, (a, page) in list(waiting.items()):
                try:
                    if page.is_closed():
                        waiting.pop(name)
                        log(f"{name}: the login tab was closed before logging in.", "WARN")
                        continue
                    if a.login_done(page.url):
                        await asyncio.sleep(2)
                        if a.login_done(page.url):
                            status[name] = True
                            waiting.pop(name)
                            log(f"{name}: logged in.")
                            await page.close()
                except Exception:
                    waiting.pop(name, None)
        for name, (a, page) in waiting.items():
            status[name] = False
            try:
                await page.close()
            except Exception:
                pass
    saved = load_login_status(cid)
    for k, v in status.items():
        saved[k] = {"logged_in": v, "checked": db.now()}
    db.kv_set(f"logins:{cid}", saved)
    return status


def browser_closed(ctx):
    try:
        return not ctx.pages
    except Exception:
        return True


CONTROL_PAGE = """data:text/html,<html><body style='font:16px Segoe UI,Arial;padding:40px;background:%231F3864;color:white'>
<h2>Job Application Automator</h2><p>This window is used by the tool. <b>Keep it open</b> while a run is working.</p>
<p>Log in / review in the other tabs. Close this window only when the run is finished.</p></body></html>"""


def adapters_for(prefs):
    from adapters.job_boards import BOARDS
    from adapters.ats import ATS
    india = prefs["markets"]["India"]
    on = [k for k, v in india["platforms"].items() if v]
    # career sites first (fast, API-based), then job boards
    order = ["Greenhouse", "Lever", "Workday", "LinkedIn", "Naukri", "Indeed India", "Foundit"]
    allp = {**ATS, **BOARDS}
    return [allp[k] for k in order if k in on and k in allp]


class _NoBrowser:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *a):
        return False


async def main(cid, run_id, search_only=False, login_only=False, only_api=False):
    prefs, taxonomy = cfg.load_preferences(), cfg.load_taxonomy()
    profile = cfg.load_profile(cid)
    _cv = cfg.resume_local_path(cid, profile.get("active_resume"))
    cv_path = str(_cv) if _cv else None

    def log(msg, level="INFO"):
        db.log(msg, level, run_id)
        print(f"[{level}] {msg}", flush=True)

    def stopped():
        return is_stopped(run_id)

    def seen(url):
        with db.connect() as con:
            return con.execute("SELECT 1 FROM jobs WHERE candidate_id=? AND canonical_url=? LIMIT 1",
                               (cid, db.canonical_url(url))).fetchone() is not None

    env = dict(prefs=prefs, profile=profile, taxonomy=taxonomy, cv_path=cv_path, log=log, seen=seen,
               stopped=stopped, days=prefs["posting_age"]["reject_older_than_days"],
               max_pages=int(prefs["run"].get("max_pages_per_search") or 3))
    adapters = adapters_for(prefs)
    if only_api:                                    # online (cloud) run: career-site feeds only, no browser
        adapters = [a for a in adapters if not a.needs_browser]
        search_only = True
    queries = build_queries(prefs, profile)
    log(f"Candidate: {profile['personal'].get('full_name') or cid} | experience: {profile.get('total_experience_years')} yrs "
        f"| platforms: {', '.join(a.name for a in adapters)} | searches: {', '.join(queries)}")
    counts = dict(found=0, approved=0, duplicate=0, not_eligible=0, review=0, prepared=0, needs=0, submitted=0)

    need_browser = any(a.needs_browser for a in adapters) or not search_only
    if need_browser:
        from playwright.async_api import async_playwright
        pw_cm = async_playwright()
    else:
        pw_cm = _NoBrowser()
    async with pw_cm as pw:
        ctx = await launch_browser(pw, cid, log) if need_browser else None

        # ---------------- logins: all login pages open together, the user logs in ----------------
        status = await login_all(ctx, adapters, cid, log, minutes=15 if login_only else 4) if ctx else {}
        ready = []
        for a in adapters:
            if stopped():
                break
            if getattr(a, "login_url", None) and not status.get(a.name):
                log(f"{a.name}: skipped this run (not logged in). Use 'Log in to job sites' on the dashboard.", "WARN")
                continue
            if a.needs_browser and ctx is not None and browser_closed(ctx):
                log("Browser was closed - stopping this run.", "WARN")
                break
            try:
                if a.needs_browser and not getattr(a, "login_url", None) and not await a.ensure_ready(ctx, log):
                    continue
                ready.append(a)
            except Exception as e:
                log(f"{a.name}: could not start ({type(e).__name__}: {e})", "WARN")
        if login_only:
            log("Login check finished. The browser will close now.")
            if ctx:
                await ctx.close()
            return counts

        # ---------------- search + score + dedupe ----------------
        queue = []
        for a in ready:
            if stopped():
                break
            log(f"Searching {a.name} ...")
            try:
                jobs = await a.search(ctx, queries, env)
            except Exception as e:
                log(f"{a.name}: search failed ({type(e).__name__}: {e})", "WARN")
                continue
            log(f"{a.name}: {len(jobs)} new jobs read")
            for j in jobs:
                r = score_job(j, profile, prefs, taxonomy)
                lo, hi = parse_salary_inr(j.get("salary_text", ""))
                row = dict(candidate_id=cid, company=j.get("company", ""), title=j.get("title", ""),
                           location=j.get("location", ""), market="India", salary_text=j.get("salary_text", ""),
                           salary_min_annual=lo, salary_max_annual=hi, currency="INR", job_url=j.get("job_url", ""),
                           source_platform=a.name, requisition_id=str(j.get("requisition_id", "")),
                           date_posted=str(j.get("posted", "")), sponsorship_status="N/A (India)",
                           match_score=r["score"], match_reasons="\n".join(r["reasons"]),
                           resume_used=profile.get("active_resume"), status=r["status"],
                           notes=r["reject_reason"] or (r["tier"] or ""), description=(j.get("description") or "")[:20000])
                jid, dup, prev = db.add_job(row)
                counts["found"] += 1
                if dup:
                    counts["duplicate"] += 1
                    continue
                if r["status"] == "Not Eligible":
                    counts["not_eligible"] += 1
                elif r["status"] == "Under Review":
                    counts["review"] += 1
                else:
                    counts["approved"] += 1
                    j.update(id=jid, score=r["score"], tier=r["tier"] or "")
                    queue.append((a, j))
        tier_rank = {t["name"]: i for i, t in enumerate(prefs["posting_age"]["tiers"])}
        queue.sort(key=lambda x: (tier_rank.get(x[1].get("tier"), 9), -x[1]["score"]))
        log(f"Search done: {counts['found']} jobs | {counts['approved']} match | {counts['review']} below score "
            f"| {counts['not_eligible']} not eligible | {counts['duplicate']} duplicates")

        if search_only or not queue:
            if ctx:
                await ctx.close()
            return counts

        # ---------------- prepare applications, keep them open for review ----------------
        cap = int(prefs["run"].get("review_tabs_open_at_once") or 15)
        open_tabs = {}           # page -> (adapter, job)
        announced = False
        lo_s, hi_s = prefs["run"].get("slow_mode_seconds", [2, 5])
        log(f"Preparing {len(queue)} applications ({cap} open at a time). Nothing is submitted - you review each tab.")
        while (queue or open_tabs) and not stopped():
            if not ctx.pages and not queue:
                break
            # finished tabs
            for page, (a, j) in list(open_tabs.items()):
                try:
                    if page.is_closed():
                        open_tabs.pop(page)
                        continue
                    if await a.is_submitted(page):
                        db.update_job(j["id"], status="Submitted", notes="Submitted by you in the browser.")
                        counts["submitted"] += 1
                        log(f"Submitted by you: {j['title']} @ {j['company']}")
                        open_tabs.pop(page)
                        continue
                    await a.assist(page, j, env)
                except Exception:
                    open_tabs.pop(page, None)
            # prepare more
            while queue and len(open_tabs) < cap and not stopped():
                a, j = queue.pop(0)
                try:
                    page = await ctx.new_page()
                except Exception:
                    log("Browser was closed - stopping.", "WARN")
                    queue.clear()
                    break
                log(f"Preparing [{j['score']}] {j['title']} @ {j['company']} ({a.name})")
                try:
                    res = await a.prepare(page, j, env)
                except Exception as e:
                    res = {"state": "stuck", "needs": [f"Error while filling: {type(e).__name__}"], "notes": {}}
                    log(traceback.format_exc(limit=2), "WARN")
                show_page = j.pop("external_page", None) or page
                state, needs = res.get("state"), res.get("needs", [])
                save_prepared(j["id"], {"state": state, "needs": needs, "answers": res.get("notes", {}).get("answers", []),
                                        "left_blank": res.get("notes", {}).get("left_blank", []),
                                        "cv_uploaded": res.get("notes", {}).get("cv_uploaded", False),
                                        "submit_button": res.get("submit_button")})
                if state == "closed":
                    db.update_job(j["id"], status="Not Eligible", notes="No longer accepting applications.")
                    await page.close()
                    continue
                if state in ("ready", "ready_open"):
                    note = ("Filled - check it and click '" + (res.get("submit_button") or "Submit") + "' yourself."
                            if state == "ready" else needs[0])
                    db.update_job(j["id"], status="Awaiting My Review", date_prepared=db.now(), notes=note)
                    counts["prepared"] += 1
                else:
                    db.update_job(j["id"], status="Awaiting My Review" if state == "needs_input" else "Application Started",
                                  date_prepared=db.now(), notes="Needs you: " + "; ".join(needs)[:900])
                    counts["needs"] += 1
                from core.form_filler import show_banner
                if state != "ready_open":
                    await show_banner(show_page, f"Job Automator - prepared for your review\n{j['title']} @ {j['company']}  "
                                                 f"(match {j['score']})\n"
                                      + ("Needs you: " + "; ".join(needs)[:300] + "\n" if needs else "")
                                      + "Check every answer, then click Submit yourself.")
                open_tabs[show_page] = (a, j)
                await asyncio.sleep(random.uniform(lo_s, hi_s))
            if not queue and not announced:
                announced = True
                log(f"All {counts['prepared'] + counts['needs']} applications are prepared. Review the open tabs and "
                    "click Submit on the ones you want. Close a tab to skip it; close the browser when you are done.")
            await asyncio.sleep(4)
            try:
                _ = ctx.pages
            except Exception:
                break
        try:
            await ctx.close()
        except Exception:
            pass
    return counts


def run(cid, run_id, search_only=False, login_only=False, only_api=False):
    db.init_db()
    try:
        counts = asyncio.run(main(cid, run_id, search_only, login_only, only_api))
        summary = ", ".join(f"{k}: {v}" for k, v in counts.items())
        db.finish_run(run_id, "stopped" if db.stop_requested(run_id) else "finished", summary)
        db.log(f"Run finished - {summary}", "INFO", run_id)
    except Exception as e:
        db.log(f"Run failed: {type(e).__name__}: {e}", "ERROR", run_id)
        db.log(traceback.format_exc()[-1500:], "ERROR", run_id)
        db.finish_run(run_id, "failed", str(e)[:300])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--run", type=int, required=True)
    ap.add_argument("--search-only", action="store_true")
    ap.add_argument("--login-only", action="store_true")
    a = ap.parse_args()
    run(a.candidate, a.run, a.search_only, a.login_only)
