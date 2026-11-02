"""Hard safety rules shared by every platform adapter.

THE AUTOMATION MUST NEVER CLICK THE FINAL SUBMIT BUTTON.

Every adapter must click buttons only through `safe_click()`. It refuses any element whose
visible text, value, aria-label or type looks like a final submission, and it cannot be
switched off by configuration.
"""
import re

FINAL_SUBMIT_PATTERNS = [
    r"\bsubmit\b", r"\bsend application\b", r"\bapply now\b", r"\bfinish\b", r"\bcomplete application\b",
    r"\bconfirm and apply\b", r"\bsubmit application\b", r"\breview and submit\b", r"\bsend\b",
    r"\bfinal submit\b", r"\bplace application\b", r"\beasy apply\b(?=.*submit)",
]

# Buttons that are allowed even though they move the form forward.
ALLOWED_NAVIGATION = [
    r"^\s*(next|continue|save|save and continue|save & continue|back|previous|upload|add|add another|"
    r"select file|attach|choose file|start application|apply for this job|apply manually|"
    r"autofill with resume|sign in|log in|login)\s*$",
]


class SubmitBlocked(Exception):
    """Raised whenever code tries to press something that looks like a final submit."""


def looks_like_final_submit(text: str = "", input_type: str = "", aria: str = "") -> bool:
    label = " ".join(x for x in (text, aria) if x).strip().lower()
    if any(re.match(p, label, re.I) for p in ALLOWED_NAVIGATION):
        return False
    if (input_type or "").lower() == "submit" and not label:
        return True
    return any(re.search(p, label, re.I) for p in FINAL_SUBMIT_PATTERNS)


def assert_not_final_submit(text: str = "", input_type: str = "", aria: str = ""):
    if looks_like_final_submit(text, input_type, aria):
        raise SubmitBlocked(f"Blocked click on '{text or aria or input_type}'. Final submission is reserved for the user.")


async def safe_click(locator, allow=(), block=()):
    """Click wrapper used by all adapters.
    allow: regexes for buttons that START a platform's application flow even though the generic rules
           would block them (e.g. Indeed's 'Apply now' only opens the form). Never used for submit buttons.
    block: extra platform-specific final buttons (e.g. Naukri's one-click 'Apply')."""
    text = ((await locator.inner_text()) if await locator.count() else "").strip()
    input_type = await locator.get_attribute("type") or ""
    aria = await locator.get_attribute("aria-label") or ""
    value = await locator.get_attribute("value") or ""
    label = (text or value or aria).strip()
    if any(re.search(p, label, re.I) for p in block):
        raise SubmitBlocked(f"Blocked click on '{label}': on this platform it submits the application.")
    if re.search(r"submit|send", label, re.I) or not any(re.search(p, label, re.I) for p in allow):
        assert_not_final_submit(text or value, input_type, aria)
    await locator.click()


AUTO_SUBMIT_ENABLED = False  # constant - not read from config, never True
