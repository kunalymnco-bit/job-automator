"""Standard CV template (DOCX) and its exact parser.

Any candidate's CV (fresher, experienced, CA / CMA / CFA / B.Com ...) can be typed into this
template. A CV in this format is read field by field, with no guessing, so every platform form
can be filled from it.

    python -m core.cv_template          -> (re)creates templates/CV_Template.docx
"""
import copy
import re
from pathlib import Path

TEMPLATE_MARKER = "JAA-CV-TEMPLATE-v1"
TEMPLATE_FILE = Path(__file__).resolve().parent.parent / "templates" / "CV_Template.docx"

NAVY = "1F3864"
LIGHT = "D9E2F3"
GREY = "808080"

# ---- section definitions (heading text is what the parser looks for) ----
PERSONAL_FIELDS = [
    ("Full Name", "full_name", "e.g. Priya Sharma"),
    ("Email", "email", "e.g. priya.sharma@email.com"),
    ("Mobile", "phone", "e.g. +91 98XXXXXXXX"),
    ("Current City", "current_city", "e.g. Mumbai"),
    ("LinkedIn URL (optional)", "linkedin_url", "e.g. linkedin.com/in/priya-sharma"),
    ("Date of Birth (optional)", "date_of_birth", "e.g. 15-08-2002"),
]
EDU_COLS = ["Qualification", "Specialisation", "College / University / Board", "Year", "% / CGPA"]
EDU_EXAMPLE = ["e.g. B.Com", "Accounting & Finance", "Mumbai University", "2024", "72%"]
QUAL_COLS = ["Course", "Status", "Details", "Year"]
QUAL_EXAMPLE = ["e.g. CA Inter", "Both groups cleared", "ICAI", "2023"]
EXP_COLS = ["Organisation", "Designation / Role", "From (MM-YYYY)", "To (MM-YYYY or Present)", "Key Responsibilities"]
EXP_EXAMPLE = ["e.g. ABC & Co., Chartered Accountants", "Article Assistant", "01-2023", "Present",
               "Statutory audit of manufacturing clients; vouching; GST and TDS returns"]
PREF_FIELDS = [
    ("Preferred Location(s)", "preferred_locations", "e.g. Mumbai"),
    ("Current CTC (Rs per year, 0 for fresher)", "current_ctc_annual_inr", "e.g. 0"),
    ("Expected CTC (Rs per year)", "expected_ctc_annual_inr", "e.g. 400000"),
    ("Minimum acceptable CTC (Rs per year)", "minimum_ctc_annual_inr", "e.g. 360000"),
    ("Notice Period (days, 0 = immediate)", "notice_period_days", "e.g. 0"),
    ("Willing to Relocate (Yes / No)", "willing_to_relocate", "e.g. No"),
    ("Authorised to work in India (Yes / No)", "authorized_to_work_in_india", "e.g. Yes"),
]

H_PERSONAL = "PERSONAL DETAILS"
H_SUMMARY = "PROFILE SUMMARY"
H_EDU = "EDUCATION"
H_QUAL = "PROFESSIONAL QUALIFICATIONS"
H_EXP = "WORK EXPERIENCE / ARTICLESHIP / INTERNSHIP"
H_TOTAL = "TOTAL EXPERIENCE"
H_SKILLS = "SKILLS"
H_SOFTWARE = "SOFTWARE / TOOLS"
H_ACH = "ACHIEVEMENTS (optional)"
H_PREF = "JOB PREFERENCES"
H_DECL = "DECLARATION"


# =====================================================================
# Building the template
# =====================================================================
def _shade(cell, hex_color):
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_color)
    tcPr.append(shd)


def _cell_text(cell, text, bold=False, color=None, italic=False, size=9.5):
    from docx.shared import Pt, RGBColor
    cell.text = ""
    p = cell.paragraphs[0]
    p.paragraph_format.space_after = Pt(0)
    r = p.add_run(text)
    r.font.size = Pt(size)
    r.bold = bold
    r.italic = italic
    if color:
        r.font.color.rgb = RGBColor.from_string(color)


def _heading(doc, text):
    from docx.shared import Pt, RGBColor
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(10)
    p.paragraph_format.space_after = Pt(3)
    r = p.add_run(text)
    r.bold = True
    r.font.size = Pt(11.5)
    r.font.color.rgb = RGBColor.from_string(NAVY)
    # bottom border line
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    pPr = p._p.get_or_add_pPr()
    bdr = OxmlElement("w:pBdr")
    b = OxmlElement("w:bottom")
    for k, v in (("val", "single"), ("sz", "8"), ("space", "1"), ("color", NAVY)):
        b.set(qn(f"w:{k}"), v)
    bdr.append(b)
    pPr.append(bdr)
    return p


def _hint(doc, text):
    from docx.shared import Pt, RGBColor
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(2)
    r = p.add_run(text)
    r.italic = True
    r.font.size = Pt(8.5)
    r.font.color.rgb = RGBColor.from_string(GREY)


def _kv_table(doc, fields):
    from docx.shared import Cm
    t = doc.add_table(rows=0, cols=2)
    t.style = "Table Grid"
    for label, _key, example in fields:
        row = t.add_row().cells
        _cell_text(row[0], label, bold=True)
        _shade(row[0], LIGHT)
        _cell_text(row[1], example, italic=True, color=GREY)
        row[0].width, row[1].width = Cm(6.0), Cm(11.0)
    return t


def _grid_table(doc, cols, example, blank_rows=3, widths_cm=None):
    from docx.shared import Cm
    t = doc.add_table(rows=1, cols=len(cols))
    t.style = "Table Grid"
    t.autofit = False
    for i, c in enumerate(cols):
        _cell_text(t.rows[0].cells[i], c, bold=True, color="FFFFFF")
        _shade(t.rows[0].cells[i], NAVY)
    ex = t.add_row().cells
    for i, v in enumerate(example):
        _cell_text(ex[i], v, italic=True, color=GREY)
    for _ in range(blank_rows):
        t.add_row()
    if widths_cm:
        for i, w in enumerate(widths_cm):
            t.columns[i].width = Cm(w)
            for c in t.columns[i].cells:
                c.width = Cm(w)
    return t


def _box(doc, example):
    t = doc.add_table(rows=1, cols=1)
    t.style = "Table Grid"
    _cell_text(t.rows[0].cells[0], example, italic=True, color=GREY)
    return t


def build_template(out_path: Path = TEMPLATE_FILE) -> Path:
    import docx
    from docx.shared import Pt, Cm, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    doc = docx.Document()
    sec = doc.sections[0]
    sec.page_height, sec.page_width = Cm(29.7), Cm(21.0)
    sec.left_margin = sec.right_margin = Cm(2.0)
    sec.top_margin = sec.bottom_margin = Cm(1.6)
    st = doc.styles["Normal"]
    st.font.name = "Calibri"
    st.font.size = Pt(10)

    doc.core_properties.title = "Curriculum Vitae"
    doc.core_properties.keywords = TEMPLATE_MARKER
    doc.core_properties.subject = "Standard CV template - Job Application Automator"

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("CURRICULUM VITAE")
    r.bold = True
    r.font.size = Pt(18)
    r.font.color.rgb = RGBColor.from_string(NAVY)
    _hint(doc, "How to fill: replace every grey 'e.g.' line with the candidate's real details (or delete it). "
               "Add table rows if needed. Leave a row blank if it does not apply - never write anything that is not true. "
               "Do not rename the section headings; the dashboard reads the CV by these headings. "
               "Save as .docx and upload.")

    _heading(doc, H_PERSONAL)
    _kv_table(doc, PERSONAL_FIELDS)

    _heading(doc, H_SUMMARY)
    _box(doc, "e.g. B.Com graduate with working knowledge of Tally Prime, MS Excel and GST returns, looking for an "
              "accounts or audit role in Mumbai.")

    _heading(doc, H_EDU)
    _hint(doc, "Highest qualification first. Include 12th / 10th for freshers.")
    _grid_table(doc, EDU_COLS, EDU_EXAMPLE, blank_rows=3, widths_cm=[3.0, 3.6, 6.4, 1.8, 2.2])

    _heading(doc, H_QUAL)
    _hint(doc, "CA / CA Inter / CA Foundation / CMA / CS / CFA / ACCA / US CPA / CIA / CISA / Tally or GST certificates. "
               "Status examples: Qualified, Final pending, Inter both groups cleared, Level 1 cleared, Pursuing.")
    _grid_table(doc, QUAL_COLS, QUAL_EXAMPLE, blank_rows=3, widths_cm=[3.4, 4.0, 7.6, 2.0])

    _heading(doc, H_EXP)
    _hint(doc, "Latest first. Freshers can list internships or articleship; leave empty if none.")
    _grid_table(doc, EXP_COLS, EXP_EXAMPLE, blank_rows=3, widths_cm=[3.2, 2.8, 1.9, 2.1, 7.0])

    _heading(doc, H_TOTAL)
    _kv_table(doc, [("Total experience in years (0 for fresher)", "total", "e.g. 1.5")])

    _heading(doc, H_SKILLS)
    _hint(doc, "Separate with commas.")
    _box(doc, "e.g. Bank reconciliation, GST returns (GSTR-1, GSTR-3B), TDS, Voucher entry, Statutory audit, Vouching")

    _heading(doc, H_SOFTWARE)
    _box(doc, "e.g. Tally Prime, MS Excel (VLOOKUP, Pivot tables), SAP, Zoho Books")

    _heading(doc, H_ACH)
    _hint(doc, "One per line.")
    _box(doc, "e.g. Cleared CA Inter in first attempt")

    _heading(doc, H_PREF)
    _hint(doc, "Used only to answer job-form questions. Leave blank if unsure - you will be asked in the dashboard.")
    _kv_table(doc, PREF_FIELDS)

    _heading(doc, H_DECL)
    p = doc.add_paragraph("I hereby declare that the information given above is true to the best of my knowledge.")
    p.runs[0].font.size = Pt(9.5)
    doc.add_paragraph("Place: ____________        Date: ____________        Signature: ____________")

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_path))
    return out_path


def ensure_template() -> Path:
    if not TEMPLATE_FILE.exists():
        build_template()
    return TEMPLATE_FILE


# =====================================================================
# Exact parser
# =====================================================================
_PLACEHOLDER = re.compile(r"^\s*(e\.?g\.?\b|\[.*\]\s*$|_+\s*$)", re.I)


def _val(text: str) -> str:
    t = (text or "").strip()
    return "" if (not t or _PLACEHOLDER.match(t)) else t


def is_template_docx(path) -> bool:
    path = Path(path)
    if path.suffix.lower() != ".docx":
        return False
    try:
        import docx
        d = docx.Document(str(path))
    except Exception:
        return False
    if TEMPLATE_MARKER in (d.core_properties.keywords or ""):
        return True
    heads = {p.text.strip().upper() for p in d.paragraphs}
    return {H_PERSONAL, H_EDU, H_EXP}.issubset(heads)   # template re-saved by Word / Google Docs


def _blocks(doc):
    """Yield ('heading', text) and ('table', Table) in document order."""
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    known = {h.upper(): h for h in (H_PERSONAL, H_SUMMARY, H_EDU, H_QUAL, H_EXP, H_TOTAL, H_SKILLS,
                                    H_SOFTWARE, H_ACH, H_PREF, H_DECL)}
    for el in doc.element.body.iterchildren():
        tag = el.tag.split("}")[-1]
        if tag == "p":
            t = Paragraph(el, doc).text.strip().upper()
            if t in known:
                yield "heading", known[t]
        elif tag == "tbl":
            yield "table", Table(el, doc)


def _kv(table):
    out = {}
    for row in table.rows:
        cells = row.cells
        if len(cells) >= 2:
            out[cells[0].text.strip()] = _val(cells[1].text)
    return out


def _grid(table, ncols):
    rows = []
    for row in list(table.rows)[1:]:
        vals = [c.text.strip() for c in row.cells][:ncols]
        if any(_PLACEHOLDER.match(v) for v in vals if v):
            continue                        # example row
        vals = [_val(v) for v in vals]
        if any(vals):
            rows.append(vals + [""] * (ncols - len(vals)))
    return rows


def _box_text(table):
    return "\n".join(_val(l) for l in table.rows[0].cells[0].text.splitlines() if _val(l)).strip()


def _num(s):
    s = re.sub(r"[,\s₹]|rs\.?|inr|lpa|per annum|p\.a\.", "", (s or "").lower())
    try:
        return float(s) if "." in s else int(s)
    except ValueError:
        return None


def _yes_no(s):
    s = (s or "").strip().lower()
    return True if s in ("yes", "y", "true") else False if s in ("no", "n", "false") else None


def _csv_list(s):
    return [x.strip() for x in re.split(r"[,\n;]", s or "") if x.strip()]


def parse_template_docx(path, template: dict) -> tuple:
    """Exact field-by-field read of a CV made from the template. Returns (draft, text, sections)."""
    import docx
    path = Path(path)
    d = docx.Document(str(path))
    draft = copy.deepcopy(template)
    draft["confirmed"] = False
    draft["active_resume"] = path.name
    draft["cv_source"] = "template"
    sections = {}
    current = None

    for kind, obj in _blocks(d):
        if kind == "heading":
            current = obj
            continue
        if current == H_PERSONAL:
            kv = _kv(obj)
            for label, key, _ in PERSONAL_FIELDS:
                draft["personal"][key] = kv.get(label, "")
            sections["personal"] = "\n".join(f"{k}: {v}" for k, v in kv.items())
        elif current == H_SUMMARY:
            draft["summary"] = _box_text(obj)
            sections["summary"] = draft["summary"]
        elif current == H_EDU:
            draft["education"] = [{"degree": r[0], "specialisation": r[1], "institution": r[2], "year": r[3],
                                   "score": r[4], "source": "CV template"} for r in _grid(obj, 5)]
            sections["education"] = "\n".join(" | ".join(r) for r in _grid(obj, 5))
        elif current == H_QUAL:
            draft["qualifications"] = [{"name": r[0], "status": r[1], "details": r[2], "year": r[3],
                                        "source": "CV template"} for r in _grid(obj, 4)]
            sections["qualifications"] = "\n".join(" | ".join(r) for r in _grid(obj, 4))
        elif current == H_EXP:
            draft["experience"] = [{"organisation": r[0], "designation": r[1], "from": r[2], "to": r[3],
                                    "responsibilities": r[4]} for r in _grid(obj, 5)]
            sections["experience"] = "\n".join(" | ".join(r) for r in _grid(obj, 5))
        elif current == H_TOTAL:
            v = next(iter(_kv(obj).values()), "")
            draft["total_experience_years"] = _num(v) if v else None
        elif current == H_SKILLS:
            draft["skills"] = _csv_list(_box_text(obj))
            sections["skills"] = _box_text(obj)
        elif current == H_SOFTWARE:
            draft["software"] = _csv_list(_box_text(obj))
        elif current == H_ACH:
            draft["achievements"] = [l for l in _box_text(obj).splitlines() if l.strip()]
        elif current == H_PREF:
            kv = _kv(obj)
            ind = draft["india_application_fields"]
            for label, key, _ in PREF_FIELDS:
                v = kv.get(label, "")
                if key == "preferred_locations":
                    ind[key] = v
                elif key in ("current_ctc_annual_inr", "expected_ctc_annual_inr", "minimum_ctc_annual_inr",
                             "notice_period_days"):
                    ind[key] = _num(v) if v else None
                elif key == "willing_to_relocate":
                    ind[key] = _yes_no(v)
                elif key == "authorized_to_work_in_india":
                    draft["work_authorization"]["India"][key] = _yes_no(v)
        current = current if kind == "heading" else current

    text = "\n\n".join(f"[{k}]\n{v}" for k, v in sections.items() if v)
    return draft, text, sections


def fill_template_for_test(out_path, data: dict) -> Path:
    """Fill a fresh template programmatically (used by tests)."""
    import docx
    build_template(out_path)
    d = docx.Document(str(out_path))
    current = None
    for kind, obj in _blocks(d):
        if kind == "heading":
            current = obj
            continue
        if current == H_PERSONAL:
            for row in obj.rows:
                for label, key, _ in PERSONAL_FIELDS:
                    if row.cells[0].text.strip() == label:
                        row.cells[1].text = data.get(key, "")
        elif current in (H_EDU, H_QUAL, H_EXP):
            key = {H_EDU: "education", H_QUAL: "qualifications", H_EXP: "experience"}[current]
            for i, vals in enumerate(data.get(key, [])):
                for j, v in enumerate(vals):
                    obj.rows[2 + i].cells[j].text = v
        elif current == H_TOTAL:
            obj.rows[0].cells[1].text = str(data.get("total", ""))
        elif current == H_SKILLS:
            obj.rows[0].cells[0].text = data.get("skills", "")
        elif current == H_SOFTWARE:
            obj.rows[0].cells[0].text = data.get("software", "")
        elif current == H_ACH:
            obj.rows[0].cells[0].text = data.get("achievements", "")
        elif current == H_PREF:
            for row in obj.rows:
                for label, key, _ in PREF_FIELDS:
                    if row.cells[0].text.strip() == label and key in data:
                        row.cells[1].text = str(data[key])
    d.save(str(out_path))
    return Path(out_path)


if __name__ == "__main__":
    print("Template written to", build_template())
