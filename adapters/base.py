"""Platform adapter interface.

Every adapter:
  ensure_ready(ctx, log)        -> bool   (the USER logs in when needed; no passwords stored)
  search(ctx, queries, env)     -> list of job dicts
  prepare(page, job, env)       -> dict(state, needs, notes)   fills the form and STOPS before submit
  is_submitted(page)            -> bool   (confirmation shown after the user clicked Submit)

env = {"prefs", "profile", "taxonomy", "cv_path", "log", "seen", "stopped", "days", "max_pages"}
"""


class PlatformAdapter:
    name = "base"
    # "fill"      : opens the application and fills it, stops before the final button
    # "open_only" : the platform applies in ONE click (that click is the submission), so the job is
    #               opened with everything ready and the user presses Apply
    apply_mode = "fill"
    needs_browser = True

    async def ensure_ready(self, ctx, log):
        return True

    async def search(self, ctx, queries, env):
        raise NotImplementedError

    async def prepare(self, page, job, env):
        raise NotImplementedError

    async def is_submitted(self, page):
        return False

    async def assist(self, page, job, env):
        """Called repeatedly while a prepared tab is open (e.g. Naukri questions after you press Apply)."""
        return None


SUBMITTED_TEXT = r"application (was |has been )?(sent|submitted|received)|you have (successfully )?applied|" \
                 r"thank you for (applying|your application)|successfully applied|applied successfully"
