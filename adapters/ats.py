"""Company career sites: Greenhouse, Lever, Workday.

Search uses each site's public job-board feed (the same data their career pages show), for the
company career pages you list in the dashboard (Preferences -> Company career pages).
Applications are filled on the real page and stop before Submit.
"""
import asyncio
import re
from urllib.parse import urlparse, quote

from core import form_filler as FF
from .base import PlatformAdapter, SUBMITTED_TEXT
from .common import http_json, strip_html, show_banner, wait_for_login

ROLE_HINT = re.compile(r"audit|account|financ|tax|gst|ledger|reconcil|controller|bookkeep|assurance|compliance|"
                       r"payable|receivable|treasury|mis\b|costing|ind as|ifrs|sox|internal control|article", re.I)


def parse_board(url):
    """Career-page URL -> (platform, id dict) or None."""
    u = url.strip()
    if not u:
        return None
    if not u.startswith("http"):
        u = "https://" + u
    p = urlparse(u)
    host, parts = p.netloc.lower(), [x for x in p.path.split("/") if x]
    if "greenhouse.io" in host:
        tok = (parts[0] if parts and parts[0] not in ("embed", "v1") else (dict(x.split("=") for x in p.query.split("&") if "=" in x).get("for")))
        return ("Greenhouse", {"token": tok}) if tok else None
    if "lever.co" in host:
        return ("Lever", {"company": parts[0]}) if parts else None
    if "myworkdayjobs.com" in host or "myworkdaysite.com" in host:
        tenant = host.split(".")[0]
        site = [x for x in parts if not re.fullmatch(r"[a-z]{2}-[A-Z]{2}", x)]
        return ("Workday", {"host": p.netloc, "tenant": tenant, "site": site[0]}) if site else None
    return None


async def _in_thread(fn, *a):
    return await asyncio.get_running_loop().run_in_executor(None, fn, *a)


class _ATSBase(PlatformAdapter):
    needs_browser = False
    form_selectors = ["#application-form", "#application_form", "form#application", "form[action*='apply']",
                      "form:has(input[type=file])", "form"]

    def boards(self, env):
        out = []
        for url in env["prefs"].get("company_career_pages", []):
            b = parse_board(url)
            if b and b[0] == self.name:
                out.append(b[1])
        return out

    async def _form_root(self, page):
        for frame in [page.main_frame] + [f for f in page.frames if "greenhouse" in (f.url or "") or "lever" in (f.url or "")]:
            for sel in self.form_selectors:
                loc = frame.locator(sel).first
                try:
                    if await loc.count() and await loc.locator("input, textarea, select").count():
                        return loc
                except Exception:
                    continue
        return None

    async def prepare(self, page, job, env):
        await page.goto(job["job_url"], wait_until="domcontentloaded")
        await asyncio.sleep(2)
        root = await self._form_root(page)
        if root is None or await root.locator("input[type=file]").count() == 0:
            # some boards show the form only after "Apply"
            for txt in ("Apply for this job", "Apply for this Job", "Apply now", "Apply"):
                btn = page.get_by_role("button", name=re.compile(rf"^{txt}$", re.I)).first
                lnk = page.get_by_role("link", name=re.compile(rf"^{txt}$", re.I)).first
                for b in (btn, lnk):
                    try:
                        if await b.count() and await b.is_visible():
                            from core.safety import safe_click
                            await safe_click(b, allow=(r"^apply( now| for this job)?$",))
                            await asyncio.sleep(2.5)
                            break
                    except Exception:
                        continue
        res = await FF.run_form(page, lambda: self._form_root(page), env["profile"], job, env["cv_path"], self.name, env["log"])
        return res

    async def is_submitted(self, page):
        try:
            return bool(re.search(SUBMITTED_TEXT, (await page.inner_text("body"))[:5000], re.I))
        except Exception:
            return False


class Greenhouse(_ATSBase):
    name = "Greenhouse"

    async def search(self, ctx, queries, env):
        jobs = []
        for b in self.boards(env):
            try:
                data = await _in_thread(http_json, f"https://boards-api.greenhouse.io/v1/boards/{b['token']}/jobs?content=true")
            except Exception as e:
                env["log"](f"Greenhouse '{b['token']}': could not read job board ({e})", "WARN")
                continue
            for j in data.get("jobs", []):
                if not ROLE_HINT.search(j.get("title", "")):
                    continue
                jobs.append(dict(title=j.get("title", ""), company=data.get("name") or b["token"],
                                 location=(j.get("location") or {}).get("name", ""), job_url=j.get("absolute_url", ""),
                                 description=strip_html(j.get("content", "")), posted=(j.get("updated_at") or "")[:10],
                                 requisition_id=str(j.get("requisition_id") or j.get("id") or ""), salary_text="",
                                 experience_text="", platform=self.name))
        return jobs


class Lever(_ATSBase):
    name = "Lever"
    form_selectors = ["form#application-form", "form[action*='apply']", "form:has(input[type=file])", "form"]

    async def search(self, ctx, queries, env):
        jobs = []
        for b in self.boards(env):
            try:
                data = await _in_thread(http_json, f"https://api.lever.co/v0/postings/{b['company']}?mode=json")
            except Exception as e:
                env["log"](f"Lever '{b['company']}': could not read job board ({e})", "WARN")
                continue
            for j in data:
                if not ROLE_HINT.search(j.get("text", "")):
                    continue
                cat = j.get("categories") or {}
                desc = (j.get("descriptionPlain") or "") + "\n" + "\n".join(
                    f"{l.get('text', '')}\n{strip_html(l.get('content', ''))}" for l in j.get("lists", []))
                jobs.append(dict(title=j.get("text", ""), company=b["company"], job_url=j.get("applyUrl") or j.get("hostedUrl"),
                                 location=cat.get("location") or ", ".join(cat.get("allLocations", []) or []),
                                 description=desc, posted=j.get("createdAt"), requisition_id=j.get("id", ""),
                                 salary_text="", experience_text="", platform=self.name))
        return jobs


class Workday(_ATSBase):
    name = "Workday"

    async def search(self, ctx, queries, env):
        jobs = []
        for b in self.boards(env):
            base = f"https://{b['host']}/wday/cxs/{b['tenant']}/{b['site']}"
            seen = set()
            for q in queries[:4]:
                try:
                    data = await _in_thread(http_json, base + "/jobs",
                                            {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": q})
                except Exception as e:
                    env["log"](f"Workday '{b['tenant']}': search failed ({e})", "WARN")
                    break
                for p in data.get("jobPostings", []):
                    path = p.get("externalPath")
                    if not path or path in seen or not ROLE_HINT.search(p.get("title", "")):
                        continue
                    seen.add(path)
                    loc = p.get("locationsText", "")
                    if not re.search(r"mumbai|thane|navi mumbai|maharashtra|india|locations", loc, re.I):
                        continue
                    try:
                        d = (await _in_thread(http_json, base + path)).get("jobPostingInfo", {})
                    except Exception:
                        d = {}
                    jobs.append(dict(title=p.get("title", ""), company=b["tenant"],
                                     location=d.get("location") or loc,
                                     job_url=d.get("externalUrl") or f"https://{b['host']}/{b['site']}{path}",
                                     description=strip_html(d.get("jobDescription", "")),
                                     posted=p.get("postedOn") or d.get("postedOn", ""),
                                     requisition_id=d.get("jobReqId") or (p.get("bulletFields") or [""])[0],
                                     salary_text="", experience_text="", platform=self.name))
        return jobs

    async def _form_root(self, page):
        return page

    async def prepare(self, page, job, env):
        from core.safety import safe_click
        await page.goto(job["job_url"], wait_until="domcontentloaded")
        await asyncio.sleep(3)
        for name in (r"^apply$", r"^apply manually$"):
            b = page.get_by_role("button", name=re.compile(name, re.I)).first
            if not await b.count():
                b = page.get_by_role("link", name=re.compile(name, re.I)).first
            try:
                if await b.count() and await b.is_visible():
                    await safe_click(b, allow=(r"^apply( manually)?$",))
                    await asyncio.sleep(3)
            except Exception:
                pass

        async def signed_in(p):
            return not await p.locator("input[type=password]:visible").count()
        if not await signed_in(page):
            env["log"]("Workday: sign in / create the candidate's account in the browser window (the tool waits).", "WARN")
            await show_banner(page, "Job Automator: please sign in or create an account for this company's Workday site.\n"
                                    "The form will be filled after you sign in.")
            for _ in range(120):
                await asyncio.sleep(5)
                if await signed_in(page):
                    break
            else:
                return {"state": "needs_input", "needs": ["Workday sign-in"], "notes": {}}
            await asyncio.sleep(3)
        return await FF.run_form(page, lambda: self._form_root(page), env["profile"], job, env["cv_path"], self.name,
                                 env["log"], max_steps=10)


ATS = {"Greenhouse": Greenhouse(), "Lever": Lever(), "Workday": Workday()}
