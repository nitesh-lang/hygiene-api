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

print("autosave: comments without marking done")
db.save_comments("B0TEST0003", comments={"title": "typed but not finished"},
                 validated_by="Naresh More", brand="Audio Array")
check("the text is stored", comments_of("B0TEST0003"),
      {"title": "typed but not finished"})
check("the ASIN is NOT counted as validated", db.is_done("B0TEST0003"), False)
check("it stays off the done list", "B0TEST0003" in db.list_done_asins(), False)
row = [v for v in db.list_all_validations() if v["asin"] == "B0TEST0003"][0]
check("but it IS returned so the text can come back", row["is_done"], "no")

db.save_comments("B0TEST0003", comments={"colour": "wrong shade"})
check("a second autosave merges", comments_of("B0TEST0003"),
      {"title": "typed but not finished", "colour": "wrong shade"})
db.mark_done("B0TEST0003", "Naresh More", brand="Audio Array",
             check_results={"title": "No"})
check("finishing it later keeps the typed text", comments_of("B0TEST0003"),
      {"title": "typed but not finished", "colour": "wrong shade"})
check("and now it is done", db.is_done("B0TEST0003"), True)

print("autosave never disturbs a finished record")
before = [v for v in db.list_all_validations() if v["asin"] == "B0TEST0001"][0]
db.save_comments("B0TEST0001", comments={"colour": "added later"})
after = [v for v in db.list_all_validations() if v["asin"] == "B0TEST0001"][0]
check("validated_by is untouched", after["validated_by"], before["validated_by"])
check("validated_at is untouched", after["validated_at"], before["validated_at"])
check("the answers are untouched", decisions_of("B0TEST0001"),
      {k: v for k, v in before["check_results"].items() if k != "comments"})

print("notes are not blanked by omission")
db.save_comments("B0TEST0004", comments={"title": "x"}, notes="check packaging",
                 validated_by="Naresh More", brand="Audio Array")
db.mark_done("B0TEST0004", "Nitesh Sharma", brand="Audio Array",
             check_results={"title": "No"})
notes_of = lambda a: [v for v in db.list_all_validations() if v["asin"] == a][0]["notes"]
check("marking done keeps the note", notes_of("B0TEST0004"), "check packaging")
db.mark_done("B0TEST0004", "Naresh More", brand="Audio Array",
             check_results={"title": "No"}, notes="")
check("an explicit empty string still clears it", notes_of("B0TEST0004"), "")
db.mark_done("B0TEST0004", "Naresh More", brand="Audio Array",
             check_results={"title": "No"}, notes="rewritten")
check("and a real note replaces it", notes_of("B0TEST0004"), "rewritten")

print("corrections survive the browser too")
db.save_corrections({"global": {"packer": "Cambium Retail LLP"},
                     "byAsin": {"B0TEST0001": {"colour": "Matte Black"}}},
                    updated_by="Naresh More")
got = db.list_corrections()
check("a global correction round-trips", got["global"],
      {"packer": "Cambium Retail LLP"})
check("an ASIN correction round-trips", got["byAsin"],
      {"B0TEST0001": {"colour": "Matte Black"}})
db.save_corrections({"byAsin": {"B0TEST0002": {"material": "ABS"}}})
check("a partial save leaves the others alone",
      sorted(db.list_corrections()["byAsin"]), ["B0TEST0001", "B0TEST0002"])
check("and keeps the global one", db.list_corrections()["global"],
      {"packer": "Cambium Retail LLP"})
db.save_corrections({"global": {"packer": ""}})
check("an empty value deletes exactly that one",
      db.list_corrections()["global"], {})
check("without touching the ASIN-level ones",
      sorted(db.list_corrections()["byAsin"]), ["B0TEST0001", "B0TEST0002"])

print("answers and ticks live on the server too (2026-09-11)")
ticks_of = lambda a: db.verified_of(db.stored_record(a))
answers_of = lambda a: db.split_check_results(db.stored_record(a))[0]
check("the verified key is not mistaken for an answer",
      db.split_check_results({"title": "No", "verified": {"title": True}})[0], {"title": "No"})
db.save_comments("B0TEST0005", decisions={"title": "No", "colour": "Yes"},
                 verified={"title": True}, validated_by="Naresh More")
check("answers on an unfinished ASIN are stored", answers_of("B0TEST0005"),
      {"title": "No", "colour": "Yes"})
check("ticks are stored", ticks_of("B0TEST0005"), {"title": True})
check("and the ASIN is still not done", db.is_done("B0TEST0005"), False)
db.save_comments("B0TEST0005", decisions={"colour": "No"}, verified={"colour": True})
check("a changed answer updates just that one", answers_of("B0TEST0005"),
      {"title": "No", "colour": "No"})
check("ticks merge", ticks_of("B0TEST0005"), {"title": True, "colour": True})
db.save_comments("B0TEST0005", comments={"title": "wrong"})
check("a comments-only save leaves answers alone", answers_of("B0TEST0005"),
      {"title": "No", "colour": "No"})
check("and leaves ticks alone", ticks_of("B0TEST0005"), {"title": True, "colour": True})
db.save_comments("B0TEST0005", decisions={"colour": ""}, verified={"colour": False})
check("clearing an answer removes it", answers_of("B0TEST0005"), {"title": "No"})
check("unticking removes the tick", ticks_of("B0TEST0005"), {"title": True})
check("the comment rode through untouched", comments_of("B0TEST0005"), {"title": "wrong"})

print("only_fill can add but never overrule")
db.save_comments("B0TEST0005", decisions={"title": "Yes", "material": "Yes"},
                 verified={"title": False, "material": True}, only_fill=True)
check("a stored answer is not changed", answers_of("B0TEST0005")["title"], "No")
check("a missing answer is added", answers_of("B0TEST0005")["material"], "Yes")
check("a stored tick is not removed", ticks_of("B0TEST0005"),
      {"title": True, "material": True})

print("Done keeps what the browser didn't send")
db.mark_done("B0TEST0005", "Nitesh Sharma", brand="Audio Array",
             check_results={"title": "Yes"})
check("Done's answer wins", answers_of("B0TEST0005")["title"], "Yes")
check("an autosaved answer Done didn't mention is kept",
      answers_of("B0TEST0005").get("material"), "Yes")
check("ticks survive a Done that sends none", ticks_of("B0TEST0005"),
      {"title": True, "material": True})
check("comments survive it too", comments_of("B0TEST0005"), {"title": "wrong"})
db.mark_done("B0TEST0005", "Nitesh Sharma", brand="Audio Array",
             check_results={"title": "Yes", "verified": {"material": False}})
check("Done can untick on purpose", ticks_of("B0TEST0005"), {"title": True})

print(f"\n{PASS} passed, {FAIL} failed")
raise SystemExit(1 if FAIL else 0)
