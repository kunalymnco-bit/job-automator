"""Database: every piece of app data lives here (users, candidates, CVs, tracker, runs, logs).

* Local:  SQLite file data/tracker.db (nothing to set up).
* Online: Postgres (free Supabase) when DATABASE_URL is set - in Streamlit Cloud "Secrets", or in
          cloud_settings.toml next to app.py on the PC that runs the worker. Both then share the same data.
"""
import hashlib
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "tracker.db"

STATUSES = [
    "Found", "Under Review", "Match Approved", "Application Started", "Application Filled",
    "Awaiting My Review", "Submitted", "Rejected", "Duplicate", "Not Eligible",
]


# ======================================================================= connection
def database_url():
    """DATABASE_URL from: environment -> Streamlit secrets -> cloud_settings.toml. None = local SQLite."""
    url = os.environ.get("DATABASE_URL")
    if url:
        return url.strip()
    try:
        import streamlit as st
        if "DATABASE_URL" in st.secrets:
            return str(st.secrets["DATABASE_URL"]).strip()
    except Exception:
        pass
    for f in (ROOT / "cloud_settings.toml", ROOT / ".streamlit" / "secrets.toml"):
        if f.exists():
            m = re.search(r'^\s*DATABASE_URL\s*=\s*"([^"]+)"', f.read_text(encoding="utf-8"), re.M)
            if m:
                return m.group(1).strip()
    return None


def is_postgres():
    return bool(database_url())


class _Row(dict):
    """Row usable as row['col'] and row[0]."""
    def __init__(self, cols, vals):
        super().__init__(zip(cols, vals))
        self._vals = vals

    def __getitem__(self, k):
        return self._vals[k] if isinstance(k, int) else super().__getitem__(k)


class _Cur:
    def __init__(self, cur):
        self.cur = cur

    def _wrap(self, r):
        if r is None:
            return None
        cols = [d[0] for d in self.cur.description]
        return _Row(cols, tuple(r))

    def fetchone(self):
        return self._wrap(self.cur.fetchone())

    def fetchall(self):
        return [self._wrap(r) for r in self.cur.fetchall()]

    def __iter__(self):
        return iter(self.fetchall())


class _Con:
    def __init__(self, raw, pg):
        self.raw, self.pg = raw, pg

    def execute(self, sql, params=()):
        if self.pg:
            sql = sql.replace("?", "%s")
        cur = self.raw.cursor()
        cur.execute(sql, tuple(params))
        return _Cur(cur)

    def executescript(self, script):
        for stmt in [s.strip() for s in script.split(";") if s.strip()]:
            self.execute(stmt)


import threading

_LOCAL = threading.local()       # one reused Postgres connection per thread (opening one costs ~0.5 s online)


def _pg_conn(url):
    import psycopg
    raw = getattr(_LOCAL, "pg", None)
    if raw is None or raw.closed or raw.broken or getattr(_LOCAL, "pg_url", None) != url:
        raw = psycopg.connect(url, autocommit=False, connect_timeout=15, prepare_threshold=None)
        _LOCAL.pg, _LOCAL.pg_url = raw, url
    return raw


@contextmanager
def connect():
    url = database_url()
    if url:
        raw = _pg_conn(url)                         # a broken / closed connection is replaced automatically
        con = _Con(raw, True)
        try:
            yield con
            raw.commit()
        except Exception:
            raw.rollback()
            raise
        return
    raw = sqlite3.connect(DB_PATH, timeout=30)
    con = _Con(raw, False)
    try:
        yield con
        raw.commit()
    except Exception:
        raw.rollback()
        raise
    finally:
        raw.close()


def _ddl(sql, pg):
    return sql.replace("{ID}", "BIGSERIAL PRIMARY KEY" if pg else "INTEGER PRIMARY KEY AUTOINCREMENT") \
              .replace("{BLOB}", "BYTEA" if pg else "BLOB")


SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id {ID},
    candidate_id TEXT NOT NULL DEFAULT '',
    dedupe_key TEXT NOT NULL,
    company TEXT, title TEXT, location TEXT, market TEXT,
    salary_text TEXT, salary_min_annual REAL, salary_max_annual REAL, currency TEXT,
    job_url TEXT, canonical_url TEXT, source_platform TEXT, requisition_id TEXT,
    date_posted TEXT, date_found TEXT, date_prepared TEXT,
    sponsorship_status TEXT DEFAULT 'Unknown',
    match_score REAL, match_reasons TEXT,
    resume_used TEXT,
    status TEXT DEFAULT 'Found',
    notes TEXT,
    description TEXT
);
CREATE INDEX IF NOT EXISTS ix_jobs_key ON jobs(candidate_id, dedupe_key);
CREATE INDEX IF NOT EXISTS ix_jobs_url ON jobs(canonical_url);
CREATE INDEX IF NOT EXISTS ix_jobs_req ON jobs(requisition_id);
CREATE TABLE IF NOT EXISTS runs (
    id {ID},
    candidate_id TEXT,
    started_at TEXT, finished_at TEXT, markets TEXT, platforms TEXT,
    resume_used TEXT, status TEXT, summary TEXT,
    kind TEXT DEFAULT 'find', requested_by TEXT, stop_requested INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS logs (
    id {ID},
    ts TEXT, run_id INTEGER, level TEXT, message TEXT
);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, data {BLOB}, updated TEXT)
"""

_INIT_DONE = set()


def columns(con, table):
    if con.pg:
        return [r[0] for r in con.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name=?", (table,)).fetchall()]
    return [r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()]


def init_db():
    key = database_url() or str(DB_PATH)
    if key in _INIT_DONE:
        return
    with connect() as con:
        # migrate databases created by earlier builds
        for table, cols in (("jobs", {"candidate_id": "TEXT NOT NULL DEFAULT ''"}),
                            ("runs", {"candidate_id": "TEXT", "kind": "TEXT DEFAULT 'find'", "requested_by": "TEXT",
                                      "stop_requested": "INTEGER DEFAULT 0"})):
            have = columns(con, table)
            if have:
                for c, typ in cols.items():
                    if c not in have:
                        con.execute(f"ALTER TABLE {table} ADD COLUMN {c} {typ}")
        con.executescript(_ddl(SCHEMA, con.pg))
    _INIT_DONE.add(key)


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ======================================================================= key-value + files
def kv_get(k, default=None):
    init_db()
    with connect() as con:
        r = con.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
    return json.loads(r["v"]) if r else default


def kv_set(k, value):
    init_db()
    with connect() as con:
        con.execute("INSERT INTO kv (k, v, updated) VALUES (?,?,?) ON CONFLICT (k) DO UPDATE SET v=excluded.v, "
                    "updated=excluded.updated", (k, json.dumps(value, ensure_ascii=False), now()))


def kv_delete(k):
    with connect() as con:
        con.execute("DELETE FROM kv WHERE k=?", (k,))


def kv_prefix(prefix):
    init_db()
    with connect() as con:
        rows = con.execute("SELECT k, v FROM kv WHERE k LIKE ? ORDER BY k", (prefix + "%",)).fetchall()
    return {r["k"]: json.loads(r["v"]) for r in rows}


def file_put(path, data: bytes):
    init_db()
    with connect() as con:
        con.execute("INSERT INTO files (path, data, updated) VALUES (?,?,?) ON CONFLICT (path) DO UPDATE SET "
                    "data=excluded.data, updated=excluded.updated", (path, bytes(data), now()))


def file_get(path):
    init_db()
    with connect() as con:
        r = con.execute("SELECT data FROM files WHERE path=?", (path,)).fetchone()
    return bytes(r["data"]) if r else None


def file_list(prefix):
    init_db()
    with connect() as con:
        return [r["path"] for r in con.execute("SELECT path FROM files WHERE path LIKE ? ORDER BY path",
                                               (prefix + "%",)).fetchall()]


def file_delete(path):
    with connect() as con:
        con.execute("DELETE FROM files WHERE path=?", (path,))


# ======================================================================= duplicate detection helpers
_TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "trk", "refid",
                    "trackingid", "src", "source", "from", "ref", "gh_src", "lever-source"}


def canonical_url(url: str) -> str:
    if not url:
        return ""
    p = urlparse(url.strip())
    q = [(k, v) for k, v in parse_qsl(p.query) if k.lower() not in _TRACKING_PARAMS]
    return urlunparse((p.scheme.lower(), p.netloc.lower().replace("www.", ""), p.path.rstrip("/"), "", urlencode(q), ""))


_COMPANY_SUFFIX = r"\b(inc|llc|llp|ltd|limited|pvt|private|corp|corporation|co|company|plc|india|us|usa)\b"


def norm(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def norm_company(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(_COMPANY_SUFFIX, " ", norm(s))).strip()


def norm_location(s: str) -> str:
    s = norm(s)
    s = re.sub(r"\b(united states|usa|us|india|remote|hybrid|on site|onsite)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip().split(" ")[0] if s.strip() else ""


def dedupe_key(company: str, title: str, location: str) -> str:
    raw = f"{norm_company(company)}|{norm(title)}|{norm_location(location)}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def find_duplicate(con, candidate_id, company, title, location, job_url="", requisition_id=""):
    """Return the existing row if THIS candidate already has this job from ANY platform.
    (The same job for two different candidates is not a duplicate.)"""
    cu = canonical_url(job_url)
    if cu:
        r = con.execute("SELECT * FROM jobs WHERE candidate_id=? AND canonical_url=? LIMIT 1",
                        (candidate_id, cu)).fetchone()
        if r:
            return r
    if requisition_id:
        r = con.execute("SELECT * FROM jobs WHERE candidate_id=? AND requisition_id=? AND company LIKE ? LIMIT 1",
                        (candidate_id, requisition_id.strip(), f"%{norm_company(company)[:12]}%")).fetchone()
        if r:
            return r
    return con.execute("SELECT * FROM jobs WHERE candidate_id=? AND dedupe_key=? LIMIT 1",
                       (candidate_id, dedupe_key(company, title, location))).fetchone()


def add_job(job: dict) -> tuple:
    """Insert a job. Returns (id, is_duplicate, previous_status)."""
    init_db()
    with connect() as con:
        dup = find_duplicate(con, job.get("candidate_id", ""), job.get("company"), job.get("title"), job.get("location"),
                             job.get("job_url", ""), job.get("requisition_id", ""))
        row = dict(job)
        row["dedupe_key"] = dedupe_key(job.get("company"), job.get("title"), job.get("location"))
        row["canonical_url"] = canonical_url(job.get("job_url", ""))
        row["date_found"] = now()
        if dup:
            row["status"] = "Duplicate"
            row["notes"] = (row.get("notes") or "") + f" Duplicate of #{dup['id']} ({dup['source_platform']}, status: {dup['status']})."
        cols = ",".join(row.keys())
        q = ",".join("?" * len(row))
        new_id = con.execute(f"INSERT INTO jobs ({cols}) VALUES ({q}) RETURNING id", list(row.values())).fetchone()[0]
        return new_id, bool(dup), (dup["status"] if dup else None)


def update_job(job_id: int, **fields):
    if not fields:
        return
    if fields.get("status") == "Submitted":
        fields["notes"] = (fields.get("notes") or "") + " Marked submitted by user."
    sets = ",".join(f"{k}=?" for k in fields)
    with connect() as con:
        con.execute(f"UPDATE jobs SET {sets} WHERE id=?", list(fields.values()) + [job_id])


def list_jobs(candidate_id=None, status=None, platform=None, market=None, search=None):
    init_db()
    q = "SELECT * FROM jobs WHERE 1=1"
    args = []
    if candidate_id:
        q += " AND candidate_id=?"; args.append(candidate_id)
    if status:
        q += f" AND status IN ({','.join('?' * len(status))})"; args += list(status)
    if platform:
        q += f" AND source_platform IN ({','.join('?' * len(platform))})"; args += list(platform)
    if market:
        q += f" AND market IN ({','.join('?' * len(market))})"; args += list(market)
    if search:
        q += " AND (company LIKE ? OR title LIKE ? OR location LIKE ? OR notes LIKE ?)"
        args += [f"%{search}%"] * 4
    q += " ORDER BY id DESC"
    with connect() as con:
        return [dict(r) for r in con.execute(q, args).fetchall()]


# ======================================================================= runs + logs
def start_run(candidate_id, markets, platforms, resume, kind="find", requested_by="", status="running"):
    init_db()
    with connect() as con:
        return con.execute("INSERT INTO runs (candidate_id, started_at, markets, platforms, resume_used, status, kind, "
                           "requested_by, stop_requested) VALUES (?,?,?,?,?,?,?,?,0) RETURNING id",
                           (candidate_id, now(), ",".join(markets), ",".join(platforms), resume, status, kind,
                            requested_by)).fetchone()[0]


def claim_next_run():
    """Worker: take the oldest queued run (atomically). Returns the run dict or None."""
    init_db()
    with connect() as con:
        r = con.execute("SELECT * FROM runs WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
        if not r:
            return None
        n = con.execute("UPDATE runs SET status='running', started_at=? WHERE id=? AND status='queued' RETURNING id",
                        (now(), r["id"])).fetchone()
        return dict(r) if n else None


def get_run(run_id):
    with connect() as con:
        r = con.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    return dict(r) if r else None


def request_stop(run_id):
    with connect() as con:
        con.execute("UPDATE runs SET stop_requested=1 WHERE id=?", (run_id,))
        con.execute("UPDATE runs SET status='cancelled', finished_at=? WHERE id=? AND status='queued'", (now(), run_id))


def stop_requested(run_id):
    r = get_run(run_id)
    return bool(r and r.get("stop_requested"))


def finish_run(run_id, status, summary):
    with connect() as con:
        con.execute("UPDATE runs SET finished_at=?, status=?, summary=? WHERE id=?", (now(), status, summary, run_id))


def log(message, level="INFO", run_id=None):
    init_db()
    with connect() as con:
        con.execute("INSERT INTO logs (ts, run_id, level, message) VALUES (?,?,?,?)", (now(), run_id, level, message))


def recent_logs(limit=200):
    init_db()
    with connect() as con:
        return [dict(r) for r in con.execute("SELECT * FROM logs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]


def run_logs(run_id, limit=12):
    with connect() as con:
        return [dict(r) for r in con.execute("SELECT ts, level, message FROM logs WHERE run_id=? ORDER BY id DESC LIMIT ?",
                                             (run_id, limit)).fetchall()]


def list_runs(limit=50):
    init_db()
    with connect() as con:
        return [dict(r) for r in con.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]
