#!/usr/bin/env python3
"""Tests for the product-specs importer.

    python tests_specs.py

The importer is the piece the team touches most: every time Sagar re-issues the
dimensions sheet, this code has to read it without anyone editing Python. So
these tests are mostly about SHAPE tolerance — different column names, different
units, junk rows above the header — plus the one case that would be silently
catastrophic: reading Amazon's block as if it were ours, which inverts every
discrepancy flag on the page.

Writes only to temporary files and never touches the real database.
"""
import os
import sys
import tempfile

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hygiene_db as db

failed = 0
passed = 0


def ok(cond, label, detail=""):
    global failed, passed
    if cond:
        passed += 1
        print(f"  PASS  {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}" + (f"\n        {detail}" if detail else ""))


def section(t):
    print(f"\n{t}")


def sheet(rows, name="Sheet1"):
    """Write rows (list of lists, first row is the header) to a temp xlsx."""
    fd, path = tempfile.mkstemp(suffix=".xlsx")
    os.close(fd)
    pd.DataFrame(rows).to_excel(path, index=False, header=False, sheet_name=name)
    return path


tmp = []


def parse(rows, **kw):
    p = sheet(rows)
    tmp.append(p)
    return db.parse_spec_sheet(p, **kw)


# ---------------------------------------------------------------------------
section("1. the sheet we actually have today")

# The real 2026-08 layout: our numbers first, then a column literally headed
# "Actual", then Amazon's numbers. Ours must win.
rows, rep = parse([
    ["ASIN", "Product Code", "L(cm)", "B(Cm)", "H(Cm)", "Weight Kg", "Remarks",
     "Actual", "L(cm)", "B(Cm)", "H(Cm)", "Weight Kg", "Sagar's remarks"],
    ["B0CDPP45BM", "SC-01", 30, 19.5, 21, 2.3, "Correct",
     "", 30, 21, 19.5, 1.65, "No changes"],
])
r = rows[0]
ok(len(rows) == 1, "one row parsed")
ok((r["length_cm"], r["breadth_cm"], r["height_cm"]) == (30.0, 19.5, 21.0),
   "took OUR block (30 x 19.5 x 21), not Amazon's (30 x 21 x 19.5)",
   f'got {r["length_cm"]} x {r["breadth_cm"]} x {r["height_cm"]}')
ok(r["weight_kg"] == 2.3, "took our 2.3 kg, not Amazon's 1.65 kg")
ok(rep["other_candidates"], "the duplicate second block is REPORTED, not hidden",
   "the operator must be able to see that a choice was made")

# ...and the choice is overridable, because the label 'Actual' is genuinely
# ambiguous about which side it describes.
rows2, _ = parse([
    ["ASIN", "Product Code", "L(cm)", "B(Cm)", "H(Cm)", "Weight Kg", "Remarks",
     "Actual", "L(cm)", "B(Cm)", "H(Cm)", "Weight Kg", "Sagar's remarks"],
    ["B0CDPP45BM", "SC-01", 30, 19.5, 21, 2.3, "Correct",
     "", 30, 21, 19.5, 1.65, "No changes"],
], overrides={"l": 8, "b": 9, "h": 10, "w": 11})
ok(rows2[0]["weight_kg"] == 1.65, "--l/--b/--h/--w override picks the other block")

# ---------------------------------------------------------------------------
section("2. a differently shaped sheet needs no code change")

rows, rep = parse([
    ["Nexlev master packing data", None, None, None, None, None],   # junk title row
    ["Asin", "SKU Code", "Length (mm)", "Width (mm)", "Height (mm)", "Item Weight (g)"],
    ["B0CDPP45BM", "SC-01", 300, 195, 210, 2300],
    ["B0D67727VG", "NX-77", "100 mm", 100, 50, "200 g"],
])
ok(rep["header_row"] == 2, "header found below a junk title row")
ok(rows[0]["length_cm"] == 30.0 and rows[0]["weight_kg"] == 2.3,
   "mm and g converted to cm and kg", f"got {rows[0]}")
ok(rows[1]["length_cm"] == 10.0 and rows[1]["weight_kg"] == 0.2,
   "units written in the CELL are honoured too")
ok(rep["columns_used"]["b"] == "Width (mm)", "'Width' is accepted for breadth")

# The template we hand out, round-tripped.
rows, rep = parse([
    ["ASIN", "SKU", "Title", "Length (cm)", "Breadth (cm)", "Height (cm)",
     "Packed Weight (kg)", "Remarks"],
    ["B0CDPP45BM", "SC-01", "Steam Cleaner", 30, 19.5, 21, 2.3, ""],
])
ok(rows[0]["length_cm"] == 30.0 and rows[0]["weight_kg"] == 2.3,
   "our own template imports cleanly")
ok(not rep["other_candidates"], "the template has no ambiguous second block")

# ---------------------------------------------------------------------------
section("3. incomplete and messy input")

rows, rep = parse([
    ["ASIN", "L(cm)", "B(Cm)", "H(Cm)", "Weight Kg"],
    ["B0AAAAAAAA", 30, 20, 10, 1.5],
    ["B0BBBBBBBB", None, None, None, None],       # not measured yet
    ["B0CCCCCCCC", 30, 20, None, None],           # half measured
    [None, 1, 2, 3, 4],                           # no ASIN
    ["", 1, 2, 3, 4],                             # blank ASIN
])
ok(len(rows) == 2, "rows with no ASIN and fully blank rows are dropped",
   f"got {len(rows)}")
ok(rep["blank_rows_skipped"] == 1, "the unmeasured ASIN is counted, not silently lost")
half = next(r for r in rows if r["asin"] == "B0CCCCCCCC")
ok(half["length_cm"] == 30.0 and half["height_cm"] == "" and half["weight_kg"] == "",
   "a half-measured row is kept with the known values")

rows, _ = parse([
    ["ASIN", "L(cm)", "B(Cm)", "H(Cm)", "Weight Kg"],
    ["b0aaaaaaaa", "30 cm", " 20 ", "10", " 1.5 kg "],
])
ok(rows[0]["asin"] == "B0AAAAAAAA", "ASIN is upper-cased")
ok(rows[0]["length_cm"] == 30.0 and rows[0]["weight_kg"] == 1.5,
   "stray units and whitespace in cells are tolerated")

try:
    parse([["Product", "L(cm)"], ["x", 1]])
    ok(False, "a sheet with no ASIN column is rejected")
except ValueError as e:
    ok("ASIN" in str(e), "a sheet with no ASIN column is rejected with a clear error",
       str(e))

# ---------------------------------------------------------------------------
section("4. volumetric maths (must agree with the frontend)")

ok(db.VOLUMETRIC_DIVISOR == 5000, "divisor is 5000, same as App.jsx's VOL_DIVISOR")
ok(db.volumetric_kg(30, 21, 19.5) == 2.457, "30 x 21 x 19.5 -> 2.457 kg")
ok(db.volumetric_kg("30", "21", "19.5") == 2.457, "strings from the TEXT columns work")
ok(db.volumetric_kg("", "21", "19.5") is None, "a missing side -> None")
ok(db.volumetric_kg(30, 0, 19.5) is None, "a zero side -> None, not 0 kg")
ok(db.chargeable_kg(0.5, 2.457) == 2.457, "billed on volumetric when larger")
ok(db.chargeable_kg(6.3, 2.0) == 6.3, "billed on actual when larger")
ok(db.chargeable_kg(None, None) is None, "nothing known -> None")

for p in tmp:
    try:
        os.remove(p)
    except OSError:
        pass

print(f"\n{f'{failed} FAILED, ' if failed else ''}{passed} assertions passed")
sys.exit(1 if failed else 0)
