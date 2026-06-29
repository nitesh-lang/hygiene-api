# Hygiene Crawler — Database Setup

The crawler now saves every run into a database (in addition to CSV/XLSX).
Two backends, picked automatically by the `DATABASE_URL` environment variable.

## 1. Install the driver

```powershell
pip install psycopg2-binary    # only needed if you use Postgres/Neon
```
(SQLite needs nothing — it's built into Python.)

## 2a. Local mode (zero setup — recommended to start)

Do nothing. With no `DATABASE_URL` set, the crawler writes to a local file:

```
C:\Hazique Backup\Hygine\output\hygiene.db
```

## 2b. Production mode (Postgres / Neon — shared with the app)

Set the connection string before running the crawler:

```powershell
$env:DATABASE_URL = "postgres://USER:PASSWORD@HOST/DBNAME?sslmode=require"
python amazon_in_crawler_playwright.py
```

To make it permanent (so you don't set it each time):

```powershell
[Environment]::SetEnvironmentVariable("DATABASE_URL", "postgres://...", "User")
```

## 3. Tables created

| Table | What it holds |
|-------|---------------|
| `products` | Every crawled row from every run (full history, with `crawl_run` + `crawled_at`) |
| `products_latest` | One row per ASIN — the newest crawl. **The validator should read this.** |

All 48 crawler columns are stored as snake_case fields
(e.g. `"What is in the box?"` → `what_is_in_the_box`, `"A+ Content"` → `a_content`).

## 4. Push your EXISTING output into the DB (no re-crawl)

```powershell
python hygiene_db.py import-csv output\amazon_products_full.csv
python hygiene_db.py stats
```

## 5. Reading from Python (validator backend)

```python
from hygiene_db import fetch_latest, fetch_asin_history

rows = fetch_latest()                  # newest row per ASIN, all brands
rows = fetch_latest(brand="Nexlev")    # partial match — works on the byline too
history = fetch_asin_history("B0G3Q59X8C")   # all crawls of one ASIN, for diffing
```

Each row is a dict with the human column names (`row["Image Count"]`,
`row["What is in the Box Image"]`, etc.) plus `_crawl_run` and `_crawled_at`.

## 6. CLI quick reference

```powershell
python hygiene_db.py init                 # create tables
python hygiene_db.py import-csv <path>    # load a CSV
python hygiene_db.py show <ASIN>          # full history of one ASIN (JSON)
python hygiene_db.py run <crawl_run>      # count rows in a run
python hygiene_db.py stats                # backend + row/asin/run counts
```

## Notes
- The crawler still writes CSV + XLSX as before — the DB is additive, nothing breaks if it fails.
- Re-running a crawl with the same `crawl_run` label replaces those rows (idempotent).
- `products_latest` is what the app reads so it always sees current data without history noise.
