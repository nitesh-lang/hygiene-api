#!/usr/bin/env python3
"""Tests for the 2nd-of-the-month password rotation.

    python tests_password_rotation.py

Runs against a THROWAWAY SQLite file, never the real database — the env vars
below are set before hygiene_db is imported, which is when it picks its backend.

The things worth proving here are the ones that would be expensive to discover
in production:
  - the job must not change anyone's password (they still sign in with the one
    they know, then choose their own)
  - it must not touch validation data
  - it must not fire twice for the same month, or a catch-up run would re-arm
    people who already chose a new password
  - a user must not be able to cycle back to a password they just left
"""
import os
import sys
import tempfile

# Isolate BEFORE the import: hygiene_db reads these at module load.
_fd, _tmpdb = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.remove(_tmpdb)                       # let sqlite create it fresh
os.environ.pop("DATABASE_URL", None)
os.environ["HYGIENE_SQLITE"] = _tmpdb

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hygiene_db as db                                        # noqa: E402

assert not db._IS_PG, "tests must not run against Postgres"
assert db.SQLITE_PATH == _tmpdb, "tests are not isolated from the real database"

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


def fresh_team():
    """Three users who have all chosen their own passwords already."""
    for u in db.list_users():
        db.delete_user(u["name"])
    conn = db._connect()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM password_history")
        cur.execute("DELETE FROM job_runs")
        conn.commit()
    finally:
        conn.close()
    for name, email, admin in (("Naresh More", "naresh@cambiumretail.com", False),
                               ("Nitesh Sharma", "nitesh@cambiumretail.com", False),
                               ("Hazique Khalique", "hazique@cambiumretail.com", True)):
        db.create_user(name, "startpass1", is_admin=admin, must_change=True, email=email)
        db.set_own_password(name, "startpass1", f"own-{name.split()[0].lower()}-1")


# ---------------------------------------------------------------------------
section("1. arming the month")
fresh_team()
ok(all(not u["must_change"] for u in db.list_users()),
   "everyone starts settled (nobody owes a password)")

res = db.run_monthly_password_reset(period="2026-08")
ok(len(res["armed"]) == 3, "all three are asked for a new password",
   f"armed={res['armed']}")
ok(all(u["must_change"] for u in db.list_users()),
   "must_change is raised on every account")
ok(res["already_ran"] is False, "first run for the month is not a no-op")

section("2. it does NOT change anyone's password")
u = db.verify_user("naresh@cambiumretail.com", "own-naresh-1")
ok(u is not None, "the password they already knew still signs them in")
ok(u and u["must_change"] is True,
   "...and the app is told they must choose a new one")
ok(db.verify_user("naresh@cambiumretail.com", "startpass1") is None,
   "the old shared starter password is still dead")

section("3. the admin is included, not exempt")
ok(db.must_change_password("Hazique Khalique"),
   "admins rotate too")

section("4. running twice in one month does nothing the second time")
db.set_own_password("Naresh More", "own-naresh-1", "own-naresh-2")
ok(not db.must_change_password("Naresh More"),
   "Naresh has chosen his new password")
again = db.run_monthly_password_reset(period="2026-08")
ok(again["already_ran"] is True, "a repeat run for the same month is refused")
ok(not db.must_change_password("Naresh More"),
   "...so his fresh password is NOT re-armed")
ok(again["armed"] == [], "nobody is armed by the repeat run")

section("5. next month arms him again")
nxt = db.run_monthly_password_reset(period="2026-09")
ok("Naresh More" in nxt["armed"], "Naresh is asked again in September")
ok("Nitesh Sharma" in nxt["already_pending"],
   "someone who never changed last month is reported as still owing, not re-armed",
   f"already_pending={nxt['already_pending']}")

section("6. --force overrides the once-a-month guard")
db.set_own_password("Naresh More", "own-naresh-2", "own-naresh-3")
forced = db.run_monthly_password_reset(period="2026-09", force=True)
ok("Naresh More" in forced["armed"], "force re-arms a settled user")

section("7. dry run writes nothing")
fresh_team()
dry = db.run_monthly_password_reset(period="2026-10", dry_run=True)
ok(len(dry["armed"]) == 3, "dry run reports who WOULD be armed")
ok(all(not u["must_change"] for u in db.list_users()),
   "dry run leaves must_change alone")
ok(db.get_job_run(db.MONTHLY_RESET_JOB, "2026-10") is None,
   "dry run does not record the month as done")

section("8. old passwords cannot be reused")
fresh_team()
db.run_monthly_password_reset(period="2026-11")
reused = None
try:
    db.set_own_password("Naresh More", "own-naresh-1", "own-naresh-1")
except ValueError as e:
    reused = str(e)
ok(reused is not None, "reusing the current password is rejected")

db.set_own_password("Naresh More", "own-naresh-1", "second-password")
db.run_monthly_password_reset(period="2026-12")
back = None
try:
    db.set_own_password("Naresh More", "second-password", "own-naresh-1")
except ValueError as e:
    back = str(e)
ok(back is not None and "used this password before" in back,
   "going back to last month's password is rejected", f"got: {back}")
db.set_own_password("Naresh More", "second-password", "third-password")
ok(db.verify_user("Naresh More", "third-password") is not None,
   "a genuinely new password is accepted")

section("9. history is capped, so old enough passwords come back around")
depth = db.PASSWORD_HISTORY_DEPTH
cur_pw = "third-password"
for i in range(depth + 2):
    nxt_pw = f"rotation-pass-{i}"
    db.set_own_password("Naresh More", cur_pw, nxt_pw)
    cur_pw = nxt_pw
hist = db._password_history("Naresh More")
ok(len(hist) <= depth, f"history keeps at most {depth} entries", f"len={len(hist)}")

section("10. validation data is never touched")
fresh_team()
db.mark_done("B0TESTASIN1", "Naresh More", check_results={"P1": "pass"},
             notes="before the rotation", brand="Nexlev")
before = db.get_validation("B0TESTASIN1")
db.run_monthly_password_reset(period="2027-01")
after = db.get_validation("B0TESTASIN1")
ok(after is not None, "the validation still exists after the rotation")
ok(before == after, "the validation row is byte-for-byte unchanged")
ok(len(db.list_all_validations()) == 1, "no validations were added or removed")

section("11. sessions survive (nobody is signed out mid-work)")
fresh_team()
tok = db.create_session("Naresh More")
db.run_monthly_password_reset(period="2027-02")
who = db.session_user(tok)
ok(who is not None, "an open session is still valid after the rotation")
ok(who and who["must_change"] is True,
   "...and /me now reports must_change, which is what raises the screen")

section("12. status reporting")
st = db.password_reset_status("2027-02")
ok(st["ran"] is True, "status knows the month has run")
ok(len(st["pending"]) == 3, "status lists who still owes a password",
   f"pending={st['pending']}")
ok(db.password_reset_status("2027-03")["ran"] is False,
   "a month that has not run yet reports as not run")

section("13. targeting one person")
fresh_team()
one = db.run_monthly_password_reset(period="2027-04", only=["naresh@cambiumretail.com"])
ok(one["armed"] == ["Naresh More"], "an email address targets the right user",
   f"armed={one['armed']}")
ok(not db.must_change_password("Nitesh Sharma"), "others are left alone")
miss = db.run_monthly_password_reset(period="2027-05", only=["Nobody At All"])
ok(miss["missing"] == ["Nobody At All"], "an unknown name is reported, not silently skipped")

section("14. signing in is by work email only")
fresh_team()
ok(db.verify_user("naresh@cambiumretail.com", "own-naresh-1", email_only=True) is not None,
   "the work email signs you in")
ok(db.verify_user("NARESH@Cambiumretail.COM", "own-naresh-1", email_only=True) is not None,
   "...case-insensitively")
ok(db.verify_user("Naresh More", "own-naresh-1", email_only=True) is None,
   "the display name does NOT sign you in")
ok(db.verify_user("Naresh more", "own-naresh-1", email_only=True) is None,
   "...in any casing")
ok(db.verify_user("naresh@cambiumretail.com", "wrong", email_only=True) is None,
   "a wrong password is still refused")

# The regression this change could easily have caused: the change-password
# route hands set_own_password the canonical DISPLAY NAME from the session, not
# an email. Locking the resolver to emails outright would have made every
# password change fail with "current password is incorrect" — including the
# forced one on the 2nd, which would wedge the whole team out of the app.
db.run_monthly_password_reset(period="2027-06")
db.set_own_password("Naresh More", "own-naresh-1", "brand-new-password")
ok(db.verify_user("naresh@cambiumretail.com", "brand-new-password",
                  email_only=True) is not None,
   "changing a password still works when called with the display name")
ok(not db.must_change_password("Naresh More"),
   "...and it clears the monthly flag")


# ---------------------------------------------------------------------------
print(f"\n{passed} passed, {failed} failed")
try:
    os.remove(_tmpdb)
except OSError:
    pass
sys.exit(1 if failed else 0)
