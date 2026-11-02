"""Answers to application-form questions.

Rules (from the project spec):
* Facts come ONLY from the confirmed candidate profile. Nothing is invented.
* If a question is unusual, ambiguous, sensitive or cannot be answered from the profile,
  `answer()` returns None and the form is left for the user at that question.
* Written answers are short, plain and job-specific (no buzzwords, no exaggeration).
  If an Anthropic API key is set (environment variable ANTHROPIC_API_KEY), Claude rewrites the
  draft in a more natural voice using the same facts; otherwise the built-in writer is used.
"""
import os
import random
import re
from dataclasses import dataclass

YES, NO = "Yes", "No"


@dataclass
class Answer:
    value: object            # str / number / "Yes" / "No" / file path
    source: str              # where the answer came from (shown in the review notes)
    generated: bool = False  # True for written (free-text) answers


# ======================================================================= helpers
def _q(text):
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def _years(profile):
    v = profile.get("total_experience_years")
    return None if v in (None, "") else float(v)


def _fmt_years(y):
    return str(int(y)) if float(y).is_integer() else f"{y:g}"


def _ind(profile):
    return profile.get("india_application_fields", {}) or {}


def _degree_level(profile):
    names = " ".join(e.get("degree", "") for e in profile.get("education", [])).lower()
    if re.search(r"m\.?\s?com|mba|pgdm|master|m\.?\s?sc|\bma\b", names):
        return "master"
    if re.search(r"b\.?\s?com|bba|bachelor|b\.?\s?sc|\bba\b|bms|baf", names):
        return "bachelor"
    if re.search(r"xii|12|hsc", names):
        return "school"
    return None


def _qual_status(profile, pattern):
    """Return the status text of a qualification whose name matches pattern, '' if listed without status,
    None if not listed."""
    for q in profile.get("qualifications", []):
        if re.search(pattern, (q.get("name") or ""), re.I):
            return (q.get("status") or "").strip()
    return None


def _all_text(profile):
    parts = [profile.get("summary", "")] + profile.get("skills", []) + profile.get("software", [])
    for e in profile.get("experience", []):
        parts += [e.get("designation", ""), e.get("responsibilities", "")]
    return " ".join(parts).lower()


def _lakh(v):
    return f"{v / 100000:g}"


def money_for_field(amount, question):
    """Convert annual rupees to the unit the question asks for."""
    q = _q(question)
    if amount is None:
        return None
    if re.search(r"lakh|lac|lpa|\bin l\b", q):
        return _lakh(amount)
    if re.search(r"month|monthly|per month|/m\b", q):
        return str(int(round(amount / 12)))
    return str(int(amount))


def notice_for_field(days, question, options=None):
    if days is None:
        return None
    q = _q(question)
    if options:
        return pick_notice_option(days, options)
    if "month" in q:
        return f"{days / 30:g}"
    return str(int(days))


def pick_notice_option(days, options):
    """'Immediately', '15 days or less', '1 Month', '2 Months', '3 Months', 'More than 3 Months' ..."""
    best, best_gap = None, 10 ** 9
    for o in options:
        t = o.lower()
        if re.search(r"immediate|serving|0 days", t):
            val = 0
        else:
            m = re.search(r"(\d+)\s*(day|week|month)", t)
            if not m:
                continue
            n, u = int(m.group(1)), m.group(2)
            val = n * (30 if u == "month" else 7 if u == "week" else 1)
            if "more than" in t:
                val += 1
        if "or less" in t and days <= val:
            return o
        gap = abs(val - days)
        if gap < best_gap:
            best, best_gap = o, gap
    return best


def pick_option(desired, options):
    """Choose the option that best matches the desired answer, or None."""
    if desired is None or not options:
        return None
    d = str(desired).strip().lower()
    opts = [o for o in options if o and not re.search(r"^(select|choose|--|please select)", o.strip(), re.I)]
    for o in opts:
        if o.strip().lower() == d:
            return o
    for o in opts:
        if re.match(rf"^{re.escape(d)}\b", o.strip().lower()):
            return o
    if d in ("yes", "no"):
        return None
    for o in opts:
        if d in o.lower() or o.lower() in d:
            return o
    try:                                   # numeric ranges, e.g. "2-3 years"
        x = float(d)
        for o in opts:
            m = re.search(r"(\d+(?:\.\d+)?)\s*(?:-|to)\s*(\d+(?:\.\d+)?)", o)
            if m and float(m.group(1)) <= x <= float(m.group(2)):
                return o
    except ValueError:
        pass
    return None


# ======================================================================= question rules
SENSITIVE = r"gender|sex\b|marital|religion|caste|category \(|reserved category|disab|pwd|ethnic|race\b|veteran|" \
            r"date of birth|dob\b|age\b|pan\b|aadhaar|aadhar|passport|blood group|father|mother|spouse"

SKIP_OPTIONAL = r"cover letter.*upload|portfolio|website|github|twitter|referral code|referred by|employee id"


def answer(question, kind, options, profile, job, platform=""):
    """Return an Answer or None (= stop: the user must answer this one)."""
    q = _q(question)
    p = profile.get("personal", {})
    ind = _ind(profile)
    years = _years(profile)
    if not q:
        return None

    # ---- personal ----
    if re.search(r"first name|given name", q):
        n = (p.get("full_name") or "").split()
        return Answer(n[0], "profile") if n else None
    if re.search(r"last name|surname|family name", q):
        n = (p.get("full_name") or "").split()
        return Answer(" ".join(n[1:]), "profile") if len(n) > 1 else None
    if re.search(r"middle name", q):
        return None
    if re.search(r"^(full )?name\b|your name|candidate name|full name", q):
        return Answer(p.get("full_name"), "profile") if p.get("full_name") else None
    if re.search(r"e-?mail", q):
        return Answer(p.get("email"), "profile") if p.get("email") else None
    if re.search(r"country code", q):
        return Answer("+91", "India market") if (p.get("phone") or "").replace(" ", "").startswith(("+91", "91", "0")) or len(re.sub(r"\D", "", p.get("phone") or "")) == 10 else None
    if re.search(r"phone|mobile|contact number|cell", q):
        digits = re.sub(r"\D", "", p.get("phone") or "")
        if not digits:
            return None
        local = digits[-10:]
        return Answer(local if re.search(r"10.digit|without country", q) or platform in ("LinkedIn", "Naukri", "Indeed") else (p.get("phone") or local), "profile")
    if re.search(r"linkedin", q):
        return Answer(p.get("linkedin_url"), "profile") if p.get("linkedin_url") else None
    if re.search(SENSITIVE, q):
        if re.search(r"date of birth|dob", q) and p.get("date_of_birth"):
            return Answer(p["date_of_birth"], "profile")
        return None
    if re.search(SKIP_OPTIONAL, q):
        return None
    if re.search(r"current (city|location)|where do you (currently )?live|city of residence|^city$|^location( \(city\))?$|"
                 r"current address|address.*city", q):
        return Answer(p.get("current_city"), "profile") if p.get("current_city") else None

    # ---- CTC / notice ----
    if re.search(r"current (ctc|salary|compensation|package)|present (ctc|salary)|last drawn", q):
        v = ind.get("current_ctc_annual_inr")
        if v is None:
            return None
        return Answer(money_for_field(v, q) if v else "0", "profile (current CTC)")
    if re.search(r"expected (ctc|salary|compensation|package)|salary expectation|desired salary|expected pay", q):
        v = ind.get("expected_ctc_annual_inr")
        return Answer(money_for_field(v, q), "profile (expected CTC)") if v else None
    if re.search(r"notice period|how soon can you join|when can you (join|start)|joining time|earliest (start|joining)", q):
        d = ind.get("notice_period_days")
        if d is None:
            return None
        if kind in ("select", "radio"):
            o = pick_notice_option(d, options)
            return Answer(o, "profile (notice period)") if o else None
        if re.search(r"immediate", q):                         # "Can you join immediately?"
            return Answer(YES if d == 0 else NO, "profile (notice period)")
        return Answer(notice_for_field(d, q), "profile (notice period)")
    if re.search(r"serving notice|currently serving", q):
        return None

    # ---- location / relocation / commute ----
    if re.search(r"relocat", q):
        v = ind.get("willing_to_relocate")
        return Answer(YES if v else NO, "profile") if v is not None else None
    if re.search(r"commut|comfortable (working|travelling) (from|to)|work from (the )?office|on-?site|onsite|hybrid", q):
        city = (p.get("current_city") or "").lower()
        jl = (job.get("location") or "").lower()
        mumbai = ["mumbai", "thane", "navi mumbai", "andheri", "bandra", "powai", "goregaon", "malad", "borivali",
                  "kurla", "worli", "vikhroli", "lower parel", "bkc", "fort", "nariman"]
        if city and (city in jl or (any(m in city for m in mumbai) and any(m in jl for m in mumbai))):
            return Answer(YES, "candidate lives in the job's city")
        return None

    # ---- work authorisation ----
    if re.search(r"authori[sz]ed to work|legally (allowed|eligible|authorized)|right to work|work permit", q):
        if re.search(r"united states|\bu\.?s\.?\b|usa|america", q):
            return None                              # USA on hold - never answered automatically
        v = profile.get("work_authorization", {}).get("India", {}).get("authorized_to_work_in_india")
        return Answer(YES if v else NO, "profile") if v is not None else None
    if re.search(r"sponsor", q):
        if re.search(r"united states|\bu\.?s\.?\b|usa|h-?1b", q):
            return None
        v = profile.get("work_authorization", {}).get("India", {}).get("authorized_to_work_in_india")
        return Answer(NO, "authorised to work in India - no sponsorship needed") if v else None

    # ---- experience ----
    m = re.search(r"(?:years?|yrs).{0,40}?(?:experience|worked)\s+(?:do you have\s+)?(?:in|with|using|on|of)\s+(.+?)\??$", q) \
        or re.search(r"experience (?:in|with|using|on) (.+?)\??$", q) \
        or re.search(r"years? of (?!work\b|total\b|professional\b|relevant\b|overall\b|industry\b|full.time\b)"
                     r"([a-z][a-z0-9 .&/+-]{1,40}?) experience", q)
    skill = None
    if m and ("year" in q or kind in ("number",)):
        skill = re.sub(r"\b(the|field|domain|role|area)\b", "", m.group(1)).strip(" ?.")
        if skill in ("total", "overall", "all", "", "your career"):
            skill = None
    if m and skill and ("year" in q or kind in ("number",)):
        if years is None:
            return None
        if years == 0:
            return Answer("0", "candidate is a fresher (0 years of work experience)")
        if skill and re.search(r"(?<![a-z])" + re.escape(skill) + r"s?(?![a-z])", _all_text(profile)):
            v = int(years) if kind == "number" else _fmt_years(years)
            if kind in ("select", "radio"):
                o = pick_option(str(v), options)
                return Answer(o, f"'{skill}' appears in the candidate's work experience") if o else None
            return Answer(str(v), f"'{skill}' appears in the candidate's work experience")
        return None                                   # not evidenced in the profile -> ask the user
    if re.search(r"(total|overall|relevant)?.*years? of (work |total |professional |relevant )?experience(?! (in|with|using|on) )|"
                 r"how many years.*(experience|worked)(?! (in|with|using|on) )|total experience|experience in total|work experience \(years\)", q) \
            and not re.search(r"experience (in|with|using|on) (?!total)", q):
        if years is None:
            return None
        v = _fmt_years(years)
        if kind in ("select", "radio"):
            o = pick_option(v, options) or pick_option(str(int(years)), options)
            return Answer(o, "profile (total experience)") if o else None
        return Answer(str(int(years)) if kind == "number" and years.is_integer() else v, "profile (total experience)")

    # ---- education / qualifications ----
    if re.search(r"bachelor'?s? degree|graduat(e|ion)\b.*\?|completed.*(bachelor|graduation)", q):
        lvl = _degree_level(profile)
        return Answer(YES if lvl in ("bachelor", "master") else NO, "profile (education)") if lvl else None
    if re.search(r"master'?s? degree|post.?graduat", q):
        lvl = _degree_level(profile)
        return Answer(YES if lvl == "master" else NO, "profile (education)") if lvl else None
    if re.search(r"highest (qualification|education|degree)", q):
        edu = profile.get("education", [])
        if not edu:
            return None
        top = edu[0].get("degree", "")
        if kind in ("select", "radio"):
            lvl = _degree_level(profile)
            o = pick_option(top, options) or pick_option({"master": "Post Graduate", "bachelor": "Graduate"}.get(lvl, ""), options) \
                or pick_option({"master": "Master", "bachelor": "Bachelor"}.get(lvl, ""), options)
            return Answer(o, "profile (education)") if o else None
        return Answer(top, "profile (education)")
    for pat, label in [(r"\bca inter|ca intermediate|ipcc|semi.?qualified", r"ca inter|intermediate|ipcc"),
                       (r"chartered accountant|\bqualified ca\b|\bare you (a )?ca\b|\bca qualified", r"^ca$|chartered accountant|^ca \(|^ca final"),
                       (r"\bcpa\b|certified public accountant", r"cpa"), (r"\bcma\b|icwa", r"cma|icwa"),
                       (r"\bacca\b", r"acca"), (r"\bcfa\b", r"cfa"), (r"\bcia\b|certified internal auditor", r"\bcia\b"),
                       (r"company secretary|\bcs\b", r"^cs\b|company secretary")]:
        if re.search(pat, q):
            st = _qual_status(profile, label)
            if st is None:
                return Answer(NO, "not in the confirmed profile") if profile.get("confirmed") else None
            if re.search(r"qualified|cleared|completed|passed|member", st, re.I) and not re.search(r"pursuing|candidate|pending", st, re.I):
                return Answer(YES, f"profile: {st}")
            return None                               # pursuing / candidate -> the user decides how to answer
    if re.search(r"articleship|article assistant", q):
        has = any(re.search(r"article", (e.get("designation", "") + e.get("organisation", "")), re.I)
                  for e in profile.get("experience", []))
        return Answer(YES, "profile (experience)") if has else None

    # ---- software / skills yes-no ----
    m = re.search(r"(?:do you (?:have|know)|are you (?:familiar|proficient|comfortable) with|have you (?:worked|used)|"
                  r"knowledge of|experience (?:in|with|of)|hands.on)\s+(?:on |with |in |using )?(.+?)\??$", q)
    if m and kind in ("radio", "select", "checkbox") and len(m.group(1)) < 60:
        tool = m.group(1).strip()
        blob = _all_text(profile)
        words = [w for w in re.split(r"[ /,&]+|\band\b|\bor\b", tool) if len(w) > 1]
        if words and all(re.search(r"(?<![a-z])" + re.escape(w) + r"s?(?![a-z])", blob) for w in words):
            return Answer(YES, f"'{tool}' is in the candidate's skills / experience")
        return None
    if re.search(r"english.*(proficien|fluen)|proficien.*english|language", q):
        return None

    # ---- consent / declarations: left for the user ----
    if re.search(r"agree|consent|privacy|terms|declar|acknowledg|certify|confirm that", q):
        return None
    if re.search(r"how did you (hear|find|come to know)|source of (application|this job)|where did you (see|find)", q):
        if kind in ("select", "radio"):
            o = pick_option(platform, options) or pick_option("Job board", options) or pick_option("Job portal", options)
            return Answer(o, "platform used") if o else None
        return Answer(platform or "Job portal", "platform used")

    # ---- written answers ----
    wtype = written_type(q)
    if wtype and kind in ("textarea", "text"):
        txt = write_answer(wtype, profile, job, question)
        return Answer(txt, "written from the profile", generated=True) if txt else None
    if re.search(r"headline|summary|about (yourself|you)", q):
        return Answer(profile.get("summary"), "profile summary") if profile.get("summary") else None
    return None


def written_type(q):
    q = _q(q)
    if re.search(r"cover letter|covering letter|message to (the )?(hiring|recruiter)|note to (the )?recruiter|additional information|anything else", q):
        return "cover"
    if re.search(r"why .*(interested|apply|this (role|position|job|company)|join)|what (interests|attracts) you|motivat", q):
        return "why"
    if re.search(r"why should we hire|what makes you (a )?(good|suitable|right)|why are you (a )?(good )?fit|strength", q):
        return "fit"
    if re.search(r"tell us about yourself|describe yourself|introduce yourself|brief (about|profile)|profile summary|summary", q):
        return "about"
    if re.search(r"describe your (audit|accounting|relevant|work) experience|relevant experience|key responsibilities", q):
        return "experience"
    return None


# ======================================================================= writer
def _facts(profile, job):
    p = profile.get("personal", {})
    years = _years(profile)
    exp = profile.get("experience", [])
    latest = exp[0] if exp else {}
    jd = (job.get("description") or "").lower()
    pool = profile.get("skills", []) + profile.get("software", [])
    soft = set(profile.get("software", []))
    nice = lambda s: s if (s in soft or re.search(r"\b[A-Z]{2,}\b", s)) else s.lower()
    matched = [nice(s) for s in pool if s and re.search(r"(?<![a-z])" + re.escape(s.lower().replace("ms ", "")) + r"(?![a-z])", jd)]
    pool = [nice(s) for s in pool]
    quals = []
    for q in profile.get("qualifications", []):
        n, st = q.get("name", ""), (q.get("status") or "").lower()
        if "candidate" in st:
            quals.append(f"I am a {n} candidate.")
        elif "pursuing" in st:
            quals.append(f"I am currently pursuing {n}.")
        elif re.search(r"qualified|member", st):
            quals.append(f"I am a qualified {n}.")
        elif st:
            quals.append(f"{n}: {q.get('status')}.")
    edu = profile.get("education", [])
    deg = edu[0] if edu else {}
    resp = [r.strip() for r in (latest.get("responsibilities") or "").split("\n") if r.strip()]
    rel = [r for r in resp if any(w in r.lower() for w in re.findall(r"[a-z]{5,}", jd))][:2] or resp[:2]
    return dict(name=p.get("full_name", ""), years=years, org=latest.get("organisation", ""),
                role=latest.get("designation", ""), matched=matched[:4], pool=pool[:4], quals=quals,
                degree=" ".join(x for x in [deg.get("degree", ""), f"({deg['specialisation']})" if deg.get("specialisation") else ""] if x),
                deg_year=deg.get("year", ""), resp=rel, title=job.get("title", "this role"),
                company=job.get("company", "your organisation"))


def _join(items):
    items = [i for i in items if i]
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1] if items else ""


def _lower_first(s):
    return s[:1].lower() + s[1:] if s and not s[:2].isupper() else s


def write_answer(kind, profile, job, question=""):
    f = _facts(profile, job)
    rnd = random.Random(f"{f['company']}|{f['title']}|{kind}")      # varied per job, stable on re-run
    skills = _join(f["matched"] or f["pool"])
    fresher = not f["years"]
    if fresher:
        base = (f"I completed my {f['degree']}" + (f" in {f['deg_year']}" if f["deg_year"] else "")
                + (f" and have practical working knowledge of {skills}" if skills else "") + ".")
    else:
        where = f" at {f['org']}" if f["org"] else ""
        as_role = f" as {f['role']}" if f["role"] else ""
        base = f"I have {_fmt_years(f['years'])} years of experience{as_role}{where}."
    work = ""
    if f["resp"] and not fresher:
        r = f["resp"][0].rstrip(".")
        first = r.split()[0].lower() if r.split() else ""
        if first.endswith("ed") or first in ("led", "ran", "built", "did", "made", "wrote", "drew", "took"):
            work = f" There, I {_lower_first(r)}."
        else:
            work = f" My work there includes {_lower_first(r)}."
    quals = (" " + f["quals"][0]) if f["quals"] else ""

    if kind == "why":
        opener = rnd.choice([f"The {f['title']} role fits the work I want to keep doing.",
                             f"I am applying for the {f['title']} role because it is close to the work I already do."
                             if not fresher else f"I am applying for the {f['title']} role because it is where I want to start my career.",
                             f"This {f['title']} position matches my background well."])
        tail = rnd.choice([f" I would like to build on this with {f['company']} and take on more responsibility over time.",
                           f" Joining {f['company']} would give me the chance to work on a wider range of clients and keep learning.",
                           f" I think I can contribute from the start and grow with the team at {f['company']}."])
        txt = opener + " " + base + work + (f" The role asks for {skills}, which I use regularly." if f["matched"] and not fresher else "") + tail
    elif kind == "fit":
        txt = base + work + (f" I am comfortable with {skills}." if skills and not fresher else "") + quals + \
              rnd.choice([" I am careful with detail and meet deadlines.", " I work carefully and keep my documentation in order.",
                          " I take ownership of my work and ask questions early when something is unclear."])
    elif kind == "about":
        txt = profile.get("summary") or (base + work + quals)
    elif kind == "experience":
        if fresher and not f["resp"]:
            return None
        txt = base + (" " + " ".join(r.rstrip(".") + "." for r in f["resp"]) if f["resp"] else "")
    elif kind == "cover":
        txt = (f"Dear Hiring Team,\n\nI would like to apply for the {f['title']} role at {f['company']}. " + base + work + quals
               + (f"\n\nThe job asks for {skills}, which matches what I work with." if f["matched"] else "")
               + "\n\nI would be glad to discuss how I can help your team. Thank you for considering my application.\n\n"
               + f"Regards,\n{f['name']}")
    else:
        return None
    txt = re.sub(r"\s+([.,])", r"\1", re.sub(r"[ \t]+", " ", txt)).strip()
    return polish_with_claude(txt, kind, question, profile, job) or txt


def polish_with_claude(draft, kind, question, profile, job):
    """Optional: rewrite the draft naturally with Claude, using only the given facts. Silent fallback."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=key)
        facts = _facts(profile, job)
        msg = client.messages.create(
            model=os.environ.get("JAA_CLAUDE_MODEL", "claude-sonnet-5-5"), max_tokens=500,
            messages=[{"role": "user", "content":
                       "Rewrite this job-application answer so it reads like a real person wrote it: plain, specific, "
                       "formal but conversational, no buzzwords, no exaggeration, under 120 words (cover letters under 180). "
                       "Use ONLY the facts below; do not add any skill, number, employer, qualification or claim that is "
                       "not in them. Return only the answer text.\n\n"
                       f"Question: {question}\nJob: {job.get('title')} at {job.get('company')}\n"
                       f"Facts: {facts}\nDraft: {draft}"}])
        out = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
        return out or None
    except Exception:
        return None
