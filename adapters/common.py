"""Shared helpers for browser-based platform adapters."""
import asyncio
import json
import random
import re
import urllib.request

from core.form_filler import show_banner, has_captcha, wait_for_user_captcha, human_pause  # noqa: F401

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) JobApplicationAutomator/1.0"

TEXT_FALLBACK_JS = r"""
() => {
  const T = el => (el.innerText || '').replace(/[ \t]+/g, ' ').trim();
  const h1 = document.querySelector('h1');
  let best = '', bestLen = 0;
  for (const el of document.querySelectorAll('article, section, div')) {
    const t = T(el);
    if (t.length < 300 || t.length > 25000) continue;
    const score = (/(responsib|requirement|qualification|experience|skills|role)/i.test(t) ? 2 : 1) * Math.min(t.length, 6000)
                  - el.querySelectorAll('a').length * 40;
    if (score > bestLen && !/sign in|cookie/i.test(t.slice(0, 120))) { best = t; bestLen = score; }
  }
  return {title: h1 ? T(h1) : '', doc_title: document.title, description: best};
}
"""


async def first_text(page, selectors, timeout=1500):
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if await loc.count():
                t = (await loc.inner_text(timeout=timeout)).strip()
                if t:
                    return re.sub(r"\s+", " ", t)
        except Exception:
            continue
    return ""


async def page_fallback(page):
    try:
        return await page.evaluate(TEXT_FALLBACK_JS)
    except Exception:
        return {"title": "", "doc_title": "", "description": ""}


async def slow_scroll(page, selector=None, rounds=8):
    for _ in range(rounds):
        try:
            if selector and await page.locator(selector).count():
                await page.locator(selector).first.evaluate("e => e.scrollBy(0, e.clientHeight * 0.9)")
            else:
                await page.mouse.wheel(0, 900)
        except Exception:
            pass
        await asyncio.sleep(random.uniform(0.5, 1.1))


async def wait_for_login(page, is_logged_in, login_url, platform, log, minutes=10):
    """Open the login page and wait while the USER logs in. Passwords are never typed or stored by the tool."""
    if await is_logged_in(page):
        return True
    await page.goto(login_url, wait_until="domcontentloaded")
    log(f"{platform}: please log in in the browser window (one time per candidate). Waiting up to {minutes} minutes...", "WARN")
    await show_banner(page, f"Job Automator: please log in to {platform} for this candidate.\n"
                            "The tool continues automatically after you log in. Your password is not saved by the tool.")
    for _ in range(minutes * 12):
        await asyncio.sleep(5)
        try:
            if await is_logged_in(page):
                log(f"{platform}: logged in.")
                return True
        except Exception:
            pass
    log(f"{platform}: not logged in - skipped this run.", "WARN")
    return False


def http_json(url, data=None, timeout=25):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers={"User-Agent": UA, "Accept": "application/json",
                                                          **({"Content-Type": "application/json"} if body else {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


def strip_html(html):
    import html as H
    t = H.unescape(html or "")
    t = re.sub(r"<(br|/p|/li|/div|/h\d)[^>]*>", "\n", t, flags=re.I)
    t = re.sub(r"<li[^>]*>", "\n- ", t, flags=re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = H.unescape(t)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n\n", t)).strip()


def experience_filter_linkedin(years):
    """LinkedIn f_E codes: 1 internship, 2 entry, 3 associate, 4 mid-senior."""
    if years is None:
        return "2,3"
    if years < 1:
        return "1,2"
    if years < 3:
        return "2,3"
    if years < 7:
        return "3,4"
    return "4"


ATS_HOSTS = {"greenhouse": "Greenhouse", "lever.co": "Lever", "myworkdayjobs": "Workday", "workday": "Workday"}


def ats_of(url):
    u = (url or "").lower()
    for k, v in ATS_HOSTS.items():
        if k in u:
            return v
    return None
