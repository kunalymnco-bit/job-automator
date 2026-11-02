"""User accounts for the dashboard (login ID + password).

* Passwords are stored only as salted PBKDF2-SHA256 hashes - never in plain text.
* 5 wrong passwords lock the account for 10 minutes.
* The first admin is created on the computer that runs the app (create_admin.bat), never from the web page,
  so nobody who finds the public link can make themselves admin.
"""
import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta

from . import db

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    pw_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT, last_login TEXT,
    failed INTEGER NOT NULL DEFAULT 0,
    locked_until TEXT,
    must_change INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    expires TEXT NOT NULL
);
"""
SESSION_DAYS = 7
ITER = 240_000
MAX_FAILS, LOCK_MIN = 5, 10


_READY = set()


def init():
    key = db.database_url() or str(db.DB_PATH)
    if key in _READY:
        return
    db.init_db()
    with db.connect() as con:
        con.executescript(SCHEMA)
    _READY.add(key)


def _hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), ITER)
    return f"pbkdf2${ITER}${salt}${dk.hex()}"


def _check(password, stored):
    try:
        _, it, salt, h = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(it))
        return hmac.compare_digest(dk.hex(), h)
    except Exception:
        return False


def valid_username(u):
    return bool(re.fullmatch(r"[A-Za-z0-9._-]{3,30}", u or ""))


def password_problem(p):
    if len(p or "") < 8:
        return "Password must be at least 8 characters."
    if not re.search(r"[A-Za-z]", p) or not re.search(r"\d", p):
        return "Password must contain letters and numbers."
    return ""


def any_admin():
    init()
    with db.connect() as con:
        return con.execute("SELECT 1 FROM users WHERE role='admin' AND active=1").fetchone() is not None


def create_user(username, password, role="user", must_change=True):
    init()
    username = username.strip().lower()
    if not valid_username(username):
        raise ValueError("Login ID: 3-30 characters, letters / numbers / . _ - only.")
    prob = password_problem(password)
    if prob:
        raise ValueError(prob)
    with db.connect() as con:
        if con.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
            raise ValueError("This login ID already exists.")
        con.execute("INSERT INTO users (username, pw_hash, role, active, created_at, must_change) VALUES (?,?,?,?,?,?)",
                    (username, _hash(password), role, 1, db.now(), int(must_change)))
    return username


def authenticate(username, password):
    """Returns (user dict | None, message)."""
    init()
    username = (username or "").strip().lower()
    with db.connect() as con:
        u = con.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if not u:
            _hash(password or "x")                        # same time cost as a real check
            return None, "Wrong login ID or password."
        u = dict(u)
        if not u["active"]:
            return None, "This account is disabled. Contact the admin."
        if u["locked_until"] and u["locked_until"] > db.now():
            return None, f"Too many wrong attempts. Try again after {u['locked_until'][11:16]}."
        if not _check(password or "", u["pw_hash"]):
            fails = u["failed"] + 1
            lock = (datetime.now() + timedelta(minutes=LOCK_MIN)).strftime("%Y-%m-%d %H:%M:%S") if fails >= MAX_FAILS else None
            con.execute("UPDATE users SET failed=?, locked_until=? WHERE username=?",
                        (0 if lock else fails, lock, username))
            return None, "Wrong login ID or password." + (f" Account locked for {LOCK_MIN} minutes." if lock else "")
        con.execute("UPDATE users SET failed=0, locked_until=NULL, last_login=? WHERE username=?", (db.now(), username))
        u["last_login"] = db.now()
        u.pop("pw_hash")
        return u, ""


def set_password(username, new_password, must_change=False, keep_token=None):
    prob = password_problem(new_password)
    if prob:
        raise ValueError(prob)
    with db.connect() as con:
        con.execute("DELETE FROM sessions WHERE username=?" + (" AND token_hash<>?" if keep_token else ""),
                    (username, _th(keep_token)) if keep_token else (username,))
        con.execute("UPDATE users SET pw_hash=?, must_change=?, failed=0, locked_until=NULL WHERE username=?",
                    (_hash(new_password), int(must_change), username))


def list_users():
    init()
    with db.connect() as con:
        return [dict(r) for r in con.execute(
            "SELECT username, role, active, created_at, last_login, locked_until, must_change FROM users ORDER BY username")]


def set_active(username, active):
    if not active:
        end_session(username=username)
    with db.connect() as con:
        con.execute("UPDATE users SET active=? WHERE username=?", (int(active), username))


def set_role(username, role):
    with db.connect() as con:
        con.execute("UPDATE users SET role=? WHERE username=?", (role, username))


def delete_user(username):
    end_session(username=username)
    with db.connect() as con:
        con.execute("DELETE FROM users WHERE username=?", (username,))


def generate_password():
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"
    while True:
        p = "".join(secrets.choice(alphabet) for _ in range(10))
        if not password_problem(p):
            return p


# ---------------- "stay logged in" sessions (a random token in a browser cookie) ----------------
def _th(token):
    return hashlib.sha256(token.encode()).hexdigest()


def new_session(username):
    init()
    token = secrets.token_urlsafe(32)
    exp = (datetime.now() + timedelta(days=SESSION_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    with db.connect() as con:
        con.execute("DELETE FROM sessions WHERE expires < ?", (db.now(),))
        con.execute("INSERT INTO sessions VALUES (?,?,?)", (_th(token), username, exp))
    return token


def user_from_session(token):
    if not token or not isinstance(token, str):
        return None
    init()
    with db.connect() as con:
        r = con.execute("SELECT username FROM sessions WHERE token_hash=? AND expires > ?", (_th(token), db.now())).fetchone()
        if not r:
            return None
        u = con.execute("SELECT * FROM users WHERE username=? AND active=1", (r["username"],)).fetchone()
        if not u:
            return None
        u = dict(u)
        u.pop("pw_hash")
        return u


def end_session(token=None, username=None):
    with db.connect() as con:
        if token:
            con.execute("DELETE FROM sessions WHERE token_hash=?", (_th(token),))
        if username:
            con.execute("DELETE FROM sessions WHERE username=?", (username,))
