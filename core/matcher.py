"""Match score: transparent, rule-based, computed only from the job text and the confirmed profile.

score = role relevance (35) + skills overlap (25) + experience fit (25) + qualification fit (15)
Every point comes with a written reason, so you can see exactly why a job scored what it did.
"""
import re

from . import filters as F

ROLE_WORDS = ["audit", "auditor", "assurance", "account", "accountant", "accounts", "finance", "financial reporting",
              "tax", "taxation", "gst", "tds", "bookkeep", "ledger", "reconciliation", "controller", "internal control",
              "sox", "ifc", "ind as", "ifrs", "costing", "mis", "billing", "payable", "receivable", "treasury",
              "compliance", "risk advisory", "due diligence", "forensic", "article", "chartered"]

OFF_ROLE = ["sales", "business development", "telecaller", "telesales", "marketing executive", "software engineer",
            "developer", "nurse", "driver", "delivery", "customer support", "call center", "insurance advisor",
            "relationship manager"]


def _has(word, text):
    return re.search(r"(?<![a-z])" + re.escape(word.lower()) + r"(?![a-z])", text) is not None


def _quals(profile):
    out = {}
    for q in profile.get("qualifications", []):
        n = (q.get("name") or "").lower()
        out[n] = (q.get("status") or "").lower()
    return out


def candidate_is_ca(profile):
    for n, st in _quals(profile).items():
        if (re.fullmatch(r"ca|chartered accountant|aca|fca", n) or n.startswith("ca ") and "inter" not in n
                and "foundation" not in n) and ("qualified" in st or st in ("", "member")):
            if "pursuing" not in st and "final pending" not in st:
                return True
    return False


def candidate_has(profile, word):
    blob = " ".join(f"{q.get('name', '')} {q.get('status', '')}" for q in profile.get("qualifications", [])).lower()
    return _has(word, blob)


def score_job(job: dict, profile: dict, prefs: dict, taxonomy: dict) -> dict:
    """job: dict with title, company, location, description, salary_text, experience_text, posted.
    Returns {eligible, status, score, reasons, reject_reason, tier, salary_*}."""
    title = (job.get("title") or "")
    desc = (job.get("description") or "")
    tl, dl = title.lower(), desc.lower()
    reasons, reject = [], ""

    # ---------- hard filters ----------
    why = F.excluded(job.get("company", ""), title, desc, prefs, taxonomy)
    if why:
        reject = why
    ok, loc_note = F.location_ok(job.get("location", ""), desc, prefs)
    if not ok and not reject:
        reject = loc_note
    days = F.posted_days_ago(job.get("posted"))
    tier, too_old = F.age_tier(days, prefs)
    if too_old and not reject:
        reject = f"Posted {days} days ago (older than {prefs['posting_age']['reject_older_than_days']})"
    from .config import candidate_min_ctc
    sal_dec, sal_note = F.salary_decision(job.get("salary_text", ""), candidate_min_ctc(profile, prefs), prefs)
    if sal_dec == "reject" and not reject:
        reject = sal_note
    jmin, jmax = F.parse_experience_range(job.get("experience_text") or "")
    if jmin is None:
        jmin, jmax = F.parse_experience_range(desc)
    cand = profile.get("total_experience_years")
    cand = float(cand) if cand not in (None, "") else None
    exp_ok, exp_fit, exp_note = F.experience_decision(jmin, jmax, cand)
    if not exp_ok and not reject:
        reject = exp_note
    if any(_has(w, tl) for w in OFF_ROLE) and not any(_has(w, tl) for w in ("account", "audit", "finance", "tax")):
        reject = reject or "Not an audit / accounting role"

    # ---------- role relevance (35) ----------
    title_hits = [w for w in ROLE_WORDS if w in tl]
    sig_hits = [s for s in taxonomy.get("description_signals", []) if _has(s, dl)]
    role = (20 if title_hits else 0) + min(15, len(sig_hits) * 1.5)
    if not title_hits and len(sig_hits) < 3:
        reject = reject or "Title and description do not look like audit / accounting"
    reasons.append(f"Role: {'title matches (' + ', '.join(title_hits[:3]) + ')' if title_hits else 'title not a direct match'}; "
                   f"{len(sig_hits)} audit/accounting terms in description")

    # ---------- skills overlap (25) ----------
    cand_sk = [s for s in profile.get("skills", []) + profile.get("software", []) if s]
    hit_sk = [s for s in cand_sk if _has(s.lower(), dl) or _has(s.lower().replace("ms ", ""), dl)]
    want = [s for s in taxonomy.get("basic_skill_signals", []) + ["SAP", "Ind AS", "IFRS", "Statutory audit",
                                                                  "Internal audit", "GST", "TDS"] if _has(s, dl)]
    skills = 25 * (len(set(map(str.lower, hit_sk))) / max(4, min(len(want), 10))) if want else (12 if hit_sk else 8)
    skills = min(25, skills)
    reasons.append(f"Skills: {len(hit_sk)} of the candidate's skills appear in the job"
                   + (f" ({', '.join(hit_sk[:5])})" if hit_sk else ""))

    # ---------- experience (25) ----------
    experience = 25 * exp_fit
    reasons.append(f"Experience: {exp_note}")

    # ---------- qualification (15) ----------
    qual, qnote = 10, "No specific qualification demanded"
    ca = r"(?:ca|chartered accountant)"
    needs_ca = re.search(rf"qualified {ca}\b|\bca qualified|newly qualified|\b(?:must|should) be (?:a )?(?:qualified )?{ca}\b|"
                         rf"\b{ca} (?:is )?(?:mandatory|required|must)|\b(?:qualification|education)\s*[:\-]\s*{ca}\b|"
                         rf"\bpost[- ]qualification|\bpqe\b", dl)
    ca_pref = re.search(rf"\b{ca}\b[^.\n]{{0,30}}\b(?:preferred|advantage|plus|desirable)", dl)
    ca_inter_ok = re.search(r"semi[- ]qualified|ca inter|ca intermediate|ipcc|ca dropout|ca final", dl)
    if needs_ca and not ca_inter_ok:
        if candidate_is_ca(profile):
            qual, qnote = 15, "Needs CA - candidate is CA"
        else:
            qual, qnote = 0, "Job asks for a qualified CA"
            reject = reject or "Requires a qualified CA"
    elif ca_pref and not candidate_is_ca(profile):
        qual, qnote = 6, "CA preferred (not mandatory)"
    elif ca_inter_ok and (candidate_has(profile, "inter") or candidate_is_ca(profile)):
        qual, qnote = 15, "CA Inter / semi-qualified fits"
    for code in ("cma", "acca", "cfa", "cpa", "cia", "cisa"):
        if _has(code, dl) and candidate_has(profile, code):
            qual, qnote = 15, f"{code.upper()} mentioned and candidate has it"
    reasons.append(f"Qualification: {qnote}")

    score = round(role + skills + experience + qual)
    if tier:
        reasons.append(f"Posted: {'unknown' if days is None else str(days) + ' days ago'} ({tier})")
    if sal_note:
        reasons.append(f"Salary: {sal_note}")
    if loc_note:
        reasons.append(f"Location: {loc_note}")

    min_score = prefs.get("matching", {}).get("min_score", 55)
    if reject:
        status = "Not Eligible"
    elif score >= min_score:
        status = "Match Approved"
    else:
        status = "Under Review"
    return {"eligible": not reject, "status": status, "score": score, "reasons": reasons,
            "reject_reason": reject, "tier": tier, "days": days}
