# Monthly password reset — 2nd of every month

Everyone on the team picks a **new password of their own** on the 2nd of each
month. Nobody is given a password, and nobody — admin included — ever sees
anyone else's.

## Set it up (once)

In PowerShell, from `C:\Hazique Backup\Hygine`:

```powershell
.\setup_monthly_reset_task.ps1
```

That's it. Windows now runs the job on the 2nd of every month at 09:00, forever.

```powershell
.\setup_monthly_reset_task.ps1 -Time 10:30   # a different time
.\setup_monthly_reset_task.ps1 -Show         # is it scheduled? when does it next run?
.\setup_monthly_reset_task.ps1 -Remove       # stop it running
```

The task runs as you, while you are logged on, so Windows never stores your
Windows password. If the PC is off on the 2nd it catches up the next time you
log on, and still counts as that month's reset.

## What happens to the team

1. The job raises a "must choose a new password" flag on every account.
2. Next time someone opens the validator, they sign in **with the password they
   already have** and are taken straight to a *Choose your password* screen.
3. They type their current password once, then a new one (8+ characters, and not
   one of their last 6), and carry on working.

Nobody is signed out, and nothing they have already validated is affected.

## What it does NOT do

- It does **not** change or reset anyone's password. It only asks them to.
- It does **not** touch products, validations, validation history, the input
  sheet or product specs. It writes to `users`, `password_history` and
  `job_runs` and nothing else.
- It does **not** drop live sessions, so unsynced work in someone's browser is
  never at risk.
- It does **not** email anyone. Tell the team in the group.

## Running it by hand

```powershell
.\monthly_password_reset.ps1 -DryRun   # show who WOULD be asked, change nothing
.\monthly_password_reset.ps1           # do it for real
.\monthly_password_reset.ps1 -Status   # who still owes a new password this month
.\monthly_password_reset.ps1 -Force    # re-arm even if this month already ran
```

Running it twice in a month is safe: it records the month it ran for and does
nothing the second time. Without that, a catch-up run would re-ask people who
had already chosen their new password.

Every run appends to `output\monthly_password_reset.log`.

## Behind it

The same thing is available from the database CLI, which is what the PowerShell
wrapper calls:

```
python hygiene_db.py monthly-password-reset [--dry-run] [--force] [--period YYYY-MM] ["Name" ...]
python hygiene_db.py password-reset-status [YYYY-MM]
```

Both need `DATABASE_URL` pointing at Neon. The wrapper reads it from
`deploy.secrets.ps1` and **refuses to run** if it is missing or is not a
Postgres URL — otherwise it would quietly rotate a local SQLite file and report
success while the live team stayed untouched.

Tests: `python tests_password_rotation.py` (runs on a throwaway SQLite file,
never the real database).
