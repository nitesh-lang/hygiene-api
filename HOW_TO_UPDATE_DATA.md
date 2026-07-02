# How to Update the Hygiene Validator Data

When you have a **new crawler file** and/or an **updated input sheet**, you push them
into the shared database. The whole team's validator then auto-loads the new data on
refresh — **nobody uploads anything in the browser.**

> ⚠️ Uploading a file in the website only changes **your own** screen. To update it
> for the whole team you MUST use the steps below.

---

## One-time setup (per computer)

1. Get the database URL: Render dashboard → your **Postgres database** → copy the
   **"External Database URL"** (it starts with `postgres://...`).

2. Make sure the Python packages are installed (only needed once):
   ```powershell
   cd "C:\Hazique Backup\Hygine"
   pip install -r requirements.txt
   ```

---

## Each time you have new data

Open **PowerShell** and run these in order:

```powershell
# 1. Go to the backend folder (where hygiene_db.py lives)
cd "C:\Hazique Backup\Hygine"

# 2. Point at the SHARED database — paste your real Render URL between the quotes
$env:DATABASE_URL = "postgres://...your-render-url..."

# 3. Push the new crawler data (use your latest CSV file)
python hygiene_db.py import-csv "output\amazon_products_full.csv"

# 4. Push the updated input sheet  (the REFERENCE sheet — 59 columns, "Format" tab)
#    The last word ("Format") is the TAB name inside the Excel file that has the data.
python hygiene_db.py import-input "output\All Brands Hygiene Input file - 2026.xlsx" Format
```

> 📁 **The input sheet MUST live in the `output\` folder with this exact name:**
> `output\All Brands Hygiene Input file - 2026.xlsx`
> When you get a new/updated sheet, **save (or copy) it there over the old one** before
> running step 4 — then the command above works unchanged. If the file is somewhere else
> (e.g. `C:\Hazique Backup\...` or has a `(1)` in the name), step 4 fails with
> `FileNotFoundError: ... No such file or directory`. Also **close it in Excel first** —
> a leftover `~$...` lock file means it's still open and can block the import.

> ⚠️ **Use the right input file!** The reference sheet is
> **`All Brands Hygiene Input file - 2026.xlsx`** (tab `Format`, ~59 columns:
> SKU, ASIN, Model Name, nodding, warranty, fee category…).
> Do **NOT** use `Crawling input file.xlsx` — that one only has 4 columns
> (ASIN/status/URL/Brand) and is just the crawler's ASIN list. Importing it would
> make every validation show blank reference values.

### ⚠️ Step 2 is the important one
If you skip `DATABASE_URL`, the data is written to a **local file on your PC** and the
team will see nothing. With it set, it writes to the **shared** database.

---

## Check it worked

```powershell
python hygiene_db.py stats        # shows total rows / distinct ASINs in the DB
python hygiene_db.py show-input   # shows the current input sheet name + row count
```

Then tell the team to **refresh the validator** — it auto-loads the new crawl + sheet.

---

## Notes

- **Use your real filenames** in steps 3 and 4. Swap in whatever the latest files are:
  - Crawl CSV: `output\amazon_products_full.csv`
  - Reference sheet: `output\All Brands Hygiene Input file - 2026.xlsx` (tab `Format`)
    — NOT `Crawling input file.xlsx` (that's just the crawler's ASIN list)
- **Step 4's last word** = the Excel **tab name** with the data. If your data is on a
  differently-named tab, use that name. If unsure, leave it off to use the first tab.
- **History is kept.** Each `import-csv` adds a new crawl run and updates the
  "latest per ASIN" that the app reads; old runs stay in the DB as history.
  `import-input` replaces the current reference sheet.
- **Errors about a missing module** (`pandas`, `psycopg2`, `openpyxl`)? Run the
  `pip install -r requirements.txt` from the one-time setup.

---

## Quick reference — what each command does

| Command | What it does |
|---|---|
| `import-csv <file.csv>` | Loads crawler product data into the DB |
| `import-input <file.xlsx> [tab]` | Loads the validator reference/input sheet |
| `stats` | Shows how many products/ASINs are in the DB |
| `show-input` | Shows the current input sheet info |
| `done-list` | Lists ASINs already marked done by the team |
| `progress` | Shows done vs remaining counts |
