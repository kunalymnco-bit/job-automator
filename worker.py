"""PC worker: runs the LinkedIn / Naukri / Indeed / Foundit automation for the online website.

Start it with run_worker.bat on the PC that should open the job-site browser. It watches the database for
runs started from the website (any device) and carries them out here, one at a time. Close the window to stop.
"""
import socket
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from core import db, runner  # noqa: E402

HOST = socket.gethostname()


def heartbeat(stop):
    while not stop.is_set():
        try:
            db.kv_set("worker:heartbeat", {"at": db.now(), "host": HOST, "ts": time.time()})
        except Exception as e:
            print("heartbeat failed:", e, flush=True)
        stop.wait(20)


def main():
    db.init_db()
    print(f"Job Automator worker on {HOST}")
    print("Database:", "online (Supabase / Postgres)" if db.is_postgres() else f"local file {db.DB_PATH}")
    print("Waiting for runs started from the website... (keep this window open)\n", flush=True)
    # runs left 'running' by a worker window that was closed earlier
    for r in db.list_runs(50):
        if r.get("status") == "running" and r.get("kind") != "online-search":
            db.finish_run(r["id"], "interrupted", "The worker was closed before this run finished.")
    stop = threading.Event()
    threading.Thread(target=heartbeat, args=(stop,), daemon=True).start()
    try:
        while True:
            try:
                r = db.claim_next_run()
            except Exception as e:
                print("database not reachable:", e, flush=True)
                time.sleep(30)
                continue
            if not r:
                time.sleep(8)
                continue
            kind = r.get("kind") or "find"
            print(f"[{db.now()}] Run #{r['id']} ({kind}) for {r['candidate_id']} - requested by {r.get('requested_by')}",
                  flush=True)
            runner.run(r["candidate_id"], r["id"], search_only=(kind == "search"), login_only=(kind == "login"))
            print(f"[{db.now()}] Run #{r['id']} done.\n", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()


if __name__ == "__main__":
    main()
