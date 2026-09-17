#!/usr/bin/env python3
"""Read-only snapshot of all saved work on Neon, into auto_backups\\<date>_<time>\\.

Runs on a schedule (Windows Task Scheduler, "Hygiene Neon backup"), and can be
run by hand before any risky change:  python backup_neon.py
It only reads. It never deletes old snapshots — they are small, keep them all.
The connection string comes from the gitignored deploy.secrets.ps1.
"""
import json
import os
import re
import sys
from datetime import datetime

import psycopg2

HERE = os.path.dirname(os.path.abspath(__file__))


def database_url():
    if os.environ.get("DATABASE_URL", "").startswith("postgres"):
        return os.environ["DATABASE_URL"]
    with open(os.path.join(HERE, "deploy.secrets.ps1"), encoding="utf-8") as f:
        m = re.search(r'DATABASE_URL\s*=\s*"(postgres[^"]+)"', f.read())
    if not m:
        sys.exit("No DATABASE_URL in deploy.secrets.ps1")
    return m.group(1)


def main():
    out = os.path.join(HERE, "auto_backups", datetime.now().strftime("%Y-%m-%d_%H%M%S"))
    os.makedirs(out, exist_ok=True)
    conn = psycopg2.connect(database_url())
    conn.set_session(readonly=True)
    try:
        cur = conn.cursor()
        cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
        # "products" is every crawl ever pushed and is rebuilt from the CSVs; the
        # latest crawl is in products_latest.
        tables = sorted(r[0] for r in cur.fetchall() if r[0] != "products")
        summary = {}
        for t in tables:
            cur.execute(f'SELECT * FROM "{t}"')
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            with open(os.path.join(out, f"{t}.json"), "w", encoding="utf-8") as f:
                json.dump(rows, f, default=str, ensure_ascii=False)
            summary[t] = len(rows)
    finally:
        conn.close()
    with open(os.path.join(out, "_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=1)
    print(out, summary)


if __name__ == "__main__":
    main()
