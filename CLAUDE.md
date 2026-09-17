# Hygiene validator: data commitment

Every answer (Yes / No), comment, tick, stock status, note and Done mark a validator
saves must never be lost. This comes before any feature. The user asked for it
as a standing rule on 2026-09-17 after stale "Not Sure" values overwrote saved answers.

## Rules the code keeps (do not weaken them)

- **Only Yes / No are answers.** "Not Sure", REVIEW, PASS and FAIL mean unanswered.
  A non-answer never replaces a saved Yes/No (`REAL_ANSWERS`, `keep_real_answers`
  in `hygiene_db.py`; `mergeAnswers` / `missingWork` in `hygiene-app/src/App.jsx`).
- **The server's saved Yes/No shows on every PC.** A browser's own copy never hides it.
- **Nothing is dropped by omission.** A save that doesn't mention a check, comment,
  tick or note keeps what's stored. Only an explicit empty value clears one.
- **`work_log` is append-only.** Every change to saved work is written there, with the
  old and new value, in the same transaction as the save. No code may DELETE or
  UPDATE `work_log`. Resets and `clear-validations` copy every row they remove into
  it first, so any of them can be undone.
- **`clear-validations` refuses** unless given the exact confirmation "DELETE ALL WORK".

## Before any change that touches saving

1. Run `python backup_neon.py` (read-only snapshot into `auto_backups\`).
2. Run the tests: `python tests_comments.py` (plus `tests_password_rotation.py`, `tests_specs.py`).
   Add a test for the new rule.
3. Ask the user before pushing anything live. Pushing to `main` deploys on Render.
4. After deploy, compare the live data with the snapshot and show that no Yes/No was lost.

## Backups

- Task Scheduler task **"Hygiene Neon backup"** runs `backup_neon.py` daily at 1:00 PM,
  7:30 PM and at login, into `auto_backups\<date>_<time>\`. Never delete old snapshots.
- Before any reset: re-read the LIVE rows seconds before, show the list, and select
  validations by ASIN, not by brand (rows can have an empty brand).
