"""LinkedIn, Naukri, Indeed India, Foundit - driven in a real, visible browser window.

* The candidate logs in once per platform in that window (sessions are kept per candidate).
  The tool never types or stores passwords.
* Pace is slow and human-like; no stealth tricks. If a CAPTCHA / security check appears the run
  pauses until the user solves it.
* LinkedIn Easy Apply and Indeed Apply forms are filled up to the final button.
  Naukri and Foundit submit in ONE click on 'Apply', so for them the job is opened with the match
  notes ready and the user presses Apply; any follow-up questions are then pre-typed for the user.

Site layouts change often. Every selector has a text-based fallback, and anything the tool cannot
read or fill is left for the user rather than guessed.
"""
import asyncio
import re
from urllib.parse import quote, urljoin

from core import form_filler as FF
from core import answers as A
from core.safety import safe_click
from .base import PlatformAdapter, SUBMITTED_TEXT
from .common import (first_text, page_fallback, slow_scroll, wait_for_login, show_banner, experience_filter_linkedin,
                     ats_of, has_captcha, wait_for_user_captcha)
from .ats import ATS


async def _goto(page, url, log):
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
    except Exception as e:
        log(f"Could not open {url[:80]} ({type(e).__name__})", "WARN")
        return False
    await asyncio.sleep(2.5)
    if await has_captcha(page):
        return await wait_for_user_captcha(page, log)
    return True


async def _body_text(page, n=6000):
    try:
        return (await page.inner_text("body"))[:n]
    except Exception:
        return ""


# ====================================================================== LinkedIn
class LinkedIn(PlatformAdapter):
    name = "LinkedIn"
    apply_mode = "fill"
    login_url = "https://www.linkedin.com/login"

    @staticmethod
    def login_done(url):
        return "linkedin.com" in url and not re.search(r"/login|/authwall|/signup|checkpoint|uas/|challenge", url)

    async def _logged_in(self, page):
        if not page.url.startswith("https://www.linkedin.com") or re.search(r"/login|/authwall|/signup|checkpoint|uas/", page.url):
            await _goto(page, "https://www.linkedin.com/feed/", lambda *a: None)
        return not re.search(r"/login|/authwall|/signup|checkpoint|uas/", page.url) and "linkedin.com" in page.url

    async def ensure_ready(self, ctx, log):
        page = await ctx.new_page()
        ok = await wait_for_login(page, self._logged_in, "https://www.linkedin.com/login", self.name, log)
        await page.close()
        return ok

    async def search(self, ctx, queries, env):
        log, prefs, profile = env["log"], env["prefs"], env["profile"]
        page = await ctx.new_page()
        ids = []
        fe = experience_filter_linkedin(profile.get("total_experience_years"))
        for q in queries:
            for pg in range(env["max_pages"]):
                if env["stopped"]():
                    break
                url = (f"https://www.linkedin.com/jobs/search/?keywords={quote(q)}&location={quote('Mumbai, Maharashtra, India')}"
                       f"&f_TPR=r{env['days'] * 86400}&f_E={quote(fe)}&f_JT=F&sortBy=DD&start={pg * 25}")
                if not await _goto(page, url, log):
                    break
                await slow_scroll(page, ".jobs-search-results-list, .scaffold-layout__list, .scaffold-layout__list-container", 7)
                found = await page.evaluate(r"""() => {
                    const s = new Set();
                    document.querySelectorAll('[data-job-id],[data-occludable-job-id]').forEach(e =>
                        s.add(e.getAttribute('data-job-id') || e.getAttribute('data-occludable-job-id')));
                    document.querySelectorAll('a[href*="/jobs/view/"]').forEach(a => {
                        const m = a.href.match(/\/jobs\/view\/(\d+)/); if (m) s.add(m[1]); });
                    return [...s].filter(x => x && /^\d+$/.test(x)); }""")
                new = [i for i in found if i not in ids]
                ids += new
                log(f"LinkedIn '{q}' page {pg + 1}: {len(found)} jobs")
                if len(found) < 10 or not new:
                    break
        jobs = []
        for jid in ids:
            if env["stopped"]():
                break
            url = f"https://www.linkedin.com/jobs/view/{jid}/"
            if env["seen"](url):
                continue
            if not await _goto(page, url, log):
                continue
            await asyncio.sleep(1)
            try:
                more = page.locator("button[aria-label*='see more' i], button.jobs-description__footer-button").first
                if await more.count():
                    await more.click(timeout=2000)
            except Exception:
                pass
            fb = await page_fallback(page)
            title = await first_text(page, ["h1", ".job-details-jobs-unified-top-card__job-title", ".top-card-layout__title"]) or fb["title"]
            company = await first_text(page, [".job-details-jobs-unified-top-card__company-name a",
                                              ".job-details-jobs-unified-top-card__company-name", "a[href*='/company/']"])
            meta = await first_text(page, [".job-details-jobs-unified-top-card__tertiary-description-container",
                                           ".job-details-jobs-unified-top-card__primary-description-container",
                                           ".jobs-unified-top-card__subtitle-primary-grouping"])
            if not company and " | " in fb["doc_title"]:
                parts = fb["doc_title"].split(" | ")
                title, company = title or parts[0], parts[1] if len(parts) > 2 else ""
            bits = [b.strip() for b in re.split(r"·|·", meta) if b.strip()]
            location = bits[0] if bits else ""
            posted = next((b for b in bits if re.search(r"ago|just now|today", b, re.I)), "")
            desc = await first_text(page, ["#job-details", ".jobs-description__content", ".jobs-box__html-content",
                                           ".description__text"], timeout=3000) or fb["description"]
            apply_txt = await first_text(page, [".jobs-apply-button", ".jobs-s-apply button", "button.jobs-apply-button"])
            body = await _body_text(page, 3000)
            if re.search(r"\bApplied\b.{0,25}\bago\b", body):
                env["log"](f"LinkedIn: already applied - {title} @ {company}")
                continue
            salary = next((b for b in bits if "₹" in b), "") or (re.search(r"₹[\d,.\sLlakhK–-]+(?:/yr|/mo| a year| a month)?", body) or [""])[0]
            jobs.append(dict(title=title, company=company, location=location, posted=posted, description=desc,
                             salary_text=salary, experience_text="", job_url=url, requisition_id=jid, platform=self.name,
                             apply_type="easy" if "easy apply" in apply_txt.lower() else "external"))
            await asyncio.sleep(1.2)
        await page.close()
        return jobs

    async def _dialog(self, page):
        d = page.locator("div[role=dialog].jobs-easy-apply-modal, .jobs-easy-apply-modal, div[role=dialog]:has(form), div[role=dialog]").first
        return d if await d.count() else None

    async def prepare(self, page, job, env):
        log = env["log"]
        if not await _goto(page, job["job_url"], log):
            return {"state": "stuck", "needs": ["Job page did not open"], "notes": {}}
        btn = page.locator(".jobs-apply-button:visible, button.jobs-apply-button").first
        if not await btn.count():
            if re.search(r"no longer accepting|applications? (are )?closed", await _body_text(page), re.I):
                return {"state": "closed", "needs": ["No longer accepting applications"], "notes": {}}
            return {"state": "stuck", "needs": ["Apply button not found"], "notes": {}}
        label = (await btn.inner_text()).strip().lower()
        if "easy apply" in label:
            await safe_click(btn, allow=(r"^easy apply",))
            await asyncio.sleep(2.5)
            return await FF.run_form(page, lambda: self._dialog(page), env["profile"], job, env["cv_path"], self.name,
                                     log, max_steps=12)
        # external application on the company's own site
        try:
            async with page.context.expect_page(timeout=12000) as newp:
                await safe_click(btn, allow=(r"^apply",))
            ext = await newp.value
        except Exception:
            ext = page
        await asyncio.sleep(3)
        job["external_page"] = ext
        ats = ats_of(ext.url)
        log(f"LinkedIn job applies on company site ({ats or ext.url[:60]})")
        if ats:
            j2 = dict(job, job_url=ext.url)
            return await ATS[ats].prepare(ext, j2, env)
        res = await FF.run_form(ext, lambda: _page_root(ext), env["profile"], job, env["cv_path"], "Company website", log, max_steps=8)
        if res["state"] == "stuck":
            res["needs"] = ["Company website form - please complete it in this tab"]
        return res

    async def is_submitted(self, page):
        t = await _body_text(page, 8000)
        return bool(re.search(SUBMITTED_TEXT + r"|your application was sent", t, re.I))


async def _page_root(page):
    return page


async def _button_says_applied(page, selector):
    try:
        loc = page.locator(selector)
        for i in range(min(await loc.count(), 5)):
            if re.fullmatch(r"\s*applied\s*", (await loc.nth(i).inner_text()) or "", re.I):
                return True
    except Exception:
        pass
    return False


# ====================================================================== Naukri
class Naukri(PlatformAdapter):
    name = "Naukri"
    apply_mode = "open_only"         # Naukri's Apply button submits immediately - the user presses it
    login_url = "https://www.naukri.com/nlogin/login"

    @staticmethod
    def login_done(url):
        return "naukri.com" in url and not re.search(r"nlogin|login|otp|register", url)

    async def _logged_in(self, page):
        if "naukri.com" not in page.url or "login" in page.url:
            await _goto(page, "https://www.naukri.com/mnjuser/homepage", lambda *a: None)
        return "naukri.com" in page.url and not re.search(r"nlogin|login", page.url)

    async def ensure_ready(self, ctx, log):
        page = await ctx.new_page()
        ok = await wait_for_login(page, self._logged_in, "https://www.naukri.com/nlogin/login", self.name, log)
        await page.close()
        return ok

    async def search(self, ctx, queries, env):
        log = env["log"]
        page = await ctx.new_page()
        yrs = env["profile"].get("total_experience_years")
        exp = int(float(yrs)) if yrs not in (None, "") else 0
        cards = []
        for q in queries:
            slug = re.sub(r"[^a-z0-9]+", "-", q.lower()).strip("-")
            for pg in range(env["max_pages"]):
                if env["stopped"]():
                    break
                path = f"{slug}-jobs-in-mumbai" + (f"-{pg + 1}" if pg else "")
                url = f"https://www.naukri.com/{path}?k={quote(q)}&l=mumbai&experience={exp}&jobAge={min(env['days'], 30)}"
                if not await _goto(page, url, log):
                    break
                await slow_scroll(page, None, 5)
                got = await page.evaluate(r"""() => {
                  const T = e => e ? e.innerText.replace(/\s+/g,' ').trim() : '';
                  const out = [];
                  const tuples = document.querySelectorAll('.srp-jobtuple-wrapper, article.jobTuple, .cust-job-tuple, [data-job-id]');
                  tuples.forEach(t => {
                    const a = t.querySelector('a.title, a[href*="job-listings"]');
                    if (!a) return;
                    out.push({title: T(a), job_url: a.href.split('?')[0],
                      company: T(t.querySelector('.comp-name, a.subTitle, .companyInfo a')),
                      experience_text: T(t.querySelector('.expwdth, .exp, .experience')),
                      salary_text: T(t.querySelector('.sal, .sal-wrap, .salary')),
                      location: T(t.querySelector('.locWdth, .loc, .location')),
                      posted: T(t.querySelector('.job-post-day, .type, .fleft.postedDate'))});
                  });
                  if (!out.length) document.querySelectorAll('a[href*="job-listings"]').forEach(a =>
                      out.push({title: T(a), job_url: a.href.split('?')[0]}));
                  return out; }""")
                new = [c for c in got if c["job_url"] not in {x["job_url"] for x in cards}]
                cards += new
                log(f"Naukri '{q}' page {pg + 1}: {len(got)} jobs")
                if len(got) < 15 or not new:
                    break
        jobs = []
        for c in cards:
            if env["stopped"]():
                break
            if env["seen"](c["job_url"]):
                continue
            if not await _goto(page, c["job_url"], log):
                continue
            fb = await page_fallback(page)
            desc = await first_text(page, ["[class*='dang-inner-html']", "section[class*='job-desc']", ".job-desc",
                                           "[class*='JDC']"], timeout=3000) or fb["description"]
            top = await first_text(page, ["[class*='jhc__']", "[class*='jd-header']", "header"])
            company = c.get("company") or await first_text(page, ["[class*='jd-header-comp-name'] a", "[class*='comp-name']"])
            posted = c.get("posted") or (re.search(r"Posted:\s*([^|\n]+?)(?:Openings|Applicants|$)", top or "") or [None, ""])[1]
            body = await _body_text(page, 4000)
            if re.search(r"\bApplied\b", await first_text(page, ["#already-applied", "[class*='applied']", "#apply-button"])):
                env["log"](f"Naukri: already applied - {c['title']}")
                continue
            company_site = bool(await page.locator("#company-site-button, button:has-text('Apply on company site')").count())
            jobs.append(dict(title=c["title"] or fb["title"], company=company, location=c.get("location", ""),
                             posted=posted, description=desc, salary_text=c.get("salary_text", ""),
                             experience_text=c.get("experience_text", ""), job_url=c["job_url"],
                             requisition_id=(re.search(r"-(\d{9,})", c["job_url"]) or [None, ""])[1],
                             platform=self.name, apply_type="company_site" if company_site else "naukri"))
            await asyncio.sleep(1.0)
        await page.close()
        return jobs

    async def prepare(self, page, job, env):
        if not await _goto(page, job["job_url"], env["log"]):
            return {"state": "stuck", "needs": ["Job page did not open"], "notes": {}}
        if job.get("apply_type") == "company_site":
            b = page.locator("#company-site-button, button:has-text('Apply on company site')").first
            try:
                async with page.context.expect_page(timeout=12000) as newp:
                    await safe_click(b, allow=(r"apply on company site",))
                ext = await newp.value
                await asyncio.sleep(3)
                ats = ats_of(ext.url)
                if ats:
                    return await ATS[ats].prepare(ext, dict(job, job_url=ext.url), env)
                res = await FF.run_form(ext, lambda: _page_root(ext), env["profile"], job, env["cv_path"], "Company website",
                                        env["log"], max_steps=8)
                return res
            except Exception:
                pass
        await show_banner(page, f"Job Automator - ready for your review\n{job.get('title')} @ {job.get('company')}\n"
                                f"Match score: {job.get('score', '?')}\n\nNaukri applies in one click, so YOU press 'Apply'.\n"
                                "If Naukri then asks questions, the answers are typed in for you - check them and press Save.")
        return {"state": "ready_open", "needs": ["Press Apply yourself (Naukri submits in one click)"], "notes": {}}

    async def assist(self, page, job, env):
        """After the user pressed Apply: type answers into Naukri's question drawer (the user presses Save)."""
        try:
            drawer = page.locator("[class*='chatbot_Drawer'], [class*='chatbot'], [id*='chatbot']").first
            if not await drawer.count() or not await drawer.is_visible():
                return
            q = await first_text(drawer, ["li.botItem:last-of-type", "[class*='botMsg']:last-of-type", "[class*='bot']:last-child"])
            if not q or job.get("_last_q") == q:
                return
            chips = drawer.locator("[class*='chip'], .ssrc__radio-btn-container label, [class*='radio'] label")
            opts = [(await chips.nth(i).inner_text()).strip() for i in range(min(await chips.count(), 12))]
            ans = A.answer(q, "radio" if opts else "text", opts, env["profile"], job, self.name)
            job["_last_q"] = q
            if not ans:
                env["log"](f"Naukri question needs you: {q[:80]}", "WARN")
                return
            if opts:
                choice = A.pick_option(ans.value, opts)
                if choice:
                    await chips.nth(opts.index(choice)).click()
            else:
                box = drawer.locator("[contenteditable='true'], textarea, input[type=text]").first
                if await box.count():
                    await box.click()
                    await box.type(str(ans.value), delay=40)
            env["log"](f"Naukri question pre-filled: {q[:60]} -> {str(ans.value)[:40]} (press Save to send)")
        except Exception:
            return

    async def is_submitted(self, page):
        t = await _body_text(page, 6000)
        if re.search(SUBMITTED_TEXT + r"|you have (successfully )?applied", t, re.I):
            return True
        return await _button_says_applied(page, "#already-applied, #apply-button, [class*='apply-button']")


# ====================================================================== Indeed India
class IndeedIndia(PlatformAdapter):
    name = "Indeed India"
    apply_mode = "fill"
    HOST = "https://in.indeed.com"

    async def ensure_ready(self, ctx, log):
        page = await ctx.new_page()
        ok = await _goto(page, self.HOST, log)
        await page.close()
        return ok

    async def search(self, ctx, queries, env):
        log = env["log"]
        page = await ctx.new_page()
        cards = {}
        for q in queries:
            for pg in range(env["max_pages"]):
                if env["stopped"]():
                    break
                url = f"{self.HOST}/jobs?q={quote(q)}&l={quote('Mumbai, Maharashtra')}&fromage={min(env['days'], 14)}&sort=date&start={pg * 10}"
                if not await _goto(page, url, log):
                    break
                got = await page.evaluate(r"""() => {
                  const T = e => e ? e.innerText.replace(/\s+/g,' ').trim() : '';
                  return [...document.querySelectorAll('.job_seen_beacon, [data-jk]')].map(c => {
                    const a = c.querySelector('a[data-jk], h2 a') || c.closest('a[data-jk]');
                    const jk = (a && a.getAttribute('data-jk')) || c.getAttribute('data-jk');
                    return jk ? {jk, title: T(c.querySelector('h2 span[title], h2')),
                      company: T(c.querySelector('[data-testid="company-name"], .companyName')),
                      location: T(c.querySelector('[data-testid="text-location"], .companyLocation')),
                      salary_text: T(c.querySelector('[data-testid="attribute_snippet_testid"], .salary-snippet-container')),
                      posted: T(c.querySelector('[data-testid="myJobsStateDate"], .date'))} : null; }).filter(Boolean); }""")
                new = [c for c in got if c["jk"] not in cards]
                for c in new:
                    cards[c["jk"]] = c
                log(f"Indeed '{q}' page {pg + 1}: {len(got)} jobs")
                if len(got) < 10 or not new:
                    break
        jobs = []
        for jk, c in cards.items():
            if env["stopped"]():
                break
            url = f"{self.HOST}/viewjob?jk={jk}"
            if env["seen"](url):
                continue
            if not await _goto(page, url, log):
                continue
            desc = await first_text(page, ["#jobDescriptionText", "[data-testid='jobsearch-JobComponent-description']"], 3000) \
                or (await page_fallback(page))["description"]
            easy = bool(await page.locator("#indeedApplyButton, button:has-text('Apply now')").count())
            jobs.append(dict(title=c["title"], company=c["company"], location=c["location"],
                             salary_text=c["salary_text"] if "₹" in c["salary_text"] else "", posted=c["posted"],
                             description=desc, experience_text="", job_url=url, requisition_id=jk, platform=self.name,
                             apply_type="indeed" if easy else "external"))
            await asyncio.sleep(1.0)
        await page.close()
        return jobs

    async def prepare(self, page, job, env):
        log = env["log"]
        if not await _goto(page, job["job_url"], log):
            return {"state": "stuck", "needs": ["Job page did not open"], "notes": {}}
        btn = page.locator("#indeedApplyButton, button:has-text('Apply now')").first
        if job.get("apply_type") != "indeed" or not await btn.count():
            link = page.locator("a:has-text('Apply on company site'), button:has-text('Apply on company site')").first
            if await link.count():
                try:
                    async with page.context.expect_page(timeout=12000) as newp:
                        await safe_click(link, allow=(r"apply on company site",))
                    ext = await newp.value
                    await asyncio.sleep(3)
                    ats = ats_of(ext.url)
                    if ats:
                        return await ATS[ats].prepare(ext, dict(job, job_url=ext.url), env)
                    return await FF.run_form(ext, lambda: _page_root(ext), env["profile"], job, env["cv_path"],
                                             "Company website", log, max_steps=8)
                except Exception:
                    pass
            return {"state": "stuck", "needs": ["Apply on the company site from this page"], "notes": {}}
        target = page
        try:
            async with page.context.expect_page(timeout=8000) as newp:
                await safe_click(btn, allow=(r"^apply now$",))       # opens Indeed's form, does not submit
            target = await newp.value
        except Exception:
            pass
        await asyncio.sleep(3)
        if re.search(r"secure\.indeed\.com/auth|/account/login", target.url):
            async def ok(p):
                return not re.search(r"secure\.indeed\.com/auth|/account/login", p.url)
            if not await wait_for_login(target, ok, target.url, "Indeed", log):
                return {"state": "needs_input", "needs": ["Indeed login"], "notes": {}}
        job["external_page"] = target
        return await FF.run_form(target, lambda: _page_root(target), env["profile"], job, env["cv_path"], "Indeed",
                                 log, max_steps=12)

    async def is_submitted(self, page):
        return bool(re.search(SUBMITTED_TEXT + r"|your application has been submitted", await _body_text(page), re.I))


# ====================================================================== Foundit
class Foundit(PlatformAdapter):
    name = "Foundit"
    apply_mode = "open_only"
    login_url = "https://www.foundit.in/rio/login"

    @staticmethod
    def login_done(url):
        return "foundit.in" in url and not re.search(r"login|otp|register|signup", url)

    async def _logged_in(self, page):
        if "foundit.in" not in page.url:
            await _goto(page, "https://www.foundit.in/seeker/dashboard", lambda *a: None)
        return "foundit.in" in page.url and "login" not in page.url

    async def ensure_ready(self, ctx, log):
        page = await ctx.new_page()
        ok = await wait_for_login(page, self._logged_in, "https://www.foundit.in/rio/login", self.name, log)
        await page.close()
        return ok

    async def search(self, ctx, queries, env):
        log = env["log"]
        page = await ctx.new_page()
        cards = {}
        for q in queries:
            url = f"https://www.foundit.in/srp/results?query={quote(q)}&locations=Mumbai&sort=1"
            if not await _goto(page, url, log):
                continue
            await slow_scroll(page, None, 6)
            got = await page.evaluate(r"""() => {
              const T = e => e ? e.innerText.replace(/\s+/g,' ').trim() : '';
              const out = [];
              document.querySelectorAll('.srpResultCardContainer, .cardContainer, [class*="jobTuple"], [class*="srpCard"]').forEach(c => {
                const a = c.querySelector('a[href*="/job/"]') || c.querySelector('.jobTitle a') || c.closest('a[href*="/job/"]');
                out.push({title: T(c.querySelector('.jobTitle, h3, [class*="title"]')), job_url: a ? a.href.split('?')[0] : '',
                  company: T(c.querySelector('.companyName, [class*="company"]')),
                  location: T(c.querySelector('.details.location, [class*="location"]')),
                  experience_text: T(c.querySelector('.details.experience, [class*="experience"]')),
                  salary_text: T(c.querySelector('[class*="salary"]')),
                  posted: T(c.querySelector('.jobAddedTime, [class*="posted"], [class*="time"]'))}); });
              return out.filter(x => x.job_url); }""")
            for c in got:
                cards.setdefault(c["job_url"], c)
            log(f"Foundit '{q}': {len(got)} jobs")
        jobs = []
        for url, c in cards.items():
            if env["stopped"]() or env["seen"](url):
                continue
            if not await _goto(page, url, log):
                continue
            desc = await first_text(page, [".jobDescInfo", "#jobDescription", "[class*='jobDesc']"], 3000) or (await page_fallback(page))["description"]
            jobs.append(dict(c, description=desc, requisition_id=(re.search(r"(\d{6,})", url) or [None, ""])[1],
                             platform=self.name, apply_type="foundit"))
            await asyncio.sleep(1.0)
        await page.close()
        return jobs

    async def prepare(self, page, job, env):
        if not await _goto(page, job["job_url"], env["log"]):
            return {"state": "stuck", "needs": ["Job page did not open"], "notes": {}}
        await show_banner(page, f"Job Automator - ready for your review\n{job.get('title')} @ {job.get('company')}\n"
                                f"Match score: {job.get('score', '?')}\n\nFoundit applies in one click, so YOU press 'Apply'.")
        return {"state": "ready_open", "needs": ["Press Apply yourself (Foundit submits in one click)"], "notes": {}}

    async def is_submitted(self, page):
        if re.search(SUBMITTED_TEXT, await _body_text(page, 3000), re.I):
            return True
        return await _button_says_applied(page, "button[class*='apply' i], [class*='applyBtn' i], button:has-text('Applied')")


BOARDS = {"LinkedIn": LinkedIn(), "Naukri": Naukri(), "Indeed India": IndeedIndia(), "Foundit": Foundit()}
