"""Extract text from an uploaded resume/CV and build a DRAFT profile.

Rules:
* Only facts that literally appear in the document are extracted.
* Anything uncertain is left empty and shown to the user to fill or confirm.
* Section text is kept verbatim so the user can see exactly what was read.
"""
import re
from pathlib import Path

SECTION_HEADINGS = {
    "education": ["education", "academic qualification", "academic qualifications", "academics", "educational qualification",
                  "educational qualifications", "education qualification", "academic details", "academic background"],
    "qualifications": ["professional qualification", "professional qualifications", "certifications", "certification",
                       "professional certifications", "credentials"],
    "experience": ["experience", "work experience", "professional experience", "employment history", "employment details",
                   "articleship training", "internship experience", "internships", "work profile",
                   "work history", "articleship", "articleship experience", "internship"],
    "skills": ["skills", "technical skills", "key skills", "core competencies", "areas of expertise", "it skills"],
    "summary": ["summary", "profile", "professional summary", "career objective", "objective", "about me"],
    "achievements": ["achievements", "awards", "accomplishments"],
    "personal": ["personal details", "personal information", "personal profile"],
    "other": ["soft skills", "languages", "languages known", "hobbies", "interests", "hobbies and interests",
              "declaration", "references", "extra curricular activities", "extracurricular activities",
              "projects", "strengths"],
}

QUALIFICATION_PATTERNS = [
    ("CA", r"\b(chartered accountant|qualified CA|\bCA\b(?!\s*(inter|intermediate|ipcc|foundation)))"),
    ("CA Inter", r"\bCA\s*(inter|intermediate|ipcc)\b"),
    ("CA Foundation", r"\bCA\s*foundation\b"),
    ("US CPA", r"\b(US\s*CPA|CPA|certified public accountant)\b"),
    ("CMA", r"\b(CMA|ICWA|cost and management accountant)\b"),
    ("ACCA", r"\bACCA\b"),
    ("CFA", r"\b(CFA|chartered financial analyst)\b"),
    ("CIA", r"\b(certified internal auditor|\bCIA\b)"),
    ("CISA", r"\bCISA\b"),
    ("CS", r"\b(company secretary)\b"),
]

SCHOOL_PATTERNS = [("Class XII", r"\b(class\s*xii|xii(th)?|12th|hsc|higher secondary certificate)\b"),
                   ("Class X", r"\b(class\s*x|ssc|10th|secondary school certificate)\b(?!i)")]
INSTITUTION_HINT = re.compile(r"(university|vidyapeeth|vidyapith|college|institute|board|school|academy|icai|nmims)", re.I)

DEGREE_PATTERNS = [
    ("B.Com", r"\bB\.?\s?Com\b|\bbachelor of commerce\b"),
    ("M.Com", r"\bM\.?\s?Com\b|\bmaster of commerce\b"),
    ("BBA", r"\bBBA\b|\bbachelor of business administration\b"),
    ("MBA", r"\bMBA\b|\bmaster of business administration\b"),
    ("PGDM", r"\bPGDM\b"),
    ("B.Sc", r"\bB\.?\s?Sc\b"),
    ("BA", r"\bB\.?A\.?\s*\(?(hons|economics|accounting)"),
]

CPA_SECTIONS = ["AUD", "FAR", "REG", "BEC", "BAR", "ISC", "TCP"]
CPA_SECTION_WORDS = {"AUD": r"\b(AUD|audit(ing)? and attestation)\b", "FAR": r"\b(FAR|financial accounting and reporting)\b",
                     "REG": r"\b(REG|regulation)\b", "BEC": r"\bBEC\b", "BAR": r"\bBAR\b", "ISC": r"\bISC\b", "TCP": r"\bTCP\b"}


def extract_text(path: Path) -> str:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        import pdfplumber
        parts = []
        with pdfplumber.open(str(path)) as pdf:
            for page in pdf.pages:
                parts.append(page.extract_text() or "")
        text = "\n".join(parts)
    elif suffix == ".docx":
        import docx
        d = docx.Document(str(path))
        lines = [p.text for p in d.paragraphs]
        for table in d.tables:
            for row in table.rows:
                lines.append(" | ".join(c.text for c in row.cells))
        text = "\n".join(lines)
    elif suffix in (".txt", ".md"):
        text = path.read_text(encoding="utf-8", errors="ignore")
    else:
        raise ValueError(f"Unsupported file type: {suffix}. Please upload PDF, DOCX or TXT.")
    return re.sub(r"[ \t]+", " ", text).strip()


def split_sections(text: str) -> dict:
    lines = [l.strip() for l in text.splitlines()]
    sections = {"header": []}
    current = "header"
    for line in lines:
        key = _heading_key(line)
        if key:
            current = key
            sections.setdefault(current, [])
            continue
        if line:
            sections.setdefault(current, []).append(line)
    return {k: "\n".join(v) for k, v in sections.items()}


def _heading_key(line: str):
    clean = re.sub(r"[^a-z &]", "", line.lower()).strip()
    if not clean or len(clean) > 40:
        return None
    for key, words in SECTION_HEADINGS.items():
        if clean in words:
            return key
    return None


def _find(pattern, text):
    m = re.search(pattern, text, re.I)
    return m.group(0).strip() if m else ""


def detect_skills(text: str, taxonomy: dict) -> list:
    extra = ["Statutory Audit", "Internal Audit", "Tax Audit", "GST Audit", "Ind AS", "IFRS", "US GAAP",
             "Companies Act, 2013", "IPO Reporting", "DRHP", "RHP", "Tally", "SAP", "Excel", "Financial Statements",
             "Reconciliations", "GST", "TDS", "Income Tax", "Bank Audit", "Concurrent Audit", "Stock Audit",
             "Internal Controls", "SOX", "Due Diligence", "MIS Reporting", "Power BI", "Oracle", "QuickBooks"]
    found = []
    for s in extra:
        if re.search(r"(?<![a-z])" + re.escape(s.lower()) + r"s?(?![a-z])", text.lower()) and s not in found:
            found.append(s)
    return found


SOFTWARE = ["Tally Prime", "Tally ERP 9", "Tally", "MS Excel", "Advanced Excel", "MS Word", "MS PowerPoint",
            "MS Office", "SAP FICO", "SAP", "Oracle", "NetSuite", "QuickBooks", "Zoho Books", "Busy", "Marg",
            "Power BI", "Caseware", "IDEA", "ACL", "Python", "SQL"]


def detect_software(text: str) -> list:
    found = []
    for s in SOFTWARE:
        pat = r"(?<![A-Za-z])" + re.escape(s) + r"(?![A-Za-z])"
        if re.search(pat, text, re.I if len(s) > 4 else 0):
            if not any(s in f for f in found):          # "Tally" not again after "Tally Prime"
                found.append(s)
    if not any("Excel" in f for f in found) and re.search(r"\bexcel\b", text, re.I):
        found.append("MS Excel")
    return found


_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_DATE = rf"(?:{_MONTH}\s*,?\s*\d{{4}}|\d{{1,2}}[/-]\d{{4}}|\d{{4}})"
_RANGE = re.compile(rf"({_DATE})\s*(?:-|–|—|to)\s*({_DATE}|present|current|till date|ongoing)", re.I)


def _mm_yyyy(s):
    m = re.match(rf"({_MONTH})\s*,?\s*(\d{{4}})", s.strip(), re.I)
    if m:
        months = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
        return f"{months.index(m.group(1)[:3].lower()) + 1:02d}-{m.group(2)}"
    m = re.match(r"(\d{1,2})[/-](\d{4})", s.strip())
    if m:
        return f"{int(m.group(1)):02d}-{m.group(2)}"
    return s.strip().title() if s.strip().lower() in ("present", "current", "till date", "ongoing") else s.strip()


_ROLE_WORD = re.compile(r"\b(accountant|assistant|executive|auditor|associate|analyst|manager|trainee|intern|officer|"
                        r"article|consultant|senior|semi[- ]qualified|controller|head|lead|clerk|bookkeeper)\b", re.I)


def detect_experience(section: str) -> list:
    """Each job starts at a line holding a date range ('June 2024 - Present').
    Organisation = text before the dates; designation only if written on its own line - never guessed."""
    jobs, cur = [], None
    for raw in section.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _RANGE.search(line)
        if m:
            head = re.sub(r"^[^\w(]+", "", line[:m.start()]).strip(" ,|-–")
            cur = {"organisation": head, "designation": "", "from": _mm_yyyy(m.group(1)),
                   "to": _mm_yyyy(m.group(2)), "responsibilities": []}
            jobs.append(cur)
            continue
        if cur is None:
            continue
        if re.match(r"^[•\-\*▪●◦❑➢✓]", line):
            cur["responsibilities"].append(re.sub(r"^[^\w(]+", "", line))
        elif cur["responsibilities"]:
            cur["responsibilities"][-1] += " " + line            # wrapped bullet line
        elif not cur["organisation"]:
            cur["organisation"] = line
        elif (not cur["designation"] and len(line.split()) <= 6 and "," not in line and _ROLE_WORD.search(line)):
            cur["designation"] = line
        else:
            cur["responsibilities"].append(line)
    for j in jobs:
        j["responsibilities"] = "\n".join(j["responsibilities"])
    return jobs


def detect_qualifications(text: str) -> list:
    out = []
    for name, pat in QUALIFICATION_PATTERNS:
        if re.search(pat, text, re.I):
            entry = {"name": name, "status": "", "details": "", "year": "", "source": "resume (please confirm status)"}
            line = next((l for l in text.splitlines() if re.search(pat, l, re.I)), "")
            st = re.search(r"\b(candidate|pursuing|qualified|cleared|appeared|final|inter(?:mediate)?)\b", line, re.I)
            if st and name not in ("CA Inter", "CA Foundation"):
                entry["status"] = st.group(1).title()
            yrs = re.findall(r"\b(?:19|20)\d{2}\b", line)
            entry["year"] = yrs[-1] if yrs else ""
            if name == "US CPA":
                cleared = [s for s in CPA_SECTIONS if re.search(CPA_SECTION_WORDS[s], text, re.I)]
                entry["details"] = ("Sections mentioned in resume: " + ", ".join(cleared)) if cleared else ""
                if cleared and entry["status"] == "Cleared":
                    entry["status"] = "Sections cleared"
                if re.search(r"AICPA|american institute of certified public", text, re.I) and not entry["details"]:
                    entry["details"] = "AICPA"
            out.append(entry)
    # CA Inter text also matches nothing else; avoid listing "CA" if only CA Inter/Foundation is present
    names = [q["name"] for q in out]
    if "CA" in names and ("CA Inter" in names or "CA Foundation" in names):
        if not re.search(r"chartered accountant|qualified CA|\bACA\b|\bFCA\b", text, re.I):
            out = [q for q in out if q["name"] != "CA"]
    return out


_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")


def _split_spec_inst(rest: str, next_line: str):
    """rest = text after the degree name on the same line. Returns (specialisation, institution)."""
    rest = rest.strip(" ,|-–:")
    spec, inst = "", ""
    if rest.startswith("("):
        if ")" in rest:
            spec, inst = rest[1:rest.index(")")], rest[rest.index(")") + 1:]
        else:                                    # "(Accounting & University of Mumbai 2023" + "Finance)"
            body = rest[1:]
            hint = INSTITUTION_HINT.search(body)
            cut = hint.start() if hint else len(body)
            # institution usually starts a word or two before the hint word (e.g. "Tilak Maharashtra Vidyapeeth")
            words = body[:cut].split()
            spec_words = words
            if "&" in words:
                k = words.index("&") + 1
                spec_words, inst_prefix = words[:k], words[k:]
            else:
                inst_prefix = []
            spec = " ".join(spec_words)
            inst = " ".join(inst_prefix) + " " + body[cut:]
            m = re.match(r"\s*([^()]*?)\)", next_line or "")
            if m:
                spec = (spec + " " + m.group(1)).strip()
    else:
        inst = rest
    inst = _YEAR.sub("", inst)
    inst = re.sub(r"\b\d{1,3}(\.\d+)?\s*(%|cgpa|gpa)\b.*$", "", inst, flags=re.I)
    return " ".join(spec.split()).strip(" ,&"), " ".join(inst.split()).strip(" ,|-–()")


def detect_degrees(text: str) -> list:
    out = []
    lines = text.splitlines()
    for name, pat in DEGREE_PATTERNS + SCHOOL_PATTERNS:
        idx = next((i for i, l in enumerate(lines) if re.search(pat, l, re.I)), None)
        if idx is None:
            continue
        line = lines[idx]
        m = re.search(pat, line, re.I)
        nxt = lines[idx + 1] if idx + 1 < len(lines) else ""
        spec, inst = _split_spec_inst(line[m.end():], nxt)
        if not INSTITUTION_HINT.search(inst) and len(inst.split()) > 8:
            inst = ""                             # not clearly an institution - leave for the user
        years = _YEAR.findall(line)
        score = re.search(r"\b\d{1,3}(\.\d+)?\s*%|\b\d(\.\d+)?\s*(cgpa|gpa)\b", line, re.I)
        out.append({"degree": name, "specialisation": spec, "institution": inst,
                    "year": years[-1] if years else "", "score": score.group(0) if score else "",
                    "line_in_resume": line.strip(), "source": "resume (please check)"})
    return out


def build_draft_profile(path: Path, template: dict, taxonomy: dict) -> tuple:
    """Return (draft_profile, extracted_text, sections).
    CVs made from the standard template are read exactly; any other CV is read heuristically."""
    import copy
    from .cv_template import is_template_docx, parse_template_docx
    if is_template_docx(path):
        return parse_template_docx(path, template)
    text = extract_text(path)
    sections = split_sections(text)
    draft = copy.deepcopy(template)
    draft["confirmed"] = False
    draft["active_resume"] = Path(path).name
    draft["cv_source"] = "free-format CV (please check every field)"

    header = sections.get("header", "")
    first_line = next((l for l in header.splitlines() if l.strip()), "")
    if first_line and len(first_line.split()) <= 5 and not re.search(r"@|\d", first_line):
        draft["personal"]["full_name"] = first_line.title() if first_line.isupper() else first_line
    draft["personal"]["email"] = _find(r"[\w.+-]+@[\w-]+\.[\w.-]+", text)
    phone = _find(r"(\+?\d{1,3}[\s-]?)?\(?\d{3,5}\)?[\s-]?\d{3,5}[\s-]?\d{3,5}", text)
    draft["personal"]["phone"] = phone if len(re.sub(r"\D", "", phone)) >= 10 else ""
    draft["personal"]["linkedin_url"] = _find(r"(https?://)?(www\.)?linkedin\.com/in/[\w-]+/?", text)

    draft["summary"] = " ".join(sections.get("summary", "").split())
    draft["education"] = detect_degrees(sections.get("education", "") or text)
    draft["qualifications"] = detect_qualifications(text)
    draft["skills"] = detect_skills(text, taxonomy)
    draft["software"] = detect_software(text)
    draft["experience"] = detect_experience(sections.get("experience", ""))
    draft["achievements"] = [re.sub(r"^[^\w(“\"]+", "", l).strip()
                             for l in re.split(r"\n(?=\s*[•\-\*▪●❑➢])", sections.get("achievements", ""))
                             if l.strip()]
    draft["achievements"] = [" ".join(a.split()) for a in draft["achievements"]]

    yrs = re.search(r"(\d+(\.\d+)?)\s*\+?\s*(plus\s*)?(years|yrs)\s*(of)?\s*(experience|exp)", text, re.I)
    draft["total_experience_years"] = float(yrs.group(1)) if yrs else None
    draft["experience_section_text"] = sections.get("experience", "")
    return draft, text, sections
