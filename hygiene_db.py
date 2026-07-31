#!/usr/bin/env python3
# hygiene_db.py
"""
Database layer for the Hygiene crawler / validator.

Stores every crawled product row in a `products` table, keyed by ASIN, with a
`crawled_at` timestamp and a `crawl_run` id so you keep HISTORY across runs (the
validator can always read the latest row per ASIN, and you can diff runs later).

Works with TWO backends, chosen by the DATABASE_URL environment variable:
  - Not set            -> local SQLite file  ./output/hygiene.db   (zero setup)
  - postgres://...     -> Postgres / Neon     (production, shared with the app)

Usage from the crawler (already wired in amazon_in_crawler_playwright.py):
    from hygiene_db import save_rows, init_db
    init_db()
    save_rows(list_of_dicts, crawl_run="2026-06-25_full")

Usage from the validator / any reader:
    from hygiene_db import fetch_latest
    rows = fetch_latest()                # newest row per ASIN, all brands
    rows = fetch_latest(brand="Nexlev")  # filter by brand

Standalone tools:
    python hygiene_db.py import-csv output/amazon_products_full.csv
    python hygiene_db.py show B0G3Q59X8C
    python hygiene_db.py stats
"""

import os
import json
import sys
import hmac
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

# The 48 crawler columns, in order. Kept here so the schema and the crawler
# never drift apart — import this list in the crawler if you like.
CRAWLER_COLUMNS = [
    "ASIN", "SKU", "Title", "Brand",
    "MRP", "Selling Price", "Deal Price", "Buy Box Price",
    "Rating", "Rating Count",
    "Bullets", "A+ Content", "Image URLs",
    "Sold By", "Other Sellers",
    "Category Tree", "Weight", "Dimensions",
    "Tech Details", "Product URL", "Best Sellers Rank",
    "Manufacturer Contact Information", "Packer Contact Information",
    "Return Policy", "Warranty Policy",
    "Warranty Description", "What is in the box?",
    "Colour", "Material", "Additional Features",
    "Importer Contact Information",
    "Brand Story", "Description",
    "Listing Video", "Variation Data",
    "Image Count", "Video Count", "A+ Image Count",
    "CS / Support QR / Warranty Image",
    "Ours vs Their Image",
    "What is in the Box Image",
    "Brand Store", "Variation Count",
    "Availability Text", "Stock Status", "Listing Status",
    "Crawled ASIN", "ASIN Redirect",
]


def _col_to_field(col):
    """Turn a human column name into a safe snake_case DB column name.
    'A+ Content' -> 'a_content', 'What is in the box?' -> 'what_is_in_the_box'."""
    s = col.lower()
    out = []
    for ch in s:
        if ch.isalnum():
            out.append(ch)
        else:
            out.append("_")
    field = "".join(out)
    while "__" in field:
        field = field.replace("__", "_")
    return field.strip("_")


# Map human column -> db field, and remember the reverse for reads.
COL_TO_FIELD = {c: _col_to_field(c) for c in CRAWLER_COLUMNS}
FIELD_TO_COL = {v: k for k, v in COL_TO_FIELD.items()}
DB_FIELDS = list(COL_TO_FIELD.values())


# --------------------------------------------------------------------------
# Backend selection
# --------------------------------------------------------------------------
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
_IS_PG = DATABASE_URL.startswith("postgres")

if _IS_PG:
    import psycopg2
    import psycopg2.extras

    def _connect():
        conn = psycopg2.connect(DATABASE_URL)
        conn.cursor_factory = psycopg2.extras.RealDictCursor
        return conn

    PLACEHOLDER = "%s"
    SERIAL = "SERIAL PRIMARY KEY"
    TEXT = "TEXT"
else:
    import sqlite3

    # default local file next to the crawler output
    _DEFAULT_SQLITE = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "output", "hygiene.db"
    )
    SQLITE_PATH = os.environ.get("HYGIENE_SQLITE", _DEFAULT_SQLITE)
    os.makedirs(os.path.dirname(SQLITE_PATH), exist_ok=True)

    def _connect():
        conn = sqlite3.connect(SQLITE_PATH)
        conn.row_factory = sqlite3.Row
        return conn

    PLACEHOLDER = "?"
    SERIAL = "INTEGER PRIMARY KEY AUTOINCREMENT"
    TEXT = "TEXT"


def backend_name():
    return "postgres" if _IS_PG else f"sqlite ({SQLITE_PATH})"


def _scalar(row):
    """Return the first/only value from a fetched row, whether the row is a
    tuple (sqlite / plain cursor) or a dict (psycopg2 RealDictCursor)."""
    if row is None:
        return None
    if isinstance(row, dict):
        return next(iter(row.values()))
    return row[0]


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------
def init_db():
    """Create the products table if it doesn't exist. Safe to call every run."""
    cols_sql = ",\n  ".join(f'"{f}" {TEXT}' for f in DB_FIELDS)
    ddl = f"""
    CREATE TABLE IF NOT EXISTS products (
      id {SERIAL},
      crawl_run {TEXT},
      crawled_at {TEXT},
      {cols_sql}
    );
    """
    idx1 = 'CREATE INDEX IF NOT EXISTS idx_products_asin ON products (asin);'
    idx2 = 'CREATE INDEX IF NOT EXISTS idx_products_run ON products (crawl_run);'
    idx3 = 'CREATE INDEX IF NOT EXISTS idx_products_brand ON products (brand);'
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(ddl)
        for ix in (idx1, idx2, idx3):
            cur.execute(ix)
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Writes
# --------------------------------------------------------------------------
def _row_to_values(row, crawl_run, crawled_at):
    """Map a crawler dict (human keys) to ordered DB values."""
    vals = [crawl_run, crawled_at]
    for col in CRAWLER_COLUMNS:
        v = row.get(col, "")
        if v is None:
            v = ""
        vals.append(str(v))
    return vals


def save_rows(rows, crawl_run=None, replace_run=True):
    """Insert crawler rows (list of dicts with human column keys).

    crawl_run: a label for this crawl (default: timestamp). All rows from one
               crawl share it, so you can fetch or diff a specific run.
    replace_run: if True, delete any existing rows with the same crawl_run
                 first (so re-running the same labelled crawl is idempotent).
    """
    if not rows:
        return 0
    init_db()
    if crawl_run is None:
        crawl_run = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    crawled_at = datetime.now(timezone.utc).isoformat()

    all_fields = ["crawl_run", "crawled_at"] + DB_FIELDS
    quoted = ", ".join(f'"{f}"' for f in all_fields)
    marks = ", ".join([PLACEHOLDER] * len(all_fields))
    insert = f'INSERT INTO products ({quoted}) VALUES ({marks})'

    conn = _connect()
    try:
        cur = conn.cursor()
        if replace_run:
            cur.execute(
                f"DELETE FROM products WHERE crawl_run = {PLACEHOLDER}", (crawl_run,)
            )
        data = [_row_to_values(r, crawl_run, crawled_at) for r in rows]
        if _IS_PG:
            psycopg2.extras.execute_batch(cur, insert, data, page_size=200)
        else:
            cur.executemany(insert, data)
        conn.commit()
        return len(data)
    finally:
        conn.close()


def upsert_latest(rows, crawl_run=None):
    """Maintain a `products_latest` table holding ONE row per ASIN (newest).

    The validator can read this table directly without worrying about history.
    Called automatically by save_rows is optional — kept separate so history
    and 'latest' stay decoupled.
    """
    if not rows:
        return 0
    init_db()
    crawled_at = datetime.now(timezone.utc).isoformat()
    if crawl_run is None:
        crawl_run = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")

    cols_sql = ",\n  ".join(
        f'"{f}" {TEXT}' for f in DB_FIELDS if f != "asin"
    )
    ddl = f"""
    CREATE TABLE IF NOT EXISTS products_latest (
      asin {TEXT} PRIMARY KEY,
      crawl_run {TEXT},
      crawled_at {TEXT},
      {cols_sql}
    );
    """
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(ddl)
        for r in rows:
            asin = str(r.get("ASIN", "")).strip()
            if not asin:
                continue
            other_fields = [f for f in DB_FIELDS if f != "asin"]
            fields = ["asin", "crawl_run", "crawled_at"] + other_fields
            vals = [asin, crawl_run, crawled_at] + [
                str(r.get(FIELD_TO_COL[f], "") or "") for f in other_fields
            ]
            quoted = ", ".join(f'"{f}"' for f in fields)
            marks = ", ".join([PLACEHOLDER] * len(fields))
            if _IS_PG:
                updates = ", ".join(
                    f'"{f}" = EXCLUDED."{f}"' for f in fields if f != "asin"
                )
                sql = (f'INSERT INTO products_latest ({quoted}) VALUES ({marks}) '
                       f'ON CONFLICT (asin) DO UPDATE SET {updates}')
            else:
                sql = f'INSERT OR REPLACE INTO products_latest ({quoted}) VALUES ({marks})'
            cur.execute(sql, vals)
        conn.commit()
        return len(rows)
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Reads
# --------------------------------------------------------------------------
def _rows_to_human(db_rows):
    """Convert DB rows (snake_case fields) back to human column dicts."""
    out = []
    for r in db_rows:
        d = dict(r)
        human = {}
        for f, col in FIELD_TO_COL.items():
            human[col] = d.get(f, "")
        # carry metadata
        human["_crawl_run"] = d.get("crawl_run", "")
        human["_crawled_at"] = d.get("crawled_at", "")
        out.append(human)
    return out


def fetch_latest(brand=None, active_only=False):
    """Return the newest row per ASIN (across all runs).

    brand: partial + case-insensitive match ('Nexlev' matches 'Visit the nexlev Store').
    active_only: if True, return ONLY live & buyable listings
                 (Listing Status == 'Live - Active') — excludes out-of-stock
                 and dead/suppressed ASINs.
    Reads from products_latest if present, else derives from products."""
    like = None
    if brand:
        like = f"%{brand.lower()}%"
    conn = _connect()
    try:
        cur = conn.cursor()
        where = []
        params = []
        if like:
            where.append(f'LOWER(brand) LIKE {PLACEHOLDER}')
            params.append(like)
        if active_only:
            where.append(f'listing_status = {PLACEHOLDER}')
            params.append('Live - Active')
        where_sql = (' WHERE ' + ' AND '.join(where)) if where else ''
        # prefer the maintained latest table
        try:
            cur.execute(f'SELECT * FROM products_latest{where_sql}', params)
            rows = cur.fetchall()
            if rows:
                return _rows_to_human(rows)
        except Exception:
            pass
        # fallback: newest row per asin from history
        sub = """
        SELECT * FROM products p
        WHERE crawled_at = (
            SELECT MAX(crawled_at) FROM products x WHERE x.asin = p.asin
        )
        """
        if where:
            sub += ' AND ' + ' AND '.join(where)
            cur.execute(sub, params)
        else:
            cur.execute(sub)
        return _rows_to_human(cur.fetchall())
    finally:
        conn.close()


def fetch_run(crawl_run):
    """Return every row from a specific crawl run."""
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT * FROM products WHERE crawl_run = {PLACEHOLDER}", (crawl_run,)
        )
        return _rows_to_human(cur.fetchall())
    finally:
        conn.close()


def fetch_asin_history(asin):
    """Return all crawls of one ASIN over time (oldest first) — for diffing."""
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT * FROM products WHERE asin = {PLACEHOLDER} ORDER BY crawled_at",
            (asin,),
        )
        return _rows_to_human(cur.fetchall())
    finally:
        conn.close()


def stats():
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM products")
        total = _scalar(cur.fetchone())
        cur.execute("SELECT COUNT(DISTINCT asin) FROM products")
        asins = _scalar(cur.fetchone())
        cur.execute("SELECT COUNT(DISTINCT crawl_run) FROM products")
        runs = _scalar(cur.fetchone())
        return {"backend": backend_name(), "total_rows": total,
                "distinct_asins": asins, "crawl_runs": runs}
    finally:
        conn.close()


# --------------------------------------------------------------------------
# CSV import (push your EXISTING output into the DB without re-crawling)
# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# VALIDATIONS — the shared "who validated what" record
# --------------------------------------------------------------------------
# Goal: when ANYONE (Hazique / Naresh / Sagar) clicks "Mark Done" on an ASIN,
# it is saved here permanently. That ASIN then shows as DONE to everyone, so it
# is never validated twice. One row per ASIN (the latest validation wins), plus
# a full-history table so nothing is ever lost.
#
#   validations         -> one row per ASIN: is it done, by whom, when, and the
#                          full check results + notes (JSON).
#   validations_history -> append-only log of every "Mark Done" ever clicked.

def init_validations():
    """Create the validation tables. Safe to call repeatedly."""
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(f"""
        CREATE TABLE IF NOT EXISTS validations (
            asin {TEXT} PRIMARY KEY,
            brand {TEXT},
            is_done {TEXT},
            validated_by {TEXT},
            validated_at {TEXT},
            check_results {TEXT},
            notes {TEXT}
        );
        """)
        cur.execute(f"""
        CREATE TABLE IF NOT EXISTS validations_history (
            id {SERIAL},
            asin {TEXT},
            brand {TEXT},
            validated_by {TEXT},
            validated_at {TEXT},
            check_results {TEXT},
            notes {TEXT}
        );
        """)
        cur.execute('CREATE INDEX IF NOT EXISTS idx_val_asin ON validations (asin);')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_val_done ON validations (is_done);')
        conn.commit()
    finally:
        conn.close()


def mark_done(asin, validated_by, check_results=None, notes="", brand=""):
    """Record that `validated_by` has finished validating `asin`.

    check_results: dict/list of every check + its Yes/No/NotSure decision
                   (stored as JSON so the full record is kept for later review).
    Writes BOTH the latest-state row (validations) and an append-only log entry
    (validations_history). After this, the ASIN counts as done for everyone.
    """
    if not asin or not validated_by:
        raise ValueError("asin and validated_by are required")
    init_validations()
    asin = str(asin).strip()
    now = datetime.now(timezone.utc).isoformat()
    cr_json = json.dumps(check_results or {}, ensure_ascii=False)

    conn = _connect()
    try:
        cur = conn.cursor()
        # latest-state (upsert)
        fields = ["asin", "brand", "is_done", "validated_by",
                  "validated_at", "check_results", "notes"]
        vals = [asin, brand, "yes", validated_by, now, cr_json, notes or ""]
        quoted = ", ".join(f'"{f}"' for f in fields)
        marks = ", ".join([PLACEHOLDER] * len(fields))
        if _IS_PG:
            updates = ", ".join(f'"{f}" = EXCLUDED."{f}"'
                                for f in fields if f != "asin")
            sql = (f'INSERT INTO validations ({quoted}) VALUES ({marks}) '
                   f'ON CONFLICT (asin) DO UPDATE SET {updates}')
        else:
            sql = f'INSERT OR REPLACE INTO validations ({quoted}) VALUES ({marks})'
        cur.execute(sql, vals)
        # append-only history
        hf = ["asin", "brand", "validated_by", "validated_at",
              "check_results", "notes"]
        hq = ", ".join(f'"{f}"' for f in hf)
        hm = ", ".join([PLACEHOLDER] * len(hf))
        cur.execute(
            f'INSERT INTO validations_history ({hq}) VALUES ({hm})',
            [asin, brand, validated_by, now, cr_json, notes or ""],
        )
        conn.commit()
        return True
    finally:
        conn.close()


def is_done(asin):
    """True if this ASIN has already been validated by anyone."""
    init_validations()
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT is_done FROM validations WHERE asin = {PLACEHOLDER}",
            (str(asin).strip(),),
        )
        row = cur.fetchone()
        return _scalar(row) == "yes"
    finally:
        conn.close()


def get_validation(asin):
    """Return the full validation record for one ASIN (or None)."""
    init_validations()
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT * FROM validations WHERE asin = {PLACEHOLDER}",
            (str(asin).strip(),),
        )
        row = cur.fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["check_results"] = json.loads(d.get("check_results") or "{}")
        except Exception:
            pass
        return d
    finally:
        conn.close()


def list_done_asins():
    """Set of ASINs already validated — the validator hides these."""
    init_validations()
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT asin FROM validations WHERE is_done = 'yes'")
        return {_scalar(r) for r in cur.fetchall()}
    finally:
        conn.close()


def list_all_validations(brand=None):
    """Return EVERY validation record (full details) so any user can export the
    whole team's work. Each row: asin, brand, validated_by, validated_at,
    check_results (parsed), notes."""
    init_validations()
    conn = _connect()
    try:
        cur = conn.cursor()
        if brand:
            like = f"%{brand.lower()}%"
            cur.execute(
                f"SELECT asin, brand, is_done, validated_by, validated_at, "
                f"check_results, notes FROM validations "
                f"WHERE is_done='yes' AND LOWER(brand) LIKE {PLACEHOLDER}", (like,))
        else:
            cur.execute(
                "SELECT asin, brand, is_done, validated_by, validated_at, "
                "check_results, notes FROM validations WHERE is_done='yes'")
        out = []
        for r in cur.fetchall():
            d = dict(r) if isinstance(r, dict) else {
                "asin": r[0], "brand": r[1], "is_done": r[2],
                "validated_by": r[3], "validated_at": r[4],
                "check_results": r[5], "notes": r[6],
            }
            try:
                d["check_results"] = json.loads(d.get("check_results") or "{}")
            except Exception:
                pass
            out.append(d)
        return out
    finally:
        conn.close()


def validation_progress(brand=None):
    """Progress summary: how many ASINs done vs total, and per-validator counts.

    Combines the crawled product list (the universe of ASINs) with the
    validations table (what's done). Optionally filter by brand."""
    init_validations()
    products = fetch_latest(brand=brand)  # universe of ASINs to validate
    total = len(products)
    conn = _connect()
    try:
        cur = conn.cursor()
        if brand:
            like = f"%{brand.lower()}%"
            cur.execute(
                f"SELECT validated_by, COUNT(*) FROM validations "
                f"WHERE is_done='yes' AND LOWER(brand) LIKE {PLACEHOLDER} "
                f"GROUP BY validated_by", (like,))
        else:
            cur.execute("SELECT validated_by, COUNT(*) FROM validations "
                        "WHERE is_done='yes' GROUP BY validated_by")
        by_person = {}
        for r in cur.fetchall():
            if isinstance(r, dict):
                vals = list(r.values())
                name, count = vals[0], vals[1]
            else:
                name, count = r[0], r[1]
            by_person[name] = count
        done = sum(by_person.values())
        return {"total": total, "done": done, "remaining": total - done,
                "by_validator": by_person}
    finally:
        conn.close()


def import_csv(path, crawl_run=None):
    import pandas as pd
    df = pd.read_csv(path, dtype=str).fillna("")
    rows = df.to_dict(orient="records")
    n = save_rows(rows, crawl_run=crawl_run or "import_" +
                  datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S"))
    upsert_latest(rows, crawl_run=crawl_run)
    return n




# --------------------------------------------------------------------------
# Input sheet (validator reference values) — stored as JSON rows so the app
# can load it from the backend instead of every user uploading the xlsx.
# --------------------------------------------------------------------------
def init_input_sheet():
    """Create the input_sheet table. One row per (sheet upload) is overkill;
    we store the whole sheet as JSON under a single current row + keep history."""
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(f"""
        CREATE TABLE IF NOT EXISTS input_sheet (
            id {SERIAL},
            uploaded_at {TEXT},
            sheet_name {TEXT},
            columns_json {TEXT},
            rows_json {TEXT},
            is_current {TEXT}
        );
        """)
        cur.execute('CREATE INDEX IF NOT EXISTS idx_input_current ON input_sheet (is_current);')
        conn.commit()
    finally:
        conn.close()


def save_input_sheet(columns, rows, sheet_name="Format"):
    """Replace the current input sheet. columns=list[str], rows=list[dict]."""
    init_input_sheet()
    now = datetime.now(timezone.utc).isoformat()
    cols_json = json.dumps(list(columns), ensure_ascii=False)
    rows_json = json.dumps(rows, ensure_ascii=False)
    conn = _connect()
    try:
        cur = conn.cursor()
        # demote previous current rows
        cur.execute("UPDATE input_sheet SET is_current = 'no' WHERE is_current = 'yes'")
        fields = ["uploaded_at", "sheet_name", "columns_json", "rows_json", "is_current"]
        quoted = ", ".join(f'"{f}"' for f in fields)
        marks = ", ".join([PLACEHOLDER] * len(fields))
        cur.execute(
            f'INSERT INTO input_sheet ({quoted}) VALUES ({marks})',
            [now, sheet_name, cols_json, rows_json, "yes"],
        )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def get_input_sheet():
    """Return the current input sheet as {sheet_name, columns, rows, uploaded_at}
    or None if none uploaded yet."""
    init_input_sheet()
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT uploaded_at, sheet_name, columns_json, rows_json "
                    "FROM input_sheet WHERE is_current = 'yes' "
                    "ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
        if not row:
            return None
        d = dict(row) if isinstance(row, dict) else {
            "uploaded_at": row[0], "sheet_name": row[1],
            "columns_json": row[2], "rows_json": row[3],
        }
        return {
            "uploaded_at": d.get("uploaded_at"),
            "sheet_name": d.get("sheet_name"),
            "columns": json.loads(d.get("columns_json") or "[]"),
            "rows": json.loads(d.get("rows_json") or "[]"),
        }
    finally:
        conn.close()



# --------------------------------------------------------------------------
# Users & sessions
# --------------------------------------------------------------------------
# The validator's login used to be a plaintext table inside the React bundle,
# which meant every teammate's password (and the admin flag) was readable by
# anyone who opened the site's JavaScript. Passwords now live here, hashed, and
# the browser is handed an opaque session token instead.
#
# PBKDF2-HMAC-SHA256 from the standard library — no new dependency to install on
# Render, and correct for this purpose. Format: pbkdf2_sha256$rounds$salt$hash
_PBKDF2_ROUNDS = 240_000


def _hash_password(password, salt=None, rounds=_PBKDF2_ROUNDS):
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                             salt.encode("utf-8"), rounds)
    return f"pbkdf2_sha256${rounds}${salt}${dk.hex()}"


def _verify_password(password, stored):
    """Constant-time check. Returns False for anything malformed rather than
    raising, so a corrupt row can't turn into a 500 on the login route."""
    try:
        algo, rounds, salt, want = str(stored).split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        got = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                  salt.encode("utf-8"), int(rounds)).hex()
        return hmac.compare_digest(got, want)
    except Exception:
        return False


def init_users():
    """Create the users + sessions tables. Safe to call repeatedly."""
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(f"""
        CREATE TABLE IF NOT EXISTS users (
            name {TEXT} PRIMARY KEY,
            pw_hash {TEXT},
            is_admin {TEXT},
            created_at {TEXT},
            must_change {TEXT},
            email {TEXT}
        );
        """)
        # Older installs predate must_change/email. SQLite has no ADD COLUMN IF
        # NOT EXISTS, so try each and accept failure when it's already there.
        for col in ("must_change", "email"):
            try:
                cur.execute(f'ALTER TABLE users ADD COLUMN {col} {TEXT}')
                conn.commit()
            except Exception:
                conn.rollback()
        cur.execute(f"""
        CREATE TABLE IF NOT EXISTS sessions (
            token {TEXT} PRIMARY KEY,
            name {TEXT},
            created_at {TEXT},
            expires_at {TEXT}
        );
        """)
        cur.execute('CREATE INDEX IF NOT EXISTS idx_sess_name ON sessions (name);')
        conn.commit()
    finally:
        conn.close()


MIN_PASSWORD_LEN = 8


def create_user(name, password, is_admin=False, must_change=True, email=None):
    """Add or replace a user.

    must_change defaults to True: a password someone else typed is a temporary
    one, and the owner should replace it before doing any work. It is cleared
    only by set_own_password().

    email=None on an existing user keeps whatever address is already stored, so
    a password change never silently drops someone's login address.
    """
    name = str(name).strip()
    if not name or not password:
        raise ValueError("name and password are required")
    init_users()
    if email is None:
        prev = _user_row(name)
        email = (prev or {}).get("email") or ""
    email = str(email).strip().lower()
    conn = _connect()
    try:
        cur = conn.cursor()
        row = [name, _hash_password(password), "yes" if is_admin else "no",
               datetime.now(timezone.utc).isoformat(),
               "yes" if must_change else "no", email]
        if _IS_PG:
            cur.execute(
                'INSERT INTO users (name, pw_hash, is_admin, created_at, must_change, email) '
                'VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (name) DO UPDATE SET '
                'pw_hash=EXCLUDED.pw_hash, is_admin=EXCLUDED.is_admin, '
                'must_change=EXCLUDED.must_change, email=EXCLUDED.email', row)
        else:
            cur.execute('INSERT OR REPLACE INTO users '
                        '(name, pw_hash, is_admin, created_at, must_change, email) '
                        'VALUES (?,?,?,?,?,?)', row)
        conn.commit()
        return name
    finally:
        conn.close()


def set_email(name, email):
    """Attach a login email to an existing user."""
    init_users()
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(f'UPDATE users SET email = {PLACEHOLDER} WHERE name = {PLACEHOLDER}',
                    (str(email).strip().lower(), str(name).strip()))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def set_own_password(name, current_password, new_password):
    """A user replacing their own password. Requires the current one, so a
    stolen session token alone can't lock the owner out of their account.
    Clears must_change and drops every other session for that user."""
    if not new_password or len(new_password) < MIN_PASSWORD_LEN:
        raise ValueError(f"New password must be at least {MIN_PASSWORD_LEN} characters.")
    if new_password == current_password:
        raise ValueError("New password must be different from the current one.")
    user = verify_user(name, current_password)
    if not user:
        raise PermissionError("Current password is incorrect.")
    # Resolve to the canonical name — `name` may have arrived as an email.
    name = user["name"]
    create_user(name, new_password, is_admin=user["is_admin"], must_change=False,
                email=user.get("email") or None)
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(f'DELETE FROM sessions WHERE name = {PLACEHOLDER}', (str(name).strip(),))
        conn.commit()
    finally:
        conn.close()
    return True


def must_change_password(name):
    row = _user_row(name)
    return bool(row) and row.get("must_change") == "yes"


def _user_row(name):
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(f'SELECT name, pw_hash, is_admin, must_change, email FROM users '
                    f'WHERE name = {PLACEHOLDER}', (str(name).strip(),))
        r = cur.fetchone()
        if not r:
            return None
        if isinstance(r, dict):
            return dict(r)
        return {"name": r[0], "pw_hash": r[1], "is_admin": r[2],
                "must_change": r[3], "email": r[4]}
    finally:
        conn.close()


def _user_row_by_login(login):
    """Resolve a sign-in identifier. Accepts the email address or the display
    name, both case-insensitively — people type their name inconsistently and
    an email is easier to remember than an exact spelling of 'Sagar Sakapl'."""
    login = str(login or "").strip().lower()
    if not login:
        return None
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute('SELECT name, pw_hash, is_admin, must_change, email FROM users')
        rows = cur.fetchall()
    finally:
        conn.close()
    for r in rows:
        d = dict(r) if isinstance(r, dict) else {
            "name": r[0], "pw_hash": r[1], "is_admin": r[2],
            "must_change": r[3], "email": r[4]}
        if str(d.get("email") or "").strip().lower() == login:
            return d
        if str(d.get("name") or "").strip().lower() == login:
            return d
    return None


def verify_user(login, password):
    """Return {name, email, is_admin, must_change} when the password matches
    the account identified by `login` (email or name), else None."""
    init_users()
    row = _user_row_by_login(login)
    if not row or not _verify_password(password or "", row.get("pw_hash")):
        return None
    return {"name": row["name"], "email": row.get("email") or "",
            "is_admin": row.get("is_admin") == "yes",
            "must_change": row.get("must_change") == "yes"}


def list_users():
    init_users()
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT name, is_admin, created_at, must_change, email "
                    "FROM users ORDER BY name")
        out = []
        for r in cur.fetchall():
            d = dict(r) if isinstance(r, dict) else {
                "name": r[0], "is_admin": r[1], "created_at": r[2],
                "must_change": r[3], "email": r[4]}
            out.append({"name": d["name"], "email": d.get("email") or "",
                        "is_admin": d.get("is_admin") == "yes",
                        "must_change": d.get("must_change") == "yes",
                        "created_at": d.get("created_at")})
        return out
    finally:
        conn.close()


def delete_user(name):
    init_users()
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(f'DELETE FROM sessions WHERE name = {PLACEHOLDER}', (str(name).strip(),))
        cur.execute(f'DELETE FROM users WHERE name = {PLACEHOLDER}', (str(name).strip(),))
        conn.commit()
    finally:
        conn.close()


def create_session(name, days=30):
    """Issue an opaque bearer token. Old sessions stay valid until they expire."""
    init_users()
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            f'INSERT INTO sessions (token, name, created_at, expires_at) '
            f'VALUES ({PLACEHOLDER},{PLACEHOLDER},{PLACEHOLDER},{PLACEHOLDER})',
            (token, str(name).strip(), now.isoformat(),
             (now + timedelta(days=days)).isoformat()))
        conn.commit()
        return token
    finally:
        conn.close()


def session_user(token):
    """Resolve a bearer token to {name, is_admin}, or None if unknown/expired."""
    if not token:
        return None
    init_users()
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(f'SELECT name, expires_at FROM sessions WHERE token = {PLACEHOLDER}',
                    (str(token).strip(),))
        r = cur.fetchone()
        if not r:
            return None
        d = dict(r) if isinstance(r, dict) else {"name": r[0], "expires_at": r[1]}
        try:
            if datetime.fromisoformat(d["expires_at"]) < datetime.now(timezone.utc):
                return None
        except Exception:
            return None
    finally:
        conn.close()
    row = _user_row(d["name"])
    if not row:
        return None
    return {"name": row["name"], "is_admin": row.get("is_admin") == "yes",
            "must_change": row.get("must_change") == "yes"}


def delete_session(token):
    init_users()
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(f'DELETE FROM sessions WHERE token = {PLACEHOLDER}', (str(token).strip(),))
        conn.commit()
    finally:
        conn.close()


def clear_validations():
    """Delete ALL validation records (and history) so the team starts fresh at 0.
    Products and input sheet are untouched."""
    init_validations()
    conn=_connect()
    try:
        cur=conn.cursor()
        cur.execute("DELETE FROM validations")
        try:
            cur.execute("DELETE FROM validations_history")
        except Exception:
            pass
        conn.commit()
        return True
    finally:
        conn.close()


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print("Commands: init | import-csv <path> | import-input <path.xlsx> [sheet] | show-input | show <asin> | run <crawl_run> | stats")
        print("Backend:", backend_name())
        sys.exit(0)

    cmd = args[0]
    if cmd == "init":
        init_db()
        print("Initialized.", backend_name())
    elif cmd == "import-csv":
        n = import_csv(args[1])
        print(f"Imported {n} rows into {backend_name()}")
    elif cmd == "show":
        for r in fetch_asin_history(args[1]):
            print(json.dumps(r, indent=2, ensure_ascii=False))
    elif cmd == "run":
        rows = fetch_run(args[1])
        print(f"{len(rows)} rows in run {args[1]}")
    elif cmd == "stats":
        print(json.dumps(stats(), indent=2))
    elif cmd == "progress":
        brand = args[1] if len(args) > 1 else None
        print(json.dumps(validation_progress(brand), indent=2))
    elif cmd == "done-list":
        print(sorted(list_done_asins()))
    elif cmd == "mark-done":
        # python hygiene_db.py mark-done <asin> <validated_by> [notes]
        notes = args[3] if len(args) > 3 else ""
        mark_done(args[1], args[2], notes=notes)
        print(f"Marked {args[1]} done by {args[2]}")
    elif cmd == "check-done":
        print(args[1], "->", "DONE" if is_done(args[1]) else "not done")
    elif cmd == "import-input":
        # python hygiene_db.py import-input <path.xlsx> [sheet_name]
        import pandas as pd
        path = args[1]
        sheet = args[2] if len(args) > 2 else 0
        df = pd.read_excel(path, sheet_name=sheet, dtype=str).fillna("")
        cols = list(df.columns)
        rows = df.to_dict(orient="records")
        n = save_input_sheet(cols, rows,
                             sheet_name=(sheet if isinstance(sheet, str) else "Format"))
        print(f"Imported input sheet: {n} rows, {len(cols)} cols into {backend_name()}")
    elif cmd == "clear-validations":
        clear_validations()
        print(f"Cleared ALL validations into {backend_name()} — everyone starts at 0.")
    elif cmd == "add-user":
        # python hygiene_db.py add-user "Naresh More" <password> [admin]
        # Passwords are hashed here and never stored or shipped in plaintext.
        is_admin = len(args) > 3 and args[3].lower() in ("admin", "yes", "true", "1")
        create_user(args[1], args[2], is_admin=is_admin)
        print(f"User '{args[1]}' saved{' (admin)' if is_admin else ''} in {backend_name()}")
    elif cmd == "set-password":
        # python hygiene_db.py set-password "Naresh More" <new-password>
        existing = _user_row(args[1])
        if not existing:
            print(f"No such user: {args[1]}")
        else:
            create_user(args[1], args[2], is_admin=existing.get("is_admin") == "yes")
            print(f"Password updated for '{args[1]}'")
    elif cmd == "list-users":
        us = list_users()
        if not us:
            print("No users yet. Add one:  python hygiene_db.py add-user \"Name\" <password> [admin]")
        for u in us:
            flags = []
            if u["is_admin"]:
                flags.append("admin")
            flags.append("must set own password" if u.get("must_change")
                         else "own password set")
            em = u.get("email") or "NO EMAIL SET"
            print(f"  {u['name']:<20} {em:<32} ({', '.join(flags)})")
    elif cmd == "set-email":
        # python hygiene_db.py set-email "Naresh More" naresh@cambiumretail.com
        n = set_email(args[1], args[2])
        print(f"{'Set' if n else 'NO SUCH USER:'} {args[1]} -> {args[2]}")
    elif cmd == "reset-to-shared":
        # python hygiene_db.py reset-to-shared <password> ["Name" ...]
        # Puts the named users (or everyone) back on a shared first-time
        # password and re-arms must_change, so each must pick their own again.
        pw = args[1]
        targets = args[2:] or [u["name"] for u in list_users()]
        for n in targets:
            row = _user_row(n)
            if not row:
                print(f"  no such user: {n}")
                continue
            create_user(n, pw, is_admin=row.get("is_admin") == "yes",
                        must_change=True)
            print(f"  {n} -> shared password, must set own at next login")
    elif cmd == "seed-users":
        # python hygiene_db.py seed-users "Name A" "Name B:admin" ...
        # Generates a temporary password for each and prints it ONCE. Every user
        # must replace it at first login, so these are safe to send over chat.
        import secrets as _s
        words = ("amber blue coral delta ember frost grove ivory jade lunar "
                 "maple noble opal quartz river slate topaz umber vivid willow").split()
        made = []
        for spec in args[1:]:
            nm_, _, flag = spec.partition(":")
            tmp = f"{_s.choice(words).capitalize()}-{_s.choice(words)}-{_s.randbelow(9000)+1000}"
            create_user(nm_.strip(), tmp, is_admin=flag.strip().lower() == "admin",
                        must_change=True)
            made.append((nm_.strip(), tmp, flag.strip().lower() == "admin"))
        print(f"\nCreated {len(made)} user(s) in {backend_name()}.")
        print("Give each person their temporary password. They MUST set their own")
        print("at first login — these stop working the moment they do.\n")
        for nm_, tmp, adm in made:
            print(f"  {nm_}{' (admin)' if adm else ''}\n      temporary password: {tmp}")
        print()
    elif cmd == "del-user":
        delete_user(args[1])
        print(f"Removed user '{args[1]}' and any active sessions")
    elif cmd == "show-input":
        d = get_input_sheet()
        if not d:
            print("No input sheet stored.")
        else:
            print(f"sheet={d['sheet_name']} rows={len(d['rows'])} "
                  f"cols={len(d['columns'])} uploaded_at={d['uploaded_at']}")
    else:
        print("Unknown command:", cmd)
