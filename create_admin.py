"""Create (or reset) an ADMIN account. Run this on the computer that runs the app:

    python create_admin.py

It is deliberately not available from the website, so nobody with the public link can make themselves admin.
"""
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from core import auth, db  # noqa: E402

db.init_db()
auth.init()
print("Create / reset an ADMIN login for the Job Application Automator")
print("Database:", "ONLINE (Supabase) - this admin works on the website" if db.is_postgres()
      else f"local file {db.DB_PATH} (add cloud_settings.toml first if this is for the online website)")
print()
user = input("Admin login ID (e.g. admin): ").strip().lower()
while True:
    pw = getpass.getpass("Password (min 8, letters + numbers; typing is hidden): ")
    prob = auth.password_problem(pw)
    if prob:
        print(prob)
        continue
    if getpass.getpass("Repeat password: ") != pw:
        print("Passwords do not match.")
        continue
    break
existing = {u["username"]: u for u in auth.list_users()}
if user in existing:
    auth.set_password(user, pw)
    auth.set_role(user, "admin")
    auth.set_active(user, True)
    print(f"Password reset - '{user}' is an active admin.")
else:
    auth.create_user(user, pw, role="admin", must_change=False)
    print(f"Admin '{user}' created. Open the dashboard and log in.")
