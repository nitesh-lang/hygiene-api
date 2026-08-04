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
# Product specs (OUR OWN dimensions + packed weight)
# --------------------------------------------------------------------------
# Why this table exists at all: the validator already had Weight and Dimensions
# checks, but the "reference" side came from the input sheet — and that sheet's
# Item Weight (g) / Dimensions columns were themselves filled in FROM Amazon
# (188 of 214 comparable rows are byte-identical to the crawled PDP). So those
# checks were comparing Amazon against Amazon and passing by construction.
#
# This table is the first genuinely independent record: what WE measured. Amazon
# bills FBA storage and fulfilment on the greater of actual and volumetric
# weight, so an Amazon-side re-measure that inflates a box silently raises fees.
# Comparing the two sides is the only way to catch that.
#
# Everything is TEXT for consistency with the rest of the schema, but values are
# NORMALISED on the way in: centimetres and kilograms, always.
SPEC_FIELDS = ["asin", "length_cm", "breadth_cm", "height_cm", "weight_kg",
               "source", "updated_at", "notes"]

# Amazon India divides cm³ by 5000 to get volumetric ("dimensional") weight in kg.
# Kept as a named constant because it is a policy number, not a fact — if Amazon
# changes the divisor, or this is ever used for another marketplace, this is the
# single place to change. The frontend has the same constant in App.jsx.
VOLUMETRIC_DIVISOR = 5000.0


def volumetric_kg(length_cm, breadth_cm, height_cm, divisor=VOLUMETRIC_DIVISOR):
    """Volumetric weight in kg from centimetres, or None if any side is missing."""
    try:
        l, b, h = float(length_cm), float(breadth_cm), float(height_cm)
    except (TypeError, ValueError):
        return None
    if l <= 0 or b <= 0 or h <= 0:
        return None
    return round(l * b * h / divisor, 3)


def chargeable_kg(actual_kg, volumetric):
    """What Amazon actually bills on: the GREATER of actual and volumetric."""
    vals = []
    for v in (actual_kg, volumetric):
        try:
            f = float(v)
            if f > 0:
                vals.append(f)
        except (TypeError, ValueError):
            pass
    return round(max(vals), 3) if vals else None


def init_product_specs():
    """Create the product_specs table. Safe to call repeatedly."""
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(f"""
        CREATE TABLE IF NOT EXISTS product_specs (
            asin {TEXT} PRIMARY KEY,
            length_cm {TEXT},
            breadth_cm {TEXT},
            height_cm {TEXT},
            weight_kg {TEXT},
            source {TEXT},
            updated_at {TEXT},
            notes {TEXT}
        );
        """)
        conn.commit()
    finally:
        conn.close()


def save_product_specs(rows, source=""):
    """Upsert our reference specs. rows = list of dicts with the SPEC_FIELDS keys
    (asin required). Partial rows are allowed — an ASIN whose weight is not known
    yet still stores its dimensions, and the UI just shows the weight as pending.

    Upserts rather than replaces, so importing a second file that covers more
    ASINs adds to the set instead of wiping the ASINs it does not mention.
    """
    if not rows:
        return 0
    init_product_specs()
    now = datetime.now(timezone.utc).isoformat()
    conn = _connect()
    try:
        cur = conn.cursor()
        n = 0
        for r in rows:
            asin = str(r.get("asin", "")).strip().upper()
            if not asin:
                continue
            vals = [asin,
                    str(r.get("length_cm", "") or ""),
                    str(r.get("breadth_cm", "") or ""),
                    str(r.get("height_cm", "") or ""),
                    str(r.get("weight_kg", "") or ""),
                    str(r.get("source", "") or source or ""),
                    now,
                    str(r.get("notes", "") or "")]
            quoted = ", ".join(f'"{f}"' for f in SPEC_FIELDS)
            marks = ", ".join([PLACEHOLDER] * len(SPEC_FIELDS))
            if _IS_PG:
                updates = ", ".join(f'"{f}" = EXCLUDED."{f}"'
                                    for f in SPEC_FIELDS if f != "asin")
                sql = (f'INSERT INTO product_specs ({quoted}) VALUES ({marks}) '
                       f'ON CONFLICT (asin) DO UPDATE SET {updates}')
            else:
                sql = f'INSERT OR REPLACE INTO product_specs ({quoted}) VALUES ({marks})'
            cur.execute(sql, vals)
            n += 1
        conn.commit()
        return n
    finally:
        conn.close()


def get_product_specs(asins=None):
    """Return our reference specs as a list of dicts. asins=None returns all."""
    init_product_specs()
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT asin, length_cm, breadth_cm, height_cm, weight_kg, "
                    "source, updated_at, notes FROM product_specs ORDER BY asin")
        out = []
        for row in cur.fetchall():
            d = dict(row) if isinstance(row, dict) else dict(zip(SPEC_FIELDS, row))
            out.append(d)
        if asins:
            want = {str(a).strip().upper() for a in asins}
            out = [d for d in out if d["asin"] in want]
        return out
    finally:
        conn.close()


def delete_product_spec(asin):
    init_product_specs()
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(f'DELETE FROM product_specs WHERE asin = {PLACEHOLDER}',
                    (str(asin).strip().upper(),))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


# ── spec-sheet importer ──────────────────────────────────────────────────────
# Deliberately tolerant about column NAMING and UNITS, because the whole point is
# that when a corrected sheet arrives the only thing that changes is the file —
# not this code. It finds the ASIN column, then the first L / B / H / weight
# columns after it, reads the unit out of the header ("L(cm)", "Weight Kg",
# "Height (mm)"), and normalises to cm/kg.
_LEN_UNITS = [(("mm", "millimet"), 0.1), (("cm", "centimet"), 1.0),
              (("inch", '"', " in"), 2.54), (("meter", "metre"), 100.0)]
_WT_UNITS = [(("kg", "kilogram"), 1.0), (("gram", " g", "(g)", "gm"), 0.001)]


def _hdr_unit(header, table, default):
    """Pull a unit multiplier out of a column header. 'L(cm)' -> 1.0."""
    h = f" {str(header).lower().strip()} "
    for keys, mult in table:
        if any(k in h for k in keys):
            return mult
    return default


def _spec_num(value, mult):
    """A cell -> a normalised float, or None. Tolerates '36 cm', '36.0', 36."""
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() in ("nan", "none", "-", "na", "n/a"):
        return None
    import re
    m = re.search(r"\d+(?:\.\d+)?", s.replace(",", ""))
    if not m:
        return None
    # A unit written in the CELL beats the one in the header ("4 kg" in a g column).
    low = s.lower()
    if mult in (1.0, 0.001):        # weight column
        if "kg" in low or "kilo" in low:
            mult = 1.0
        elif "gram" in low or re.search(r"\d\s*g\b", low):
            mult = 0.001
    else:                            # length column
        if "mm" in low:
            mult = 0.1
        elif "cm" in low:
            mult = 1.0
        elif "inch" in low or '"' in low:
            mult = 2.54
    return round(float(m.group(0)) * mult, 3)


def _find_spec_columns(headers):
    """Map headers -> {asin, l, b, h, w} column indexes, plus every candidate
    found. Takes the FIRST match after the ASIN column for each of L/B/H/W: a
    sheet that carries both our numbers and Amazon's (as the 2026-08 one does)
    lists ours first, and guessing from labels like 'Actual' is unreliable —
    it is genuinely ambiguous which side that word describes. The extras are
    returned so the caller can print them and the operator can override."""
    import re
    low = [str(h or "").strip().lower() for h in headers]

    def matches(i, *pats):
        h = low[i]
        return any(re.search(p, h) for p in pats)

    asin_i = next((i for i in range(len(low)) if low[i] == "asin"), None)
    if asin_i is None:
        asin_i = next((i for i in range(len(low)) if "asin" in low[i]), None)
    if asin_i is None:
        raise ValueError("No ASIN column found. Expected a column headed 'ASIN'.")

    cand = {"l": [], "b": [], "h": [], "w": []}
    for i in range(len(low)):
        if i == asin_i or not low[i]:
            continue
        # weight first — "Weight Kg" must not be read as a length
        if matches(i, r"weight", r"^wt\b", r"^wt[\s(]"):
            cand["w"].append(i)
        elif matches(i, r"^l\b", r"^l[\s(]", r"length"):
            cand["l"].append(i)
        elif matches(i, r"^b\b", r"^b[\s(]", r"breadth", r"^w\b", r"^w[\s(]", r"width"):
            cand["b"].append(i)
        elif matches(i, r"^h\b", r"^h[\s(]", r"height"):
            cand["h"].append(i)

    after = lambda lst: next((i for i in lst if i > asin_i), (lst[0] if lst else None))
    return {"asin": asin_i, "l": after(cand["l"]), "b": after(cand["b"]),
            "h": after(cand["h"]), "w": after(cand["w"])}, cand


def parse_spec_sheet(path, sheet=0, overrides=None):
    """Read a dimensions/weight workbook into normalised spec dicts.

    Returns (rows, report). `report` describes what was matched so the operator
    can see which columns were used and catch a mis-detected sheet immediately
    instead of after the numbers are already in the database.
    """
    import pandas as pd
    raw = pd.read_excel(path, sheet_name=sheet, header=None, dtype=object)
    raw = raw.where(pd.notnull(raw), None)
    grid = raw.values.tolist()
    if not grid:
        raise ValueError("Sheet is empty.")

    # Header row = the first row in the top 8 that mentions an ASIN column.
    hdr_i = None
    for i, row in enumerate(grid[:8]):
        if any("asin" in str(c or "").strip().lower() for c in row):
            hdr_i = i
            break
    if hdr_i is None:
        raise ValueError("No header row with an 'ASIN' column in the first 8 rows.")
    headers = [str(c).strip() if c is not None else "" for c in grid[hdr_i]]

    idx, cand = _find_spec_columns(headers)
    if overrides:
        idx.update({k: v for k, v in overrides.items() if v is not None})
    missing = [k for k in ("l", "b", "h") if idx.get(k) is None]

    lm = {k: _hdr_unit(headers[idx[k]], _LEN_UNITS, 1.0)
          for k in ("l", "b", "h") if idx.get(k) is not None}
    wm = _hdr_unit(headers[idx["w"]], _WT_UNITS, 1.0) if idx.get("w") is not None else None

    rows, skipped = [], 0
    for r in grid[hdr_i + 1:]:
        if idx["asin"] >= len(r):
            continue
        asin = str(r[idx["asin"]] or "").strip().upper()
        if len(asin) < 5:
            continue
        get = lambda k, mult: (_spec_num(r[idx[k]], mult)
                               if idx.get(k) is not None and idx[k] < len(r) else None)
        L = get("l", lm.get("l", 1.0))
        B = get("b", lm.get("b", 1.0))
        H = get("h", lm.get("h", 1.0))
        W = get("w", wm) if wm is not None else None
        if L is None and B is None and H is None and W is None:
            skipped += 1          # a row Sagar has not filled in yet
            continue
        rows.append({"asin": asin,
                     "length_cm": "" if L is None else L,
                     "breadth_cm": "" if B is None else B,
                     "height_cm": "" if H is None else H,
                     "weight_kg": "" if W is None else W,
                     "source": os.path.basename(str(path))})

    used = {k: (headers[idx[k]] if idx.get(k) is not None else None)
            for k in ("asin", "l", "b", "h", "w")}
    extras = {k: [headers[i] for i in v[1:]] for k, v in cand.items() if len(v) > 1}
    return rows, {"header_row": hdr_i + 1, "columns_used": used,
                  "other_candidates": extras, "rows": len(rows),
                  "blank_rows_skipped": skipped, "missing": missing}


def import_spec_sheet(path, sheet=0, overrides=None):
    rows, report = parse_spec_sheet(path, sheet=sheet, overrides=overrides)
    n = save_product_specs(rows, source=os.path.basename(str(path)))
    report["imported"] = n
    return report


# --------------------------------------------------------------------------
# Amazon-side changes (from the append-only crawl history)
# --------------------------------------------------------------------------
def spec_changes(fields=("dimensions", "weight"), brand=None):
    """Per ASIN, the previous vs current crawled value for each field and when it
    moved — this is the "what did Amazon change" half of the fee-risk question.

    Reads `products`, which is append-only across crawl runs, so it needs at
    least two runs covering an ASIN to report anything. With a single run it
    correctly returns nothing rather than inventing a baseline.
    """
    init_db()
    conn = _connect()
    try:
        cur = conn.cursor()
        cols = ", ".join(f'"{f}"' for f in fields)
        sql = (f'SELECT asin, brand, crawl_run, crawled_at, {cols} '
               f'FROM products ORDER BY asin, crawled_at')
        cur.execute(sql)
        by_asin = {}
        for row in cur.fetchall():
            d = dict(row) if isinstance(row, dict) else dict(
                zip(["asin", "brand", "crawl_run", "crawled_at"] + list(fields), row))
            by_asin.setdefault(str(d.get("asin") or "").strip().upper(), []).append(d)
    finally:
        conn.close()

    out = {}
    for asin, hist in by_asin.items():
        if not asin or len(hist) < 2:
            continue
        if brand and brand.lower() not in str(hist[-1].get("brand") or "").lower():
            continue
        changes = {}
        for f in fields:
            # Walk back from the newest to the most recent DIFFERENT value, so a
            # change is still reported after several unchanged crawls have run
            # on top of it.
            cur_v = str(hist[-1].get(f) or "").strip()
            prev = next((h for h in reversed(hist[:-1])
                         if str(h.get(f) or "").strip() != cur_v), None)
            if prev is None:
                continue
            changes[f] = {"from": str(prev.get(f) or "").strip(),
                          "to": cur_v,
                          "changed_after": prev.get("crawled_at"),
                          "seen_at": hist[-1].get("crawled_at")}
        if changes:
            out[asin] = changes
    return out


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
        # Previous password hashes, so the monthly rotation can't be dodged by
        # flipping between the same two passwords. Only hashes are kept — a row
        # here can no more reveal a password than the users table can.
        cur.execute(f"""
        CREATE TABLE IF NOT EXISTS password_history (
            id {SERIAL},
            name {TEXT},
            pw_hash {TEXT},
            retired_at {TEXT}
        );
        """)
        cur.execute('CREATE INDEX IF NOT EXISTS idx_pwhist_name ON password_history (name);')
        # One row per (job, period) so a scheduled job that fires twice in the
        # same month — a missed run catching up, a manual run, a retry — does
        # its work exactly once.
        cur.execute(f"""
        CREATE TABLE IF NOT EXISTS job_runs (
            job {TEXT},
            period {TEXT},
            ran_at {TEXT},
            detail {TEXT},
            PRIMARY KEY (job, period)
        );
        """)
        conn.commit()
    finally:
        conn.close()


MIN_PASSWORD_LEN = 8

# How many old passwords a user may not go back to. Six covers half a year of
# monthly rotations, which is long enough that reusing one is a real decision
# rather than muscle memory.
PASSWORD_HISTORY_DEPTH = 6


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


def _password_history(name):
    """The stored hashes of this user's previous passwords, newest first."""
    init_users()
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(f'SELECT pw_hash FROM password_history WHERE name = {PLACEHOLDER} '
                    f'ORDER BY retired_at DESC', (str(name).strip(),))
        return [(dict(r)["pw_hash"] if isinstance(r, dict) else r[0])
                for r in cur.fetchall()]
    finally:
        conn.close()


def remember_password(name, pw_hash):
    """File a hash in the history and drop anything past the depth limit, so
    the table stays a fixed handful of rows per person instead of growing
    forever. Duplicates are skipped — re-arming twice in a month must not push
    a genuinely old password out of the window."""
    if not pw_hash:
        return
    init_users()
    name = str(name).strip()
    if pw_hash in _password_history(name):
        return
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            f'INSERT INTO password_history (name, pw_hash, retired_at) '
            f'VALUES ({PLACEHOLDER},{PLACEHOLDER},{PLACEHOLDER})',
            (name, pw_hash, datetime.now(timezone.utc).isoformat()))
        conn.commit()
    finally:
        conn.close()
    keep = _password_history(name)[:PASSWORD_HISTORY_DEPTH]
    if not keep:
        return
    conn = _connect()
    try:
        cur = conn.cursor()
        marks = ",".join([PLACEHOLDER] * len(keep))
        cur.execute(f'DELETE FROM password_history WHERE name = {PLACEHOLDER} '
                    f'AND pw_hash NOT IN ({marks})', tuple([name] + keep))
        conn.commit()
    finally:
        conn.close()


def _is_reused_password(name, new_password):
    """True when the candidate matches one of the recent hashes. Each stored
    hash carries its own salt, so this is a verify per entry rather than a
    lookup — at most PASSWORD_HISTORY_DEPTH of them, on password change only."""
    return any(_verify_password(new_password, h) for h in _password_history(name))


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
    if _is_reused_password(name, new_password):
        raise ValueError(
            f"You have used this password before. Pick one you have not used in "
            f"your last {PASSWORD_HISTORY_DEPTH} passwords.")
    # Retire the outgoing password before overwriting it, otherwise the hash is
    # gone and next month it counts as new again.
    old = _user_row(name)
    if old:
        remember_password(name, old.get("pw_hash"))
    create_user(name, new_password, is_admin=user["is_admin"], must_change=False,
                email=user.get("email") or None)
    remember_password(name, _user_row(name).get("pw_hash"))
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


# --------------------------------------------------------------------------
# Monthly password rotation
# --------------------------------------------------------------------------
# On the 2nd of every month the team has to choose a new password. This does
# NOT set passwords for them and never has: it raises the must_change flag that
# already gates the app, so each person signs in with the password they know and
# picks their own replacement. Nobody — including whoever runs the job — ever
# sees or types someone else's password.
#
# Deliberately narrow. It writes to `users.must_change`, `password_history` and
# `job_runs`, and to nothing else. Products, validations, validation history and
# the input sheet are never opened, and live sessions are left alone: /me
# reports must_change, so the app raises the change-password screen on the next
# page load without signing anyone out of work they haven't synced yet.
MONTHLY_RESET_JOB = "monthly-password-reset"


def current_period(when=None):
    """The 'YYYY-MM' bucket a run belongs to, from LOCAL time — the schedule
    that triggers it is local, so the label should agree with the calendar on
    the wall rather than with UTC."""
    when = when or datetime.now()
    return f"{when.year:04d}-{when.month:02d}"


def get_job_run(job, period):
    init_users()
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(f'SELECT job, period, ran_at, detail FROM job_runs '
                    f'WHERE job = {PLACEHOLDER} AND period = {PLACEHOLDER}',
                    (job, period))
        r = cur.fetchone()
        if not r:
            return None
        return dict(r) if isinstance(r, dict) else {
            "job": r[0], "period": r[1], "ran_at": r[2], "detail": r[3]}
    finally:
        conn.close()


def record_job_run(job, period, detail=""):
    init_users()
    conn = _connect()
    try:
        cur = conn.cursor()
        row = (job, period, datetime.now(timezone.utc).isoformat(), str(detail))
        if _IS_PG:
            cur.execute('INSERT INTO job_runs (job, period, ran_at, detail) '
                        'VALUES (%s,%s,%s,%s) ON CONFLICT (job, period) DO UPDATE '
                        'SET ran_at=EXCLUDED.ran_at, detail=EXCLUDED.detail', row)
        else:
            cur.execute('INSERT OR REPLACE INTO job_runs (job, period, ran_at, detail) '
                        'VALUES (?,?,?,?)', row)
        conn.commit()
    finally:
        conn.close()


def _arm_user(name):
    """Raise must_change for one user WITHOUT touching their password hash."""
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(f"UPDATE users SET must_change = 'yes' WHERE name = {PLACEHOLDER}",
                    (str(name).strip(),))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def run_monthly_password_reset(period=None, only=None, force=False, dry_run=False):
    """Require every user to choose a new password this month.

    Returns a summary dict; never raises for an ordinary condition, so a
    scheduled run either reports what it did or reports why it did nothing.

    force=False makes this idempotent per month. A scheduler catching up on a
    missed run, a retry, or someone running it by hand on the 5th must not
    re-arm people who have already chosen their new password.
    """
    init_users()
    period = period or current_period()
    prev = get_job_run(MONTHLY_RESET_JOB, period)
    if prev and not force:
        return {"period": period, "already_ran": True, "ran_at": prev.get("ran_at"),
                "detail": prev.get("detail", ""), "armed": [], "already_pending": [],
                "missing": [], "dry_run": dry_run}

    roster = list_users()
    if only:
        wanted = {str(n).strip().lower() for n in only}
        missing = [n for n in only
                   if str(n).strip().lower() not in
                   {u["name"].strip().lower() for u in roster}
                   and str(n).strip().lower() not in
                   {(u.get("email") or "").strip().lower() for u in roster}]
        roster = [u for u in roster
                  if u["name"].strip().lower() in wanted
                  or (u.get("email") or "").strip().lower() in wanted]
    else:
        missing = []

    armed, already = [], []
    for u in roster:
        if u.get("must_change"):
            # Still owes us a password from last time — leave the flag alone
            # rather than reporting it as freshly armed.
            already.append(u["name"])
            continue
        if not dry_run:
            row = _user_row(u["name"])
            if row:
                # Keep the outgoing hash so this month's choice can't be one of
                # the last few. Their password does not change here.
                remember_password(u["name"], row.get("pw_hash"))
            _arm_user(u["name"])
        armed.append(u["name"])

    detail = (f"armed={len(armed)} already_pending={len(already)} "
              f"total={len(roster)}")
    if not dry_run:
        record_job_run(MONTHLY_RESET_JOB, period, detail)
    return {"period": period, "already_ran": False, "armed": armed,
            "already_pending": already, "missing": missing, "detail": detail,
            "dry_run": dry_run,
            "ran_at": datetime.now(timezone.utc).isoformat()}


def password_reset_status(period=None):
    """What the current month looks like: whether the job has run and who has
    yet to choose a new password."""
    init_users()
    period = period or current_period()
    run = get_job_run(MONTHLY_RESET_JOB, period)
    roster = list_users()
    return {"period": period,
            "ran": bool(run),
            "ran_at": (run or {}).get("ran_at"),
            "detail": (run or {}).get("detail", ""),
            "pending": [u["name"] for u in roster if u.get("must_change")],
            "done": [u["name"] for u in roster if not u.get("must_change")],
            "total": len(roster)}


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
        print("  specs:  import-specs <path.xlsx> [sheet] [--l=C --b=D --h=E --w=F]")
        print("          list-specs | spec-changes [brand] | spec-template <brand> [out.xlsx]")
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
    elif cmd == "import-specs":
        # python hygiene_db.py import-specs "Dimension - Weight.xlsx" [sheet] [--l=C --b=D --h=E --w=F]
        # Our OWN measured dimensions + packed weight. Column names and units are
        # auto-detected, so a re-issued sheet needs no code change — but what was
        # detected is printed every time, because silently importing the wrong
        # block of a two-block sheet would invert every discrepancy flag.
        path = args[1]
        flags = {a.split("=")[0][2:]: a.split("=", 1)[1]
                 for a in args[2:] if a.startswith("--") and "=" in a}
        positional = [a for a in args[2:] if not a.startswith("--")]
        sheet = positional[0] if positional else 0

        def _col(letter):
            """'C' or '2' -> a 0-based column index."""
            s = str(letter).strip()
            if s.isdigit():
                return int(s)
            n = 0
            for ch in s.upper():
                n = n * 26 + (ord(ch) - 64)
            return n - 1

        overrides = {k: _col(v) for k, v in flags.items()
                     if k in ("asin", "l", "b", "h", "w")}
        rep = import_spec_sheet(path, sheet=sheet, overrides=overrides or None)
        print(f"Imported {rep['imported']} spec rows into {backend_name()}")
        print(f"  header row      : {rep['header_row']}")
        for k, v in rep["columns_used"].items():
            print(f"  {k:<15} -> {v if v is not None else 'NOT FOUND'}")
        if rep["blank_rows_skipped"]:
            print(f"  blank rows skipped: {rep['blank_rows_skipped']} "
                  f"(ASINs not measured yet)")
        if rep["missing"]:
            print(f"  WARNING: no column found for {', '.join(rep['missing']).upper()}"
                  f" — volumetric weight cannot be computed for these rows.")
        if rep["other_candidates"]:
            print("  NOTE: this sheet has more than one set of dimension columns.")
            print("        The FIRST set after ASIN was used (that is our own data in")
            print("        the 2026-08 sheet; the second set is Amazon's). Ignored:")
            for k, v in rep["other_candidates"].items():
                print(f"          {k.upper()}: {', '.join(v)}")
            print("        Override with e.g. --l=I --b=J --h=K --w=L if that is wrong.")
    elif cmd == "list-specs":
        rows = get_product_specs()
        if not rows:
            print("No product specs imported yet.  "
                  "python hygiene_db.py import-specs <file.xlsx>")
        for r in rows:
            dims = " x ".join(str(r[k] or "?") for k in
                              ("length_cm", "breadth_cm", "height_cm"))
            vol = volumetric_kg(r["length_cm"], r["breadth_cm"], r["height_cm"])
            print(f"  {r['asin']:<12} {dims:<22} cm   "
                  f"{str(r['weight_kg'] or '?'):>6} kg   "
                  f"vol {vol if vol is not None else '?'} kg")
        print(f"\n{len(rows)} ASINs with our own measurements.")
    elif cmd == "spec-changes":
        brand = args[1] if len(args) > 1 else None
        ch = spec_changes(brand=brand)
        if not ch:
            print("No Amazon-side dimension/weight changes found.")
            print("This needs at least TWO crawl runs covering the same ASIN — "
                  "run the crawler again and it will start reporting.")
        for asin, fields in sorted(ch.items()):
            print(f"  {asin}")
            for f, c in fields.items():
                print(f"     {f}: {c['from']!r} -> {c['to']!r}  (after {c['changed_after']})")
    elif cmd == "spec-template":
        # python hygiene_db.py spec-template Nexlev "Nexlev dimensions template.xlsx"
        # A one-block sheet for the team to fill in, pre-listed with every ASIN of
        # a brand and pre-filled with anything already imported. One block only,
        # so there is no ambiguity about which numbers are ours.
        import pandas as pd
        brand = args[1] if len(args) > 1 else "Nexlev"
        out = args[2] if len(args) > 2 else f"{brand} dimensions template.xlsx"
        have = {r["asin"]: r for r in get_product_specs()}
        prods = fetch_latest(brand=brand)
        recs = []
        for p in sorted(prods, key=lambda x: str(x.get("SKU") or "")):
            a = str(p.get("ASIN") or "").strip().upper()
            if not a:
                continue
            h = have.get(a, {})
            recs.append({
                "ASIN": a,
                "SKU": p.get("SKU", ""),
                "Title": str(p.get("Title", ""))[:80],
                "Length (cm)": h.get("length_cm", ""),
                "Breadth (cm)": h.get("breadth_cm", ""),
                "Height (cm)": h.get("height_cm", ""),
                "Packed Weight (kg)": h.get("weight_kg", ""),
                "Remarks": h.get("notes", ""),
            })
        pd.DataFrame(recs).to_excel(out, index=False, sheet_name="Our Specs")
        filled = sum(1 for r in recs if r["Length (cm)"] != "")
        print(f"Wrote {out}")
        print(f"  {len(recs)} {brand} ASINs · {filled} already filled in · "
              f"{len(recs) - filled} to measure")
        print("  Fill ONLY our own measured values — packed/shipping box, not the")
        print("  bare product. Amazon's side is read from the crawl automatically.")
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
    elif cmd == "monthly-password-reset":
        # python hygiene_db.py monthly-password-reset [--dry-run] [--force]
        #                                             [--period YYYY-MM] ["Name" ...]
        # Requires everyone to choose a NEW password of their own. No password
        # is set for them, no session is dropped, and no validation data is
        # touched. Normally driven by the scheduled task, not typed by hand.
        flags = [a for a in args[1:] if a.startswith("--")]
        rest = [a for a in args[1:] if not a.startswith("--")]
        period = None
        if "--period" in args:
            i = args.index("--period")
            if i + 1 < len(args):
                period = args[i + 1]
                rest = [a for a in rest if a != period]
        res = run_monthly_password_reset(
            period=period, only=rest or None,
            force="--force" in flags, dry_run="--dry-run" in flags)
        if res["already_ran"]:
            print(f"Already ran for {res['period']} at {res['ran_at']} ({res['detail']}).")
            print("Nothing changed. Use --force only if you truly want to re-arm.")
        else:
            what = "WOULD require" if res["dry_run"] else "Now requires"
            print(f"{what} a new password from {len(res['armed'])} user(s) "
                  f"for {res['period']} in {backend_name()}:")
            for n in res["armed"]:
                print(f"  {n}")
            if res["already_pending"]:
                print(f"Still owed from before ({len(res['already_pending'])}), left as-is:")
                for n in res["already_pending"]:
                    print(f"  {n}")
            for n in res["missing"]:
                print(f"  no such user: {n}")
            if res["dry_run"]:
                print("Dry run — nothing was written.")
    elif cmd == "password-reset-status":
        # python hygiene_db.py password-reset-status [YYYY-MM]
        st = password_reset_status(args[1] if len(args) > 1 else None)
        print(f"{st['period']} in {backend_name()}: "
              f"{'ran ' + str(st['ran_at']) if st['ran'] else 'NOT RUN YET'}")
        if st["detail"]:
            print(f"  {st['detail']}")
        print(f"  still to choose a new password ({len(st['pending'])}/{st['total']}):")
        for n in st["pending"] or ["  (nobody)"]:
            print(f"    {n}")
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
