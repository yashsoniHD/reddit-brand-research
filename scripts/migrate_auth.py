"""Add the `user_id` column sign-in needs, and claim the reports that predate it.

Two jobs, deliberately in one script because they are the same one-off:

    # Add the column. Safe to run repeatedly; does nothing the second time.
    python scripts/migrate_auth.py

    # Then, once, hand every unowned report to whoever should own it.
    python scripts/migrate_auth.py --claim 00000000-0000-0000-0000-000000000000

The id to claim with is a Supabase user's `sub` (its uuid in `auth.users`), which
is the same value api/index.py reads off a verified access token and stores on
every new run. The quickest way to find your own: sign in to the app, then read
`sub` out of the session in the browser console, or look the row up in the
Supabase dashboard under Authentication.

Nothing here is destructive. The ALTER is `if not exists`, and the claim only
ever touches rows whose `user_id` is still null, so a second claim with a
different id cannot take reports away from the first one. Neither statement can
run without POSTGRES_URL, which is read through db.py's own `_dsn` handling, so
the `supa=` parameter Vercel writes into that URL is stripped here exactly as it
is everywhere else and `sslmode=require` is applied.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg  # noqa: E402

import db  # noqa: E402

# Nullable on purpose: the reports that already exist have no owner until the
# claim below runs, and a run whose token somehow carried no identity is still
# worth storing. Kept as `text` rather than `uuid` so a Supabase `sub` that is
# ever anything but a uuid still fits, and so the column needs no cast at insert.
ADD_COLUMN_SQL = "alter table public.reports add column if not exists user_id text"

# `where user_id is null` is what makes this safe to run twice: the second run
# matches nothing rather than reassigning reports the first run gave away.
CLAIM_SQL = "update public.reports set user_id = %s where user_id is null"

# Supabase creates a table with default grants to the browser-facing roles: anon
# and authenticated may SELECT/INSERT/UPDATE/DELETE (and TRUNCATE) every row, so
# the only thing standing between the public anon key shipped to the browser and
# the whole table is a zero-policy RLS. Close the table at the privilege layer
# too by revoking those grants. The backend connects as the table owner
# (postgres), which owns the table and keeps full access regardless, so this
# takes nothing away from the app. RLS enablement itself is deliberately left
# untouched. Revoking is idempotent: a grant already gone is a no-op.
REVOKE_TABLES = ("public.reports", "public.drafts")
REVOKE_SQL = "revoke all privileges on table {} from anon, authenticated"


def add_column(cursor) -> None:
    cursor.execute(ADD_COLUMN_SQL)


def claim_unowned(cursor, user_id: str) -> int:
    """Assign every report with no owner to `user_id`. Returns the row count."""
    cursor.execute(CLAIM_SQL, (user_id,))
    return cursor.rowcount


def revoke_browser_role_grants(cursor) -> list:
    """Revoke the anon/authenticated table grants on the app's tables.

    Idempotent and safe to re-run: revoking a grant that is already gone is a
    no-op, and a table that is not present is skipped with a log line rather than
    aborting the migration (a fresh database may not have `drafts` yet). Returns
    the tables actually touched. The table name is a fixed constant, never input,
    so formatting it into the statement is safe; an identifier cannot be a bound
    parameter, which is why `to_regclass` takes the name as a parameter and the
    REVOKE takes it by format.
    """
    revoked = []
    for table in REVOKE_TABLES:
        cursor.execute("select to_regclass(%s)", (table,))
        row = cursor.fetchone()
        if not row or row[0] is None:
            print("Skipped {}: table not present.".format(table))
            continue
        cursor.execute(REVOKE_SQL.format(table))
        print("Revoked anon/authenticated grants on {}.".format(table))
        revoked.append(table)
    return revoked


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Add reports.user_id and optionally claim the reports that predate sign-in.",
    )
    parser.add_argument(
        "--claim",
        metavar="USER_ID",
        default=None,
        help="assign every report with no user_id to this Supabase user id",
    )
    args = parser.parse_args()

    claim = (args.claim or "").strip()
    if args.claim is not None and not claim:
        print("--claim needs a Supabase user id.", file=sys.stderr)
        return 2

    if not db.is_configured():
        print(
            "POSTGRES_URL is not set. Run `vercel env pull` first, or export it for this shell.",
            file=sys.stderr,
        )
        return 1

    try:
        with psycopg.connect(db._dsn(), connect_timeout=db.CONNECT_TIMEOUT_SECONDS) as conn:
            with conn.cursor() as cur:
                add_column(cur)
                print("reports.user_id is present.")
                revoke_browser_role_grants(cur)
                if claim:
                    claimed = claim_unowned(cur, claim)
                    print("Claimed {} report(s) for {}.".format(claimed, claim))
    except db.StorageUnavailable as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - psycopg raises several unrelated types
        # Deliberately the exception class and not str(exc): a psycopg connection
        # error stringifies the host, the user and sometimes the password, and
        # this prints to a terminal that may well be in a screen share.
        print(
            "The migration failed ({}). Check POSTGRES_URL and try again.".format(
                type(exc).__name__
            ),
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
