"""Job Application Automator - dashboard.

Run:  streamlit run app.py
"""
from pathlib import Path

import pandas as pd
import streamlit as st

from core import config as cfg
from core import db
from core.cv_template import ensure_template
from core.resume_parser import build_draft_profile
from core.safety import AUTO_SUBMIT_ENABLED

st.set_page_config(page_title="Job Application Automator", page_icon="💼", layout="wide")
try:
    db.init_db()
except Exception as _e:
    st.title("Job Application Automator")
    st.error("The database cannot be reached. If this is the online website, check the DATABASE_URL secret "
             "(Supabase 'Session pooler' connection string) in the app settings, and that the Supabase project is not "
             f"paused.\n\nDetails: {type(_e).__name__}")
    st.stop()
@st.cache_resource
def _one_time_import():
    cfg.import_old_files()          # brings in candidates saved by older versions (once)
    return True


_one_time_import()

CV_TYPES = (".pdf", ".docx", ".txt")
YN = ["Not answered", "Yes", "No"]


def _yn(v):
    return "Not answered" if v is None else ("Yes" if v else "No")


def _vy(s):
    return None if s == "Not answered" else s == "Yes"


def _num_or_none(txt):
    t = (txt or "").replace(",", "").strip()
    try:
        return float(t) if "." in t else int(t)
    except ValueError:
        return None


if "prefs" not in st.session_state:
    st.session_state.prefs = cfg.load_preferences()
prefs = st.session_state.prefs

# =============================== LOGIN ===============================
from core import auth  # noqa: E402

auth.init()


def login_screen():
    if st.session_state.pop("logged_out", False):
        _set_cookie("", 0)
    st.title("Job Application Automator")
    if not auth.any_admin():
        st.warning("No admin account yet. On your PC, double-click **create_admin.bat** (for the online website, put "
                   "cloud_settings.toml in the folder first - see README), then refresh this page.")
        st.stop()
    c, _ = st.columns([1, 2])
    with c.form("login"):
        st.subheader("Log in")
        u = st.text_input("Login ID")
        p = st.text_input("Password", type="password")
        if st.form_submit_button("Log in", type="primary"):
            user, msg = auth.authenticate(u, p)
            if user:
                st.session_state.user = user
                st.session_state.token = auth.new_session(user["username"])
                st.session_state.cookie_pending = True
                st.rerun()
            st.error(msg)
    st.caption("No account? Ask the admin to create one for you.")
    st.stop()


def change_password_screen(user, forced=False):
    with st.form("chpw"):
        st.subheader("Set a new password" if forced else "Change password")
        if forced:
            st.info("This is your first login - please choose your own password.")
        p1 = st.text_input("New password (min 8, letters + numbers)", type="password")
        p2 = st.text_input("Repeat new password", type="password")
        if st.form_submit_button("Save password", type="primary"):
            if p1 != p2:
                st.error("Passwords do not match.")
            else:
                try:
                    auth.set_password(user["username"], p1, must_change=False, keep_token=st.session_state.get("token"))
                    user["must_change"] = 0
                    st.success("Password changed.")
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))


import streamlit.components.v1 as _components  # noqa: E402

COOKIE = "jaa_session"


def _set_cookie(value, days):
    # value is a server-generated random token (letters, digits, - and _ only)
    html = f"<script>document.cookie='{COOKIE}={value}; path=/; max-age={int(days * 86400)}; SameSite=Lax';</script>"
    if hasattr(st, "iframe"):
        st.iframe(html, height=1)
    else:
        _components.html(html, height=0)


if "user" not in st.session_state:
    # stay logged in after a page refresh / phone tab reload (cookie holds a random session token)
    try:
        _tok = st.context.cookies.get(COOKIE)
    except Exception:
        _tok = None
    _u = auth.user_from_session(_tok)
    if _u:
        st.session_state.user, st.session_state.token = _u, _tok
    else:
        login_screen()
if st.session_state.pop("cookie_pending", False):
    _set_cookie(st.session_state.token, auth.SESSION_DAYS)
USER = st.session_state.user
# re-check on every page load: disabled / deleted accounts are signed out at once
_live = {u["username"]: u for u in auth.list_users()}.get(USER["username"])
if not _live or not _live["active"]:
    st.session_state.clear()
    st.rerun()
USER["role"] = _live["role"]
IS_ADMIN = USER["role"] == "admin"
if _live["must_change"]:
    st.title("Job Application Automator")
    change_password_screen(USER, forced=True)
    st.stop()

# =============================== SIDEBAR: CANDIDATES ===============================
with st.sidebar:
    st.markdown(f"Logged in as **{USER['username']}**" + (" (admin)" if IS_ADMIN else ""))
    if st.button("Log out", key="logout"):
        auth.end_session(st.session_state.get("token"))
        st.session_state.clear()
        st.session_state.logged_out = True
        st.rerun()
    st.header("Candidate")
    cands = cfg.list_candidates(None if IS_ADMIN else USER["username"])
    active = cfg.get_active(USER["username"], cands)
    if cands:
        ids = [c["id"] for c in cands]
        names = {c["id"]: c["name"] + (f"  ({c['owner'] or 'admin'})" if IS_ADMIN else "") for c in cands}
        sel = st.selectbox("Applying for", ids, index=ids.index(active) if active in ids else 0,
                           format_func=lambda i: names[i])
        if sel != active:
            cfg.set_active(sel, USER["username"])
            active = sel
    else:
        st.info("No candidate yet. Add the first one below.")
    with st.form("new_cand", clear_on_submit=True):
        new_name = st.text_input("Add new candidate (name)")
        if st.form_submit_button("Add candidate") and new_name.strip():
            active = cfg.create_candidate(new_name.strip(), USER["username"])
            st.session_state.pop("profile_cid", None)
            st.rerun()
    st.divider()
    st.caption("Each candidate has a separate CV, profile and tracker history. Files stay on this computer.")
    st.caption("USA market: on hold. India: " + ", ".join(prefs["markets"]["India"]["preferred_locations"]))

# load the active candidate's profile into session (reload when candidate changes)
if active and st.session_state.get("profile_cid") != active:
    st.session_state.profile = cfg.load_profile(active)
    st.session_state.profile_cid = active
    st.session_state.pop("resume_text", None)
profile = st.session_state.get("profile") if active else None
# widget-key prefixes: U for the uploader area, K for the form. K changes every time a CV is read,
# so the browser draws brand-new fields holding the freshly read values.
U = f"{active}_"
K = f"{active}_v{st.session_state.get('form_ver', 0)}_"

st.title("Job Application Automator")
st.caption("Finds and prepares Audit / Accounting applications. It never submits - you review and click Submit yourself.")
# =============================== MY ACCOUNT / ADMIN ===============================
def render_account():
    st.markdown(f"**Login ID:** {USER['username']}  \n**Role:** {USER['role']}")
    change_password_screen(USER)


def render_admin():
    st.subheader("Create a new user")
    with st.form("new_user", clear_on_submit=True):
        a1, a2, a3 = st.columns([2, 2, 1])
        nu = a1.text_input("Login ID (letters / numbers / . _ -)")
        npw = a2.text_input("Temporary password (blank = generate one)", type="password")
        nrole = a3.selectbox("Role", ["user", "admin"])
        if st.form_submit_button("Create user", type="primary"):
            pw = npw or auth.generate_password()
            try:
                created = auth.create_user(nu, pw, nrole, must_change=True)
                st.session_state.new_cred = (created, pw)
                st.rerun()
            except ValueError as e:
                st.error(str(e))
    if st.session_state.get("new_cred"):
        u_, p_ = st.session_state.new_cred
        st.success("User created. Share these details privately - the password is shown only now. "
                   "The user must set their own password at first login.")
        st.code(f"Link: (your website link)\nLogin ID: {u_}\nPassword: {p_}")
        if st.button("Done, hide password"):
            st.session_state.pop("new_cred")
            st.rerun()

    st.subheader("Users")
    users = auth.list_users()
    counts = {}
    for c in cfg.list_candidates():
        counts[c["owner"]] = counts.get(c["owner"], 0) + 1
    st.dataframe(pd.DataFrame([dict(u, candidates=counts.get(u["username"], 0)) for u in users]),
                 width="stretch", hide_index=True)
    others = [u["username"] for u in users if u["username"] != USER["username"]]
    if others:
        st.subheader("Manage a user")
        who_u = st.selectbox("User", others)
        info = next(u for u in users if u["username"] == who_u)
        b1, b2, b3 = st.columns(3)
        if b1.button("Disable" if info["active"] else "Enable", key="tog"):
            auth.set_active(who_u, not info["active"]); st.rerun()
        if b2.button("Reset password", key="rst"):
            pw = auth.generate_password()
            auth.set_password(who_u, pw, must_change=True)
            st.session_state.new_cred = (who_u, pw)
            st.rerun()
        with b3.popover("Delete user"):
            st.warning(f"Delete '{who_u}'? Their candidates stay and move to the admin view.")
            if st.button("Yes, delete", key="del"):
                auth.delete_user(who_u); st.rerun()
    st.caption("Disabled or deleted users are signed out immediately. Candidates of a deleted user remain visible to admins.")


if not active:
    st.warning("Add a candidate in the left sidebar to begin.")
    _nt = st.tabs(["My account"] + (["Admin"] if IS_ADMIN else []))
    with _nt[0]:
        render_account()
    if IS_ADMIN:
        with _nt[1]:
            render_admin()
    st.stop()
st.markdown(f"Working for: **{profile['personal'].get('full_name') or profile.get('display_name') or active}**")

_tabs = ["Dashboard", "CV & Profile", "Preferences", "Application Tracker", "Logs", "My account"] + (["Admin"] if IS_ADMIN else [])
_t = st.tabs(_tabs)
tab_dash, tab_profile, tab_prefs, tab_tracker, tab_logs, tab_account = _t[:6]
tab_admin = _t[6] if IS_ADMIN else None


# =============================== RUN HELPERS ===============================
import threading
import time as _time


def worker_status():
    """The PC worker writes a heartbeat every 20 s. Returns (online, info)."""
    hb = db.kv_get("worker:heartbeat") or {}
    online = bool(hb) and _time.time() - float(hb.get("ts", 0)) < 75
    return online, hb


def _age_min(ts):
    from datetime import datetime
    try:
        return (datetime.now() - datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")).total_seconds() / 60
    except Exception:
        return 0


def active_run(cid):
    online, _ = worker_status()
    for r in db.list_runs(30):
        if r.get("candidate_id") != cid or r.get("status") not in ("queued", "running"):
            continue
        stale = (r["status"] == "running" and ((r.get("kind") == "online-search" and _age_min(r["started_at"]) > 20)
                                               or (r.get("kind") != "online-search" and not online)))
        if stale:                                    # the PC worker was closed in the middle of this run
            db.finish_run(r["id"], "interrupted", "The worker stopped before this run finished.")
            continue
        return r
    return None


def queue_run(cid, kind):
    markets = [k for k, m in prefs["markets"].items() if m["enabled"] and not m.get("on_hold")]
    plats = sorted({p for k in markets for p, v in prefs["markets"][k]["platforms"].items() if v})
    run_id = db.start_run(cid, markets, plats, profile.get("active_resume"), kind=kind,
                          requested_by=USER["username"], status="queued")
    db.log(f"Run #{run_id} requested by {USER['username']} ({kind}). Waiting for the PC worker...", run_id=run_id)
    return run_id


def start_online_search(cid):
    """Career-site search (Greenhouse / Lever / Workday feeds) runs right here - no PC needed."""
    from core import runner
    run_id = db.start_run(cid, ["India"], ["Greenhouse", "Lever", "Workday"], profile.get("active_resume"),
                          kind="online-search", requested_by=USER["username"], status="running")
    threading.Thread(target=runner.run, args=(cid, run_id), kwargs={"only_api": True}, daemon=True).start()
    return run_id


@st.fragment(run_every=5)
def progress_panel(cid):
    run = next((r for r in db.list_runs(30) if r.get("candidate_id") == cid), None)
    if not run:
        return
    with st.container(border=True):
        a, b = st.columns([4, 1])
        state = run["status"]
        label = {"queued": "waiting for the PC worker", "running": "running"}.get(state, state)
        a.markdown(f"**Run #{run['id']}** ({run.get('kind') or 'find'}) - {label}"
                   + (f" - {run['summary']}" if run.get("summary") else ""))
        if state in ("queued", "running") and b.button("Stop run", key=f"stop_{run['id']}"):
            db.request_stop(run["id"])
            st.toast("Stopping after the current step...")
        for r in db.run_logs(run["id"], 12):
            icon = {"WARN": "⚠️", "ERROR": "❌"}.get(r["level"], "•")
            st.markdown(f"<small>{r['ts'][11:]} {icon} {r['message']}</small>", unsafe_allow_html=True)


def review_queue(cid, jobs):
    todo = [j for j in jobs if j["status"] in ("Awaiting My Review", "Application Started")]
    st.subheader(f"Your review queue ({len(todo)})")
    if not todo:
        st.caption("Prepared applications appear here and stay open as tabs in the browser window.")
        return
    st.caption("Each one is open in the browser window on the PC worker. Check it and click Submit there yourself "
               "(e.g. via Chrome Remote Desktop from your phone) - it is marked Submitted automatically. Or mark it here.")
    for j in todo[:200]:
        info = db.kv_get(f"prepared:{j['id']}", {}) or {}
        head = f"{j['match_score'] or ''} | {j['title']} @ {j['company']} - {j['source_platform']}"
        with st.expander(head):
            st.markdown(f"[Open job]({j['job_url']})  \n**Status:** {j['status']}  \n**Note:** {j['notes'] or ''}")
            if info.get("answers"):
                st.markdown("**Answers filled in**")
                st.dataframe(pd.DataFrame(info["answers"])[["q", "a", "source"]].rename(
                    columns={"q": "Question", "a": "Answer", "source": "From"}), hide_index=True, width="stretch")
            if info.get("left_blank"):
                st.caption("Left blank (optional): " + "; ".join(info["left_blank"]))
            with st.popover("Why this match score"):
                st.text(j.get("match_reasons") or "")
            c1, c2 = st.columns(2)
            if c1.button("I submitted it", key=f"sub_{j['id']}"):
                db.update_job(j["id"], status="Submitted"); st.rerun()
            if c2.button("Skip this job", key=f"rej_{j['id']}"):
                db.update_job(j["id"], status="Rejected", notes="Skipped by you."); st.rerun()


# =============================== DASHBOARD ===============================
with tab_dash:
    c1, c2 = st.columns([2, 3])
    with c1:
        st.subheader("Setup")
        st.markdown(f"**CV:** {profile.get('active_resume') or ':red[not uploaded]'}")
        exp = profile.get("total_experience_years")
        st.markdown("**Experience:** " + ("not set" if exp in (None, "") else ("Fresher" if float(exp) == 0 else f"{exp} years")))
        st.markdown(f"**Target:** {prefs.get('target_field')}")
        min_ctc = cfg.candidate_min_ctc(profile, prefs)
        for mk, m in prefs["markets"].items():
            if m.get("on_hold") or not m["enabled"]:
                st.markdown(f"**{mk}** - :grey[on hold]")
                continue
            locs = ", ".join(m["preferred_locations"]) or ":orange[location not set]"
            plats = ", ".join(k for k, v in m["platforms"].items() if v) or "none"
            sal = f"Min CTC Rs {min_ctc:,.0f} per year" if (mk == "India" and min_ctc) else ""
            st.markdown(f"**{mk}** - {locs}  \n{sal}  \nPlatforms: {plats}")
    with c2:
        st.subheader("Readiness check")
        gaps = cfg.profile_gaps(profile)
        ans_gaps = cfg.application_answer_gaps(profile, prefs)
        any_platform = any(v for m in prefs["markets"].values() if m["enabled"] for v in m["platforms"].values())
        checks = [
            ("CV uploaded", bool(profile.get("active_resume")), True),
            ("Profile has no missing basics", not gaps, True),
            ("Profile confirmed", bool(profile.get("confirmed")), True),
            ("At least one platform selected", any_platform, True),
            ("Form answers saved (notice period, expected CTC ...)", not ans_gaps, False),
        ]
        for label, ok, _ in checks:
            st.markdown(("✅ " if ok else "⚠️ ") + label)
        if gaps:
            st.caption("Missing in profile: " + ", ".join(gaps))
        if ans_gaps:
            st.caption("Not answered yet (the automation will stop on these questions): " + ", ".join(ans_gaps))

    st.divider()
    running = active_run(active)
    w_online, w_info = worker_status()
    if w_online:
        st.success(f"PC worker online ({w_info.get('host', '')}) - LinkedIn / Naukri / Indeed / Foundit runs will start there.",
                   icon="🖥️")
    else:
        st.warning("PC worker is offline - runs you start now wait until run_worker.bat is started on the PC "
                   "(they start automatically then). Career-site search below works without the PC.", icon="🖥️")
    o1, o2 = st.columns([3, 1])
    search_only = o2.checkbox("Search only (no forms)", key=U + "search_only",
                              help="Finds, scores and records jobs without opening any application.")
    go = o1.button("FIND AND PREPARE ALL MATCHING JOB APPLICATIONS", type="primary", width="stretch",
                   disabled=bool(running))
    st.caption("Final submission is always left to you. Auto-submit: " + ("ON" if AUTO_SUBMIT_ENABLED else "OFF (locked)")
               + " | The job-site browser opens on the PC running the worker - review and Submit there.")
    l1, l2 = st.columns([1, 3])
    login = l1.button("Log in to job sites (first time)", disabled=bool(running), key=U + "login")
    from core.runner import load_login_status
    ls = load_login_status(active)
    india_on = [k for k, v in prefs["markets"]["India"]["platforms"].items() if v and k in ("LinkedIn", "Naukri", "Foundit")]
    chips = []
    for k in india_on:
        v = ls.get(k)
        chips.append(f"{'✅' if v and v.get('logged_in') else '⚠️'} {k}"
                     + (f" <small>({v['checked'][:10]})</small>" if v and v.get("logged_in") else " <small>(not logged in)</small>"))
    l2.markdown(" &nbsp;&nbsp; ".join(chips) + "<br><small>Opens all login pages together on the PC worker - log in to each "
                "once per candidate. Logins stay saved (usually for weeks).</small>", unsafe_allow_html=True)
    has_pages = bool(prefs.get("company_career_pages"))
    online_btn = st.button("Search company career pages now (online, no PC needed)", disabled=bool(running) or not has_pages,
                           help="Greenhouse / Lever / Workday pages listed in Preferences. Search + match score only.")
    if not has_pages:
        st.caption("Add company career pages in Preferences to use the online search.")
    if go or login or online_btn:
        blocking = [l for l, ok, req in checks if req and not ok] if (go or online_btn) else []
        if blocking:
            st.error("Cannot start yet: " + "; ".join(blocking))
        else:
            if online_btn:
                run_id = start_online_search(active)
            else:
                run_id = queue_run(active, "login" if login else ("search" if search_only else "find"))
            if ans_gaps and go:
                db.log("Not answered in profile: " + ", ".join(ans_gaps) + " - such questions will be left for you.",
                       "WARN", run_id)
            st.rerun()

    progress_panel(active)

    st.subheader("Summary")
    jobs = db.list_jobs(candidate_id=active)
    cols = st.columns(6)
    for col, s in zip(cols, ["Found", "Match Approved", "Under Review", "Awaiting My Review", "Submitted", "Not Eligible"]):
        col.metric(s, sum(1 for j in jobs if j["status"] == s))
    review_queue(active, jobs)


# =============================== CV & PROFILE ===============================


def read_cv_into_profile(path: Path):
    """Read a CV, fill the profile, save it as an unconfirmed draft and refresh the form."""
    global profile
    old = profile
    draft, text, _ = build_draft_profile(path, cfg.blank_profile(), cfg.load_taxonomy())
    draft["display_name"] = old.get("display_name", "")
    draft["owner"] = old.get("owner", "")
    if draft.get("cv_source") != "template":
        # a free-format CV cannot contain these answers - keep what was saved before
        for keep in ("work_authorization", "india_application_fields"):
            draft[keep] = old.get(keep, draft[keep])
        if draft.get("total_experience_years") is None:
            draft["total_experience_years"] = old.get("total_experience_years")
    draft["confirmed"] = False
    cfg.save_profile(active, draft)                      # saved at once - survives a page refresh
    st.session_state.profile = profile = draft
    st.session_state.resume_text = text
    st.session_state.cv_read_msg = path.name
    for k in list(st.session_state.keys()):              # drop the old form's widget state
        if isinstance(k, str) and k.startswith(K):
            del st.session_state[k]
    st.session_state.form_ver = st.session_state.get("form_ver", 0) + 1


with tab_profile:
    st.subheader("1. Upload CV")
    up_col, tpl_col = st.columns([3, 1])
    with tpl_col:
        st.markdown("**Standard CV template**")
        tpl = ensure_template()
        st.download_button("Download CV template", tpl.read_bytes(), file_name="CV_Template.docx",
                           mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                           width="stretch", key=U + "dl_tpl")
        st.caption("Fill any candidate's details in this Word file and upload it. It is read exactly, field by field.")
    with up_col:
        ups = st.file_uploader("Upload CV - filled template (.docx, best) or any CV (PDF / DOCX / TXT)",
                               type=["pdf", "docx", "txt"], accept_multiple_files=True, key=U + "up")
        if ups:
            seen = st.session_state.setdefault("seen_uploads", set())
            fresh = [u for u in ups if (active, u.name, u.size) not in seen]
            for u in fresh:
                cfg.save_resume(active, u.name, bytes(u.getbuffer()))
                seen.add((active, u.name, u.size))
            if fresh:                                    # read the newly uploaded CV straight away
                read_cv_into_profile(cfg.resume_local_path(active, Path(fresh[-1].name).name))
                st.rerun()

    resumes = sorted(n for n in cfg.list_resumes(active) if Path(n).suffix.lower() in CV_TYPES)
    if resumes:
        idx = resumes.index(profile["active_resume"]) if profile.get("active_resume") in resumes else 0
        chosen = st.selectbox("Active CV for this candidate", resumes, index=idx, key=U + "cvsel")
        if st.button("Read this CV again", key=U + "read"):
            read_cv_into_profile(cfg.resume_local_path(active, chosen))
            st.rerun()
        if st.session_state.pop("cv_read_msg", None):
            st.toast("CV read - profile filled and saved as draft.")
        if profile.get("cv_source"):
            if profile["cv_source"] == "template":
                st.success("Read from the standard template - all fields taken exactly as written. Please check and confirm.")
            else:
                st.warning("Free-format CV: details were read as well as possible. Fields the CV does not state "
                           "(designation, CTC, notice period ...) are left blank - please check every field, "
                           "or use the template for an exact read.")
        if st.session_state.get("resume_text"):
            with st.expander("Text read from the CV (for checking)"):
                st.text(st.session_state.resume_text[:15000])
    else:
        st.warning("No CV uploaded for this candidate yet.")

    st.subheader("2. Check and complete the profile")
    st.caption("Nothing here is guessed. Empty fields stay empty; the automation stops on any question it cannot answer from this profile.")
    p = profile["personal"]
    a, b = st.columns(2)
    p["full_name"] = a.text_input("Full name", p.get("full_name", ""), key=K + "name")
    p["email"] = b.text_input("Email", p.get("email", ""), key=K + "email")
    p["phone"] = a.text_input("Mobile", p.get("phone", ""), key=K + "phone")
    p["current_city"] = b.text_input("Current city", p.get("current_city", ""), key=K + "city")
    p["linkedin_url"] = a.text_input("LinkedIn URL (optional)", p.get("linkedin_url", ""), key=K + "li")
    te = profile.get("total_experience_years")
    te_txt = b.text_input("Total experience in years (0 = fresher, blank = not answered)",
                          "" if te in (None, "") else str(te), key=K + "exp_yrs")
    profile["total_experience_years"] = _num_or_none(te_txt)
    profile["summary"] = st.text_area("Profile summary", profile.get("summary", ""), height=80, key=K + "summary")

    st.markdown("**Education**")
    edu_df = pd.DataFrame(profile.get("education") or [],
                          columns=["degree", "specialisation", "institution", "year", "score"])
    edu_df = st.data_editor(edu_df, num_rows="dynamic", width="stretch", key=K + "edu")
    profile["education"] = [r for r in edu_df.fillna("").to_dict("records") if r.get("degree")]

    st.markdown("**Professional qualifications** (CA, CA Inter, CMA, CS, CFA, ACCA, US CPA, CIA ...)")
    st.caption("Status in the candidate's own words, e.g. Qualified / Inter both groups cleared / Level 1 cleared / Pursuing.")
    q_df = pd.DataFrame(profile.get("qualifications") or [], columns=["name", "status", "details", "year"])
    q_df = st.data_editor(q_df, num_rows="dynamic", width="stretch", key=K + "quals")
    profile["qualifications"] = [r for r in q_df.fillna("").to_dict("records") if r.get("name")]

    st.markdown("**Work experience / articleship / internship** (leave empty for a fresher with none)")
    if profile.get("experience_section_text"):
        with st.expander("Experience section as read from the CV"):
            st.text(profile["experience_section_text"])
    ex_df = pd.DataFrame(profile.get("experience") or [],
                         columns=["organisation", "designation", "from", "to", "responsibilities"])
    ex_df = st.data_editor(ex_df, num_rows="dynamic", width="stretch", key=K + "exp")
    profile["experience"] = [r for r in ex_df.fillna("").to_dict("records") if r.get("organisation")]

    s1, s2 = st.columns(2)
    skills = s1.text_area("Skills (one per line)", "\n".join(profile.get("skills", [])), key=K + "skills")
    profile["skills"] = [s.strip() for s in skills.splitlines() if s.strip()]
    soft = s2.text_area("Software / tools (one per line) - Tally, Excel, SAP ...",
                        "\n".join(profile.get("software", [])), key=K + "soft")
    profile["software"] = [s.strip() for s in soft.splitlines() if s.strip()]

    st.subheader("3. Answers the automation must never guess")
    wa = profile.setdefault("work_authorization", {})
    ind = profile.setdefault("india_application_fields", {})
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**India job forms**")
        wa.setdefault("India", {})["authorized_to_work_in_india"] = _vy(st.selectbox(
            "Authorised to work in India?", YN, YN.index(_yn(wa["India"].get("authorized_to_work_in_india"))),
            key=K + "auth_in"))
        np_txt = st.text_input("Notice period in days (0 = immediate joiner, blank = not answered)",
                               "" if ind.get("notice_period_days") is None else str(ind["notice_period_days"]),
                               key=K + "notice")
        ind["notice_period_days"] = _num_or_none(np_txt)
        ind["willing_to_relocate"] = _vy(st.selectbox(
            "Willing to relocate?", YN, YN.index(_yn(ind.get("willing_to_relocate"))), key=K + "reloc"))
        ind["preferred_locations"] = st.text_input("Preferred locations", ind.get("preferred_locations") or "",
                                                   key=K + "ploc")
    with c2:
        st.markdown("**CTC (Rs per year)**")
        for key, label in [("current_ctc_annual_inr", "Current CTC (0 for fresher, blank = not answered)"),
                           ("expected_ctc_annual_inr", "Expected CTC (blank = not answered)"),
                           ("minimum_ctc_annual_inr",
                            f"Minimum acceptable CTC (blank = use default Rs {prefs['markets']['India']['salary_min_annual']:,})")]:
            v = ind.get(key)
            ind[key] = _num_or_none(st.text_input(label, "" if v in (None, "") else str(v), key=K + key))
    us_on = prefs["markets"]["USA"]["enabled"] and not prefs["markets"]["USA"].get("on_hold")
    if us_on:
        us = wa.setdefault("USA", {})
        st.markdown("**USA**")
        us["authorized_to_work_without_sponsorship"] = _vy(st.selectbox(
            "Authorised to work in the US without sponsorship?", YN,
            YN.index(_yn(us.get("authorized_to_work_without_sponsorship"))), key=K + "us1"))
        us["require_sponsorship_now_or_future"] = _vy(st.selectbox(
            "Will you now or in future require visa sponsorship?", YN,
            YN.index(_yn(us.get("require_sponsorship_now_or_future"))), key=K + "us2"))
    else:
        st.caption("USA questions are hidden while the USA market is on hold.")

    st.subheader("4. Writing style for generated answers")
    profile["answer_style_sample"] = st.text_area("Example answer in the candidate's voice (style guide)",
                                                  profile.get("answer_style_sample", ""), height=90, key=K + "style")

    c1, c2 = st.columns(2)
    if c1.button("Save draft", key=K + "save"):
        profile["confirmed"] = False
        cfg.save_profile(active, profile)
        st.success("Draft saved (not yet confirmed).")
    if c2.button("Confirm profile - this is accurate", type="primary", key=K + "confirm"):
        missing = cfg.profile_gaps(profile)
        if missing:
            st.error("Please fill first: " + ", ".join(missing))
        else:
            profile["confirmed"] = True
            cfg.save_profile(active, profile)
            st.success("Profile confirmed and saved.")


# =============================== PREFERENCES ===============================
with tab_prefs:
    st.caption("These settings apply to every candidate. Each candidate's own experience decides which jobs fit them.")
    st.subheader("Markets, locations and platforms")
    for mk, m in prefs["markets"].items():
        title = f"{mk}" + (" - ON HOLD" if m.get("on_hold") else "")
        with st.expander(title, expanded=not m.get("on_hold")):
            if m.get("on_hold"):
                st.info("USA is on hold for now. India is being built first.")
                continue
            m["enabled"] = st.checkbox(f"Search in {mk}", m["enabled"], key=f"en_{mk}")
            locs = st.text_input("Preferred locations (comma separated)", ", ".join(m["preferred_locations"]), key=f"loc_{mk}")
            m["preferred_locations"] = [x.strip() for x in locs.split(",") if x.strip()]
            if m.get("location_aliases"):
                st.caption("Also counted as Mumbai: " + ", ".join(m["location_aliases"][1:]))
            m["consider_other_locations"] = st.checkbox("Also consider other locations", m["consider_other_locations"], key=f"oth_{mk}")
            smin = st.number_input("Default minimum CTC (Rs per year; 360000 = 3.6 LPA)", min_value=0,
                                   value=int(m["salary_min_annual"] or 0), step=10000, key=f"smin_{mk}")
            m["salary_min_annual"] = smin or None
            st.markdown("Platforms")
            cols = st.columns(4)
            for i, (pl, on) in enumerate(m["platforms"].items()):
                m["platforms"][pl] = cols[i % 4].checkbox(pl, on, key=f"pl_{mk}_{pl}")

    st.subheader("Job type")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown("Employment type")
        for k in prefs["employment_type"]:
            prefs["employment_type"][k] = st.checkbox(k, prefs["employment_type"][k], key=f"et_{k}")
    with c2:
        st.markdown("Experience levels allowed")
        for k in prefs["experience_levels"]:
            prefs["experience_levels"][k] = st.checkbox(k, prefs["experience_levels"][k], key=f"ex_{k}")
    with c3:
        st.markdown("Work arrangement")
        for k in prefs["work_arrangement"]:
            prefs["work_arrangement"][k] = st.checkbox(k, prefs["work_arrangement"][k], key=f"wa_{k}")

    st.subheader("Posting age (priority tiers)")
    pa = prefs["posting_age"]
    cols = st.columns(len(pa["tiers"]) + 1)
    for i, t in enumerate(pa["tiers"]):
        t["max_days"] = cols[i].number_input(t["name"] + " - up to days", 1, 60, t["max_days"], key=f"tier{i}")
    pa["reject_older_than_days"] = cols[-1].number_input("Reject older than (days)", 1, 90, pa["reject_older_than_days"])

    st.subheader("Excluded companies")
    blocked = st.text_area("Companies to skip (one per line)", "\n".join(prefs["companies"]["blocked_companies"]))
    prefs["companies"]["blocked_companies"] = [x.strip() for x in blocked.splitlines() if x.strip()]

    st.subheader("Search and matching")
    kw = prefs.setdefault("search_keywords", {})
    k1, k2 = st.columns(2)
    kw["experienced"] = [x.strip() for x in k1.text_area("Search words - experienced candidates (one per line)",
                         "\n".join(kw.get("experienced", [])), height=170).splitlines() if x.strip()]
    kw["fresher"] = [x.strip() for x in k2.text_area("Search words - freshers (one per line)",
                     "\n".join(kw.get("fresher", [])), height=170).splitlines() if x.strip()]
    m1, m2, m3 = st.columns(3)
    prefs.setdefault("matching", {})["min_score"] = m1.number_input("Minimum match score to prepare (0-100)", 0, 100,
                                                                    int(prefs.get("matching", {}).get("min_score", 55)))
    prefs["run"]["review_tabs_open_at_once"] = m2.number_input("Applications open for review at a time", 1, 50,
                                                               int(prefs["run"].get("review_tabs_open_at_once") or 15))
    prefs["run"]["max_pages_per_search"] = m3.number_input("Result pages read per search word", 1, 10,
                                                           int(prefs["run"].get("max_pages_per_search") or 3))

    st.subheader("Company career pages (Greenhouse / Lever / Workday)")
    st.caption("Paste the career-page link of companies you want searched directly, one per line - e.g. "
               "https://boards.greenhouse.io/company, https://jobs.lever.co/company, "
               "https://company.wd3.myworkdayjobs.com/External. Jobs found on LinkedIn / Naukri that apply on these sites are "
               "handled automatically too.")
    pages_txt = st.text_area("Career pages", "\n".join(prefs.get("company_career_pages", [])), height=110)
    prefs["company_career_pages"] = [x.strip() for x in pages_txt.splitlines() if x.strip()]
    from adapters.ats import parse_board
    bad = [u for u in prefs["company_career_pages"] if not parse_board(u)]
    if bad:
        st.warning("Not a Greenhouse / Lever / Workday link (will be ignored): " + ", ".join(bad))

    if not IS_ADMIN:
        st.info("Only the admin can change these settings (they apply to every user).")
    if IS_ADMIN and st.button("Save preferences", type="primary"):
        cfg.save_preferences(prefs)
        st.success("Preferences saved to config/preferences.json")
    with st.expander("Job titles and keywords used for matching (config/taxonomy.json)"):
        st.json(cfg.load_taxonomy(), expanded=False)


# =============================== TRACKER ===============================
with tab_tracker:
    st.subheader("Application tracker")
    all_c = {c["id"]: c["name"] for c in cfg.list_candidates(None if IS_ADMIN else USER["username"])}
    c0, c1, c2, c3 = st.columns([2, 2, 2, 2])
    who = c0.selectbox("Candidate", ["All candidates"] + list(all_c), index=1 + list(all_c).index(active),
                       format_func=lambda i: all_c.get(i, i))
    f_status = c1.multiselect("Status", db.STATUSES)
    all_plats = sorted({p for m in prefs["markets"].values() for p in m["platforms"]})
    f_plat = c2.multiselect("Platform", all_plats)
    f_search = c3.text_input("Search company / title / location")
    rows = db.list_jobs(None if who == "All candidates" else who, f_status or None, f_plat or None, None,
                        f_search or None)
    rows = [r for r in rows if r.get("candidate_id") in all_c]          # only this user's candidates
    for r in rows:
        r["candidate"] = all_c.get(r.get("candidate_id"), r.get("candidate_id"))
    show = ["id", "candidate", "company", "title", "location", "salary_text", "source_platform", "date_posted",
            "date_prepared", "match_score", "resume_used", "status", "notes", "job_url"]
    df = pd.DataFrame(rows, columns=show)
    st.dataframe(df, width="stretch", hide_index=True,
                 column_config={"job_url": st.column_config.LinkColumn("Job URL")})
    st.caption(f"{len(df)} job(s)")
    if rows:
        with st.expander("Update a job"):
            jid = st.number_input("Job id", min_value=1, step=1)
            new_status = st.selectbox("New status", db.STATUSES)
            note = st.text_input("Note")
            if st.button("Update"):
                if int(jid) not in {r["id"] for r in rows}:
                    st.error("No such job in your list.")
                else:
                    db.update_job(int(jid), status=new_status, notes=note or None)
                    st.success("Updated.")
        st.download_button("Download tracker (CSV)", df.to_csv(index=False).encode(), "application_tracker.csv")


# =============================== LOGS ===============================
with tab_logs:
    mine = {c["id"] for c in cfg.list_candidates(None if IS_ADMIN else USER["username"])}
    runs = [r for r in db.list_runs(200) if IS_ADMIN or r.get("candidate_id") in mine]
    st.subheader("Runs")
    st.dataframe(pd.DataFrame(runs), width="stretch", hide_index=True)
    st.subheader("Log")
    run_ids = {r["id"] for r in runs}
    logs = [l for l in db.recent_logs(500) if IS_ADMIN or l.get("run_id") in run_ids]
    st.dataframe(pd.DataFrame(logs), width="stretch", hide_index=True)


with tab_account:
    render_account()
if IS_ADMIN:
    with tab_admin:
        render_admin()
