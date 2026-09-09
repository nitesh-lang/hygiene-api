#!/usr/bin/env python3
"""Comments must survive on the server.

They used to live only in the browser that typed them, so clearing a cache or
deleting a Chrome profile destroyed them (Audio Array lost 140 ASINs' worth on
2026-09-09). These tests pin the two rules that make that impossible now:
a save carries its comments to the DB, and no later save can silently drop
them.

Run against a throwaway SQLite file:  python tests_comments.py
"""
import json
import os
import tempfile

os.environ.pop("DATABASE_URL", None)
os.environ["HYGIENE_SQLITE"] = os.path.join(
    tempfile.mkdtemp(prefix="hygtest_"), "test.db")

import hygiene_db as db  # noqa: E402  (must follow the env setup)

PASS = FAIL = 0


def check(label, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
        print(f"  ok   {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label}\n       got  {got!r}\n       want {want!r}")


def comments_of(asin):
    return db.stored_comments(asin)


def decisions_of(asin):
    row = [v for v in db.list_all_validations() if v["asin"] == asin][0]
    return {k: v for k, v in row["check_results"].items() if k != "comments"}


print("split_check_results")
d, c = db.split_check_results({"title": "No", "comments": {"title": "wrong model"}})
check("decisions exclude the comments key", d, {"title": "No"})
check("comments come back", c, {"title": "wrong model"})
check("no comments key is fine", db.split_check_results({"title": "Yes"})[1], {})
check("a non-dict comments value is ignored",
      db.split_check_results({"title": "Yes", "comments": "oops"})[1], {})
check("None payload is fine", db.split_check_results(None), ({}, {}))

print("merge_comments")
check("new text is added", db.merge_comments({}, {"a": "x"}), {"a": "x"})
check("stored text survives an empty save", db.merge_comments({"a": "x"}, {}), {"a": "x"})
check("a key not in the save is kept",
      db.merge_comments({"a": "x", "b": "y"}, {"a": "z"}), {"a": "z", "b": "y"})
check("an explicit empty string deletes",
      db.merge_comments({"a": "x", "b": "y"}, {"a": ""}), {"b": "y"})
check("whitespace counts as empty",
      db.merge_comments({"a": "x"}, {"a": "   "}), {})

print("mark_done round trip")
db.mark_done("B0TEST0001", "Naresh More", brand="Audio Array",
             check_results={"title": "No", "colour": "Yes",
                            "comments": {"title": "model no missing"}})
check("comment is stored", comments_of("B0TEST0001"), {"title": "model no missing"})
check("decisions are stored", decisions_of("B0TEST0001"),
      {"title": "No", "colour": "Yes"})

print("a later save from a browser with no comments")
db.mark_done("B0TEST0001", "Nitesh Sharma", brand="Audio Array",
             check_results={"title": "No", "colour": "No"})
check("the comment is still there", comments_of("B0TEST0001"),
      {"title": "model no missing"})
check("the new decision won", decisions_of("B0TEST0001")["colour"], "No")

print("editing and clearing")
db.mark_done("B0TEST0001", "Naresh More", brand="Audio Array",
             check_results={"title": "No", "comments": {"title": "wrong model no",
                                                        "colour": "shade differs"}})
check("edited text replaces the old", comments_of("B0TEST0001"),
      {"title": "wrong model no", "colour": "shade differs"})
db.mark_done("B0TEST0001", "Naresh More", brand="Audio Array",
             check_results={"title": "No", "comments": {"colour": ""}})
check("clearing the box removes just that one", comments_of("B0TEST0001"),
      {"title": "wrong model no"})

print("history keeps the text too")
rows = db.validation_history("B0TEST0001") if hasattr(db, "validation_history") else None
if rows is None:
    import sqlite3
    conn = sqlite3.connect(os.environ["HYGIENE_SQLITE"])
    rows = [json.loads(r[0]) for r in conn.execute(
        "SELECT check_results FROM validations_history WHERE asin='B0TEST0001'")]
    conn.close()
    check("every save is logged", len(rows), 4)
    check("the last log entry carries the comment",
          rows[-1].get("comments"), {"title": "wrong model no"})

print("an ASIN with no comments never grows a comments key")
db.mark_done("B0TEST0002", "Naresh More", brand="Audio Array",
             check_results={"title": "Yes"})
row = [v for v in db.list_all_validations() if v["asin"] == "B0TEST0002"][0]
check("clean record stays clean", "comments" in row["check_results"], False)

print(f"\n{PASS} passed, {FAIL} failed")
raise SystemExit(1 if FAIL else 0)
