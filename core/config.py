"""Configuration, candidates and their profiles - all stored in the database (core/db.py),
so the online website and the PC worker see the same data.

    kv   'preferences'            shared settings (admin edits)
    kv   'candidate:<id>'         a candidate's profile
    kv   'active:<user>'          the candidate a user worked on last
    files 'cv/<id>/<file name>'   the candidate's CV files
"""
import copy
import json
import re
from pathlib import Path

from . import db

ROOT = db.ROOT
CONFIG_DIR = ROOT / "config"
DATA_DIR = db.DATA_DIR
CACHE_DIR = DATA_DIR / "cache"            # local copies of CVs (needed to upload them in the browser)
TEMPLATES_DIR = ROOT / "templates"
PREFS_PATH = CONFIG_DIR / "preferences.json"       # default settings shipped with the app
TAXONOMY_PATH = CONFIG_DIR / "taxonomy.json"
TEMPLATE_PATH = CONFIG_DIR / "profile_template.json"

for d in (DATA_DIR, CACHE_DIR, TEMPLATES_DIR):
    d.mkdir(parents=True, exist_ok=True)


def _read(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------------- preferences / taxonomy ----------------
def load_preferences() -> dict:
    prefs = db.kv_get("preferences") or _read(PREFS_PATH)
    default = _read(PREFS_PATH)
    for k, v in default.items():                 # settings added in newer versions
        prefs.setdefault(k, copy.deepcopy(v))
    prefs.setdefault("run", {})["auto_submit"] = False      # can never be switched on
    return prefs


def save_preferences(prefs: dict) -> None:
    prefs = copy.deepcopy(prefs)
    prefs.setdefault("run", {})["auto_submit"] = False
    db.kv_set("preferences", prefs)


def load_taxonomy() -> dict:
    return _read(TAXONOMY_PATH)


def blank_profile() -> dict:
    return _read(TEMPLATE_PATH)


# ---------------- candidates ----------------
def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return s[:40] or "candidate"


def _merge_template(prof):
    base = blank_profile()
    for k, v in base.items():
        prof.setdefault(k, copy.deepcopy(v))
    for k in ("personal", "india_application_fields", "work_authorization"):
        for kk, vv in base[k].items():
            prof[k].setdefault(kk, copy.deepcopy(vv))
    return prof


def list_candidates(owner: str = None) -> list:
    """owner=None -> every candidate (admin view); otherwise only that user's candidates."""
    out = []
    for k, prof in db.kv_prefix("candidate:").items():
        if owner is not None and prof.get("owner", "") != owner:
            continue
        cid = k.split(":", 1)[1]
        out.append({"id": cid, "name": prof.get("personal", {}).get("full_name") or prof.get("display_name") or cid,
                    "owner": prof.get("owner", "")})
    return sorted(out, key=lambda c: c["id"])


def candidate_exists(cid):
    return db.kv_get(f"candidate:{cid}") is not None


def create_candidate(display_name: str, owner: str = "") -> str:
    base = slugify(display_name)
    cid, n = base, 2
    while candidate_exists(cid):
        cid, n = f"{base}-{n}", n + 1
    prof = blank_profile()
    prof["display_name"] = display_name
    prof["owner"] = owner
    save_profile(cid, prof)
    set_active(cid, owner)
    return cid


def get_active(user: str = "", allowed: list = None) -> str:
    """Last candidate this user worked on (each login has its own)."""
    ids = [c["id"] for c in (allowed if allowed is not None else list_candidates())]
    cid = db.kv_get(f"active:{user}")
    if cid in ids:
        return cid
    return ids[0] if ids else ""


def set_active(cid: str, user: str = "") -> None:
    db.kv_set(f"active:{user}", cid)


def load_profile(cid: str) -> dict:
    prof = db.kv_get(f"candidate:{cid}") if cid else None
    return _merge_template(prof or blank_profile())


def save_profile(cid: str, profile: dict) -> None:
    # never lose who the candidate belongs to (e.g. when a CV re-read builds a fresh profile)
    if not profile.get("owner"):
        old = db.kv_get(f"candidate:{cid}") or {}
        if old.get("owner"):
            profile = dict(profile, owner=old["owner"])
    db.kv_set(f"candidate:{cid}", profile)


# ---------------- CV files ----------------
def _base(name):
    return re.split(r"[\\/]", name or "")[-1]


def save_resume(cid: str, name: str, data: bytes) -> str:
    name = _base(name)
    db.file_put(f"cv/{cid}/{name}", data)
    return name


def list_resumes(cid: str) -> list:
    return [p.split("/", 2)[2] for p in db.file_list(f"cv/{cid}/")]


def resume_dir(cid: str) -> Path:
    d = CACHE_DIR / "cv" / cid
    d.mkdir(parents=True, exist_ok=True)
    return d


def resume_local_path(cid: str, name: str):
    """Copy the CV from the database to a local file (the browser needs a real file to upload)."""
    if not name:
        return None
    data = db.file_get(f"cv/{cid}/{name}")
    if data is None:
        p = resume_dir(cid) / name
        return p if p.exists() else None
    p = resume_dir(cid) / _base(name)
    if not p.exists() or p.read_bytes() != data:
        p.write_bytes(data)
    return p


# ---------------- readiness ----------------
def profile_gaps(profile: dict) -> list:
    gaps = []
    p = profile.get("personal", {})
    for key, label in [("full_name", "Full name"), ("email", "Email"), ("phone", "Mobile")]:
        if not p.get(key):
            gaps.append(label)
    if not profile.get("active_resume"):
        gaps.append("Active CV")
    if not profile.get("education"):
        gaps.append("Education")
    if profile.get("total_experience_years") in (None, ""):
        gaps.append("Total experience (0 for fresher)")
    return gaps


def application_answer_gaps(profile: dict, prefs: dict) -> list:
    gaps = []
    if prefs["markets"]["India"]["enabled"]:
        ind = profile.get("india_application_fields", {})
        if ind.get("notice_period_days") in (None, ""):
            gaps.append("Notice period")
        if ind.get("expected_ctc_annual_inr") in (None, ""):
            gaps.append("Expected CTC")
        if profile.get("work_authorization", {}).get("India", {}).get("authorized_to_work_in_india") is None:
            gaps.append("Authorised to work in India")
    if prefs["markets"]["USA"]["enabled"] and not prefs["markets"]["USA"].get("on_hold"):
        us = profile.get("work_authorization", {}).get("USA", {})
        if us.get("authorized_to_work_without_sponsorship") is None:
            gaps.append("USA: authorised without sponsorship")
        if us.get("require_sponsorship_now_or_future") is None:
            gaps.append("USA: sponsorship needed")
    return gaps


def candidate_min_ctc(profile: dict, prefs: dict):
    own = profile.get("india_application_fields", {}).get("minimum_ctc_annual_inr")
    return own if own else prefs["markets"]["India"].get("salary_min_annual")


# ---------------- one-time import of data saved by older versions (files on disk) ----------------
def import_old_files():
    old = DATA_DIR / "candidates"
    if not old.exists() or db.kv_get("migrated_files"):
        return
    for d in old.iterdir():
        pf = d / "profile.json"
        if d.is_dir() and pf.exists() and not candidate_exists(d.name):
            db.kv_set(f"candidate:{d.name}", _read(pf))
            for f in (d / "resumes").glob("*") if (d / "resumes").exists() else []:
                if f.is_file():
                    db.file_put(f"cv/{d.name}/{f.name}", f.read_bytes())
    db.kv_set("migrated_files", True)
