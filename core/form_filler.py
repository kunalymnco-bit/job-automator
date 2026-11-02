"""Generic application-form filler (Playwright, async).

Works on any page or modal: finds every visible field, reads its question (label / legend /
aria text), asks core.answers for an answer from the confirmed profile, fills it, uploads the CV,
and moves through "Next / Continue / Review" steps.

It STOPS:
  * before any final-submit button (core.safety blocks the click - it cannot be pressed),
  * on a required question it cannot answer from the profile,
  * when a CAPTCHA / security check appears (the user solves it; nothing is bypassed).
"""
import asyncio
import random
import re

from . import answers as A
from .safety import looks_like_final_submit, SubmitBlocked, safe_click

FIELD_JS = r"""
(root) => {
  const T = el => el ? (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim() : '';
  const visible = el => {
    if (!el) return false;
    const s = getComputedStyle(el); const r = el.getBoundingClientRect();
    if (s.display === 'none' || s.visibility === 'hidden') return false;
    return r.width > 0 && r.height > 0;
  };
  const labelOf = el => {
    if (el.labels && el.labels.length) { const t = T(el.labels[0]); if (t) return t; }
    const lb = el.getAttribute('aria-labelledby');
    if (lb) { const t = lb.split(/\s+/).map(i => T(document.getElementById(i))).join(' ').trim(); if (t) return t; }
    if (el.getAttribute('aria-label')) return el.getAttribute('aria-label');
    let p = el.parentElement;
    for (let i = 0; i < 5 && p; i++, p = p.parentElement) {
      const l = p.querySelector('label, legend, [class*="label"], [class*="question"]');
      if (l && !l.contains(el) && T(l) && T(l).length < 400) return T(l);
    }
    return el.getAttribute('placeholder') || el.getAttribute('name') || '';
  };
  const groupLabel = (el) => {
    const fs = el.closest('fieldset');
    if (fs) { const lg = fs.querySelector('legend'); if (lg && T(lg)) return T(lg); }
    const grp = el.closest('[role="radiogroup"], [role="group"]');
    if (grp) {
      const lb = grp.getAttribute('aria-labelledby');
      if (lb && document.getElementById(lb)) return T(document.getElementById(lb));
      if (grp.getAttribute('aria-label')) return grp.getAttribute('aria-label');
    }
    let p = el.parentElement;
    for (let i = 0; i < 6 && p; i++, p = p.parentElement) {
      const cands = [...p.querySelectorAll('label, legend, span, div, p')].filter(n =>
        !n.querySelector('input') && !n.closest('label[for]') && T(n) && T(n).length < 300 && T(n).length > 3);
      if (cands.length && p.querySelectorAll('input[type=radio]').length >= 2) return T(cands[0]);
    }
    return el.getAttribute('name') || '';
  };
  const out = []; const seenGroups = new Set(); let n = 0;
  const els = root.querySelectorAll('input, textarea, select');
  for (const el of els) {
    const type = (el.getAttribute('type') || el.tagName).toLowerCase();
    if (['hidden', 'submit', 'button', 'image', 'reset', 'search'].includes(type)) continue;
    if (el.disabled || el.readOnly && type !== 'file') continue;
    const id = 'jaa' + (n++);
    if (type === 'radio') {
      const key = el.name || id;
      if (seenGroups.has(key)) continue; seenGroups.add(key);
      const radios = el.name ? [...root.querySelectorAll('input[type=radio][name="' + CSS.escape(el.name) + '"]')] : [el];
      if (!radios.some(r => visible(r) || visible(r.closest('label')) || (r.labels && r.labels[0] && visible(r.labels[0])))) continue;
      radios.forEach((r, i) => r.setAttribute('data-jaa', id + '_' + i));
      out.push({id, kind: 'radio', label: groupLabel(el), options: radios.map(r => (r.labels && r.labels[0] ? T(r.labels[0]) : r.value)),
                value: (radios.find(r => r.checked) || {}).value || '', required: radios.some(r => r.required) || /\*/.test(groupLabel(el))});
      continue;
    }
    if (type !== 'file' && !visible(el)) continue;
    el.setAttribute('data-jaa', id);
    const label = labelOf(el);
    let kind = el.tagName === 'SELECT' ? 'select' : el.tagName === 'TEXTAREA' ? 'textarea' :
               type === 'checkbox' ? 'checkbox' : type === 'file' ? 'file' :
               type === 'number' ? 'number' : (el.getAttribute('role') === 'combobox' || el.getAttribute('aria-autocomplete')) ? 'combobox' : 'text';
    let options = [];
    if (kind === 'select') options = [...el.options].map(o => T(o) || o.value);
    let value = kind === 'checkbox' ? (el.checked ? 'checked' : '') : kind === 'select' ? (el.selectedIndex > 0 || (el.value && !/select/i.test(T(el.options[el.selectedIndex] || {}))) ? T(el.options[el.selectedIndex]) : '') : (el.value || '');
    out.push({id, kind, label, options, value, accept: el.getAttribute('accept') || '',
              required: el.required || el.getAttribute('aria-required') === 'true' || /\*\s*$/.test(label)});
  }
  return out;
}
"""

CAPTCHA_JS = r"""
() => {
  const t = (document.body ? document.body.innerText : '').toLowerCase().slice(0, 20000);
  const fr = [...document.querySelectorAll('iframe')].some(f => /recaptcha|hcaptcha|turnstile|captcha|arkose|funcaptcha/i.test(f.src || ''));
  const txt = /verify you are (a )?human|are you a robot|security check|unusual activity|complete the captcha|i'm not a robot|press and hold/.test(t);
  const url = /checkpoint|captcha|challenge/.test(location.href);
  return fr || txt || url;
}
"""

BANNER_JS = r"""
(msg) => {
  let b = document.getElementById('jaa-banner');
  if (!b) { b = document.createElement('div'); b.id = 'jaa-banner'; document.body.appendChild(b); }
  b.style.cssText = 'position:fixed;left:12px;bottom:12px;z-index:2147483647;max-width:420px;background:#1F3864;color:#fff;' +
    'font:13px/1.4 Segoe UI,Arial,sans-serif;padding:10px 12px;border-radius:8px;box-shadow:0 4px 14px rgba(0,0,0,.3);pointer-events:none;white-space:pre-line';
  b.textContent = msg;
}
"""

NEXT_WORDS = r"^(next|continue|review|review your application|save and continue|save & continue|proceed|" \
             r"continue to next step|next step|start application|apply for this job|apply manually)$"


async def human_pause(lo=0.6, hi=1.6):
    await asyncio.sleep(random.uniform(lo, hi))


async def has_captcha(page):
    try:
        return await page.evaluate(CAPTCHA_JS)
    except Exception:
        return False


async def wait_for_user_captcha(page, log, minutes=10):
    """Pause until the user has solved the CAPTCHA in the browser window. Nothing is bypassed."""
    log("CAPTCHA / security check on screen - please solve it in the browser window. Waiting...", "WARN")
    await show_banner(page, "Job Automator is paused: please complete the security check on this page.\n"
                            "It will continue automatically afterwards.")
    for _ in range(minutes * 12):
        await asyncio.sleep(5)
        if not await has_captcha(page):
            log("Security check completed by you - continuing.")
            return True
    log("Security check not completed in time - this platform is skipped for now.", "WARN")
    return False


async def show_banner(page, msg):
    try:
        await page.evaluate(BANNER_JS, msg)
    except Exception:
        pass


async def _root_handle(root):
    """root may be a Page or a Locator; return an ElementHandle for evaluate()."""
    if hasattr(root, "element_handle"):
        return await root.element_handle()
    return await root.evaluate_handle("document")


async def read_fields(root):
    h = await _root_handle(root)
    return await h.evaluate(FIELD_JS)


def _loc(root, fid):
    return root.locator(f'[data-jaa="{fid}"]')


async def _fill_text(root, f, value):
    loc = _loc(root, f["id"])
    await loc.click()
    await loc.fill("")
    await loc.type(str(value), delay=random.randint(15, 45))
    if f["kind"] == "combobox":
        await asyncio.sleep(1.4)
        page = root.page if hasattr(root, "page") else root
        opt = page.locator('[role="option"]:visible, .basic-typeahead__selectable:visible').first
        if await opt.count():
            await opt.click()
        else:
            await loc.press("ArrowDown")
            await loc.press("Enter")


async def _choose_radio(root, f, choice):
    idx = f["options"].index(choice)
    r = _loc(root, f"{f['id']}_{idx}")
    rid = await r.get_attribute("id")
    lab = root.locator(f'label[for="{rid}"]') if rid else None
    if lab is not None and await lab.count():
        await lab.first.click()
    else:
        await r.check(force=True)


async def fill_fields(root, profile, job, cv_path, platform, log, notes):
    """Fill every visible field once. Returns list of required questions left for the user."""
    fields = await read_fields(root)
    needs = []
    for f in fields:
        q, kind = f["label"], f["kind"]
        try:
            if kind == "file":
                if re.search(r"cover", q, re.I):
                    continue
                if not re.search(r"resume|cv|curriculum|upload|attach|document", q + " " + f.get("accept", ""), re.I) \
                        and ".pdf" not in f.get("accept", "") and "doc" not in f.get("accept", ""):
                    continue
                if cv_path and not notes.get("cv_uploaded"):
                    await _loc(root, f["id"]).set_input_files(cv_path)
                    notes["cv_uploaded"] = True
                    log(f"Uploaded CV: {cv_path.split('/')[-1].split(chr(92))[-1]}")
                    await human_pause(1.5, 2.5)
                continue
            if f["value"] and kind != "checkbox":
                continue                                    # already filled (by the site or earlier)
            ans = A.answer(q, kind, f.get("options", []), profile, job, platform)
            if ans is None or ans.value in (None, ""):
                if f["required"] or kind == "radio":
                    needs.append(q or "(unlabelled field)")
                else:
                    notes.setdefault("left_blank", []).append(q)
                continue
            val = ans.value
            if kind == "select":
                choice = A.pick_option(val, f["options"])
                if not choice:
                    (needs if f["required"] else notes.setdefault("left_blank", [])).append(q)
                    continue
                await _loc(root, f["id"]).select_option(label=choice)
            elif kind == "radio":
                choice = A.pick_option(val, f["options"])
                if not choice:
                    needs.append(q); continue
                await _choose_radio(root, f, choice)
            elif kind == "checkbox":
                if str(val).lower() in ("yes", "true") and not f["value"]:
                    await _loc(root, f["id"]).check(force=True)
                elif f["required"] and not f["value"]:
                    needs.append(q)
                continue
            else:
                await _fill_text(root, f, val)
            notes.setdefault("answers", []).append({"q": q[:150], "a": str(val)[:300], "source": ans.source,
                                                    "written": ans.generated})
            await human_pause(0.3, 0.9)
        except SubmitBlocked:
            raise
        except Exception as e:                            # odd widget - leave it to the user
            needs.append(f"{q} (could not fill: {type(e).__name__})")
    # required consent boxes etc. still empty
    for f in fields:
        if f["kind"] == "checkbox" and f["required"] and not f["value"] and f["label"] not in needs:
            if not A.answer(f["label"], "checkbox", [], profile, job, platform):
                needs.append(f["label"])
    # mark what the user must look at
    if needs:
        try:
            h = await _root_handle(root)
            await h.evaluate("""(r) => r.querySelectorAll('[data-jaa]').forEach(e => {
                 if (!e.value && e.type !== 'file' && e.type !== 'hidden') e.style.outline = '3px solid #e8a400'; })""")
        except Exception:
            pass
    return list(dict.fromkeys(needs))


async def visible_buttons(root):
    btns = root.locator("button:visible, input[type=submit]:visible, [role=button]:visible, a[role=button]:visible")
    out = []
    for i in range(min(await btns.count(), 60)):
        b = btns.nth(i)
        try:
            text = (await b.inner_text() or await b.get_attribute("value") or "").strip()
            aria = (await b.get_attribute("aria-label") or "").strip()
            typ = (await b.get_attribute("type") or "").strip()
            out.append((b, re.sub(r"\s+", " ", text), aria, typ))
        except Exception:
            continue
    return out


async def final_submit_visible(root):
    for b, text, aria, typ in await visible_buttons(root):
        if (text or aria) and looks_like_final_submit(text, typ, aria):
            return text or aria
    return None


async def click_next(root, extra_block=()):
    """Click a Next/Continue/Review-type button. Never anything that looks like a final submit."""
    for b, text, aria, typ in await visible_buttons(root):
        label = (text or aria).strip().lower()
        if not label or looks_like_final_submit(text, typ, aria):
            continue
        if any(re.search(p, label, re.I) for p in extra_block):
            continue
        if re.match(NEXT_WORDS, label, re.I) or re.match(NEXT_WORDS, aria.lower(), re.I) \
                or re.search(r"continue to next step|review your application", aria, re.I):
            await safe_click(b)
            return text or aria
    return None


async def run_form(page, root_getter, profile, job, cv_path, platform, log, max_steps=15, extra_block=()):
    """Fill a (multi-step) form until a final-submit button shows, or a question needs the user.
    root_getter: async fn -> Locator/Page that contains the current step.
    Returns dict(state='ready'|'needs_input'|'captcha'|'stuck', needs=[...], notes={...})."""
    notes = {}
    last_sig = None
    for step in range(max_steps):
        if await has_captcha(page):
            if not await wait_for_user_captcha(page, log):
                return {"state": "captcha", "needs": ["Security check"], "notes": notes}
        root = await root_getter()
        if root is None:
            return {"state": "stuck", "needs": ["Application form did not open"], "notes": notes}
        needs = await fill_fields(root, profile, job, cv_path, platform, log, notes)
        await human_pause()
        sub = await final_submit_visible(root)
        if needs:
            return {"state": "needs_input", "needs": needs, "notes": notes, "submit_button": sub}
        if sub:
            return {"state": "ready", "needs": [], "notes": notes, "submit_button": sub}
        sig = str([f["label"] for f in await read_fields(root)])
        clicked = await click_next(root, extra_block)
        if not clicked:
            return {"state": "stuck", "needs": ["No Next / Continue button found - please finish this form"], "notes": notes}
        log(f"  step {step + 1}: '{clicked}'")
        await human_pause(1.2, 2.4)
        # validation errors after clicking Next -> the user must look
        root2 = await root_getter()
        if root2 is not None:
            new_sig = str([f["label"] for f in await read_fields(root2)])
            if new_sig == sig and new_sig == last_sig:
                return {"state": "needs_input", "needs": ["The form shows an error on this step - please check"],
                        "notes": notes}
            last_sig = sig
    return {"state": "stuck", "needs": ["Form has too many steps - please finish manually"], "notes": notes}
