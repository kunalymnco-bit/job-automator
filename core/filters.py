"""Hard filters applied to every job before scoring (preferences + candidate).

Every function returns plain values so it can be unit-tested without a browser.
"""
import re
from datetime import date, datetime, timedelta

LAKH = 100000


# ---------------------------------------------------------------- posting age
def posted_days_ago(text: str, today: date = None):
    """'3 Days Ago', 'Posted 30+ Days Ago', 'Just now', '2 weeks ago', '2026-10-28', epoch ms -> int days or None."""
    today = today or date.today()
    if text is None or text == "":
        return None
    if isinstance(text, (int, float)):                       # epoch milliseconds (Lever)
        return (today - datetime.fromtimestamp(text / 1000).date()).days
    t = str(text).strip().lower()
    if re.search(r"just now|today|few hours|hours? ago|minutes? ago|moments? ago|active today", t):
        return 0
    if "yesterday" in t:
        return 1
    m = re.search(r"(\d+)\s*\+?\s*(day|d\b|week|w\b|month|mo\b|hour|h\b)", t)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        if unit.startswith("h"):
            return 0
        if unit.startswith("w"):
            return n * 7
        if unit.startswith("mo") or unit.startswith("month"):
            return n * 30
        return n
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", t)
    if m:
        return (today - date(int(m.group(1)), int(m.group(2)), int(m.group(3)))).days
    return None


def age_tier(days, prefs):
    """Return (tier_name or None, reject: bool)."""
    pa = prefs["posting_age"]
    if days is None:
        return "Unknown age", False
    if days > pa["reject_older_than_days"]:
        return None, True
    for t in pa["tiers"]:
        if days <= t["max_days"]:
            return t["name"], False
    return None, True


# ---------------------------------------------------------------- salary (INR)
def _num(s):
    return float(s.replace(",", ""))


def parse_salary_inr(text: str):
    """Return (min_annual, max_annual) in rupees, or (None, None) if not disclosed / unreadable.
    Handles '3-5 Lacs PA', '3.5 - 4.5 LPA', '₹3,00,000 - ₹5,00,000 a year', '₹25,000 - 35,000 a month',
    '4 Lakhs', '₹40,000 per month'."""
    if not text:
        return None, None
    t = text.lower().replace("₹", " ").replace("rs.", " ").replace("inr", " ")
    if re.search(r"not disclosed|undisclosed|competitive|as per industry|negotiable", t):
        return None, None
    nums = re.findall(r"\d[\d,]*(?:\.\d+)?", t)
    if not nums:
        return None, None
    vals = [_num(n) for n in nums[:2]]
    if re.search(r"lac|lakh|lpa|\bl\b", t):
        vals = [v * LAKH for v in vals]
    elif re.search(r"\bcr|crore", t):
        vals = [v * 100 * LAKH for v in vals]
    elif re.search(r"month|/mo|p\.?m\.?\b|monthly", t):
        vals = [v * 12 for v in vals]
    elif re.search(r"\bk\b", t):
        vals = [v * 1000 for v in vals]
    elif max(vals) < 1000:                     # e.g. "3-5" on Naukri without unit = lakhs
        vals = [v * LAKH for v in vals]
    lo, hi = (vals[0], vals[1]) if len(vals) > 1 else (vals[0], vals[0])
    return min(lo, hi), max(lo, hi)


def salary_decision(text, minimum, prefs):
    """'consider' | 'reject' with a reason, following the preference rules."""
    lo, hi = parse_salary_inr(text)
    rules = prefs["salary_rules"]
    if lo is None:
        return rules["if_not_disclosed"], "Salary not disclosed"
    if not minimum:
        return "consider", ""
    if hi < minimum:
        return rules["if_maximum_below_minimum"], f"Max salary Rs {hi:,.0f} is below minimum Rs {minimum:,.0f}"
    if lo < minimum <= hi:
        return rules["if_range_overlaps_minimum"], "Salary range overlaps minimum"
    return "consider", ""


# ---------------------------------------------------------------- experience
def parse_experience_range(text: str):
    """'0-2 Yrs', '2 to 5 years', '3+ years', 'Fresher', 'minimum 2 years' -> (min, max) years or (None, None)."""
    if not text:
        return None, None
    t = text.lower()
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:-|–|to)\s*(\d+(?:\.\d+)?)\s*\+?\s*(?:yrs?|years?)", t)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = re.search(r"(?:minimum|min\.?|at least|atleast)\s*(?:of\s*)?(\d+(?:\.\d+)?)\s*\+?\s*(?:yrs?|years?)", t)
    if m:
        return float(m.group(1)), None
    m = re.search(r"(\d+(?:\.\d+)?)\s*\+\s*(?:yrs?|years?)", t)
    if m:
        return float(m.group(1)), None
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:yrs?|years?)\s*(?:of\s*)?(?:relevant\s*|post[- ]qualification\s*|work\s*)?(?:experience|exp)", t)
    if m:
        return float(m.group(1)), None
    if re.search(r"\bfreshers?\b|\b0\s*years?\b|no experience", t):
        return 0.0, 1.0
    return None, None


def experience_decision(job_min, job_max, cand_years):
    """Returns (ok: bool, fit 0..1, reason)."""
    if cand_years is None or job_min is None:
        return True, 0.6, "Experience requirement not stated" if job_min is None else "Candidate experience not set"
    if cand_years + 1 < job_min:
        return False, 0.0, f"Needs {job_min:g}+ years, candidate has {cand_years:g}"
    if cand_years < job_min:
        return True, 0.5, f"Slightly below required {job_min:g} years"
    if job_max is not None and cand_years > job_max + 3:
        return True, 0.3, f"Candidate ({cand_years:g} yrs) is above the {job_min:g}-{job_max:g} yr range"
    return True, 1.0, "Experience fits"


# ---------------------------------------------------------------- location
def location_ok(location: str, description: str, prefs: dict, market="India"):
    m = prefs["markets"][market]
    if m.get("consider_other_locations"):
        return True, ""
    names = [x.lower() for x in (m.get("location_aliases") or []) + m.get("preferred_locations", [])]
    loc = (location or "").lower()
    if any(n and n in loc for n in names):
        return True, ""
    if prefs["work_arrangement"].get("Remote") and re.search(r"\bremote\b|work from home|\bwfh\b", loc):
        return True, "Remote"
    if not loc.strip():
        head = (description or "")[:600].lower()
        if any(n and n in head for n in names):
            return True, ""
        return True, "Location not stated"
    return False, f"Location '{location}' is outside {', '.join(m.get('preferred_locations', []))}"


# ---------------------------------------------------------------- exclusions
def excluded(company: str, title: str, description: str, prefs: dict, taxonomy: dict):
    text = f"{title}\n{description}".lower()
    for b in prefs["companies"].get("blocked_companies", []):
        if b and b.lower() in (company or "").lower():
            return f"Company '{company}' is on your skip list"
    for sig in prefs["companies"].get("exclude_signals", []) + taxonomy.get("negative_signals", []):
        if re.search(r"(?<![a-z])" + re.escape(sig.lower()) + r"(?![a-z])", text):
            return f"Excluded: mentions '{sig}'"
    et = prefs["employment_type"]
    tl = (title or "").lower()
    if not et.get("Internship") and re.search(r"\bintern(ship)?\b", tl) and "article" not in tl:
        return "Internship (not selected in preferences)"
    if not et.get("Part-time") and re.search(r"\bpart[- ]time\b", tl):
        return "Part-time (not selected)"
    if not et.get("Contract") and re.search(r"\b(contract|contractual|temporary|temp)\b", tl):
        return "Contract role (not selected)"
    return ""
