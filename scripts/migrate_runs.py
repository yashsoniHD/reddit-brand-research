"""Create the `runs` table background runs need, and, separately, schedule
the pg_cron job that drains its queue.

Depends on two things already being true:

    1. `public.reports` exists, because `runs.report_id` is a foreign key into
       it. Every migration that creates `reports` runs before this one.
    2. `scripts/migrate_auth.py` has been run at least once. It is where this
       project's convention of revoking the anon/authenticated table grants
       began; this script only continues that convention for the new table
       and does not re-check that the older tables were done.

Two independent jobs, kept in one script because they are two migrations for
the same feature and there is no third file worth the indirection:

    # Create/repair the table. Safe to run repeatedly; the second run does
    # nothing new.
    python scripts/migrate_runs.py

    # Also enable pg_cron/pg_net and (re)point the drain-runs cron job at a
    # worker. Gated behind its own flag because it needs two things the table
    # migration does not: a worker URL and a bearer secret, both deploy-time
    # facts rather than schema facts. This also installs the AFTER INSERT
    # trigger that wakes a worker the moment a run is queued, so a run no
    # longer waits up to a minute for the next cron tick; pass
    # --no-dispatch-on-insert to schedule the cron job without it.
    python scripts/migrate_runs.py --schedule-cron \\
        --worker-url https://reddit-content-ideas.vercel.app/api/run-worker \\
        --cron-secret "$CRON_SECRET"

    # Or lift the schedule without touching the table:
    python scripts/migrate_runs.py --unschedule-cron

    # Or drop just the immediate dispatch, keeping the minute cron as the only
    # trigger. The escape hatch if pg_net ever misbehaves on insert.
    python scripts/migrate_runs.py --drop-dispatch-on-insert

Why a runs table at all: a research run today executes inside the client's
streaming HTTP request and dies the moment the tab closes. This table is the
queue a worker drains instead. A row is `queued` until some worker claims it
(`db.claim_next_run`, using `select ... for update skip locked` so two workers
racing the same minute never claim the same row), `running` while the worker
walks the pipeline and touches `heartbeat_at` between stages
(`db.update_run_progress`), and terminal (`succeeded`/`failed`/`cancelled`)
once `db.set_run_result` runs. `cancelling` is a request the owner made
(`db.mark_run_cancelling`) that the worker itself notices and honours
(`db.is_cancelling`) at its own stage boundaries.

Nothing about pg_cron and pg_net is invented here: both are Supabase-managed
Postgres extensions, `cron.schedule` and `net.http_post` are their public
surface, and this script's job is only to point them at this project's
worker. Two things an operator must do that no amount of SQL here can:

    - Enabling pg_cron and pg_net can also be done from the Supabase
      Dashboard (Database -> Extensions); `create extension if not exists`
      below is the SQL-only equivalent Supabase documents for pg_net, and is
      expected (not independently confirmed against Supabase's docs at the
      time this was written) to behave the same way for pg_cron. If
      `--schedule-cron` fails enabling either extension, toggle it on in the
      Dashboard once and rerun; nothing else in this script depends on which
      route enabled it.
    - Supabase Vault (the `vault` schema) ships enabled on Supabase-hosted
      projects already; nothing here creates it. If `vault.create_secret`
      errors with an undefined-schema/function error, Vault itself needs
      enabling from the Dashboard first.
    - The 300-second `timeout_milliseconds` on the drain function's
      `net.http_post` call assumes Supabase's managed pg_net actually holds a
      request open that long; this is reported by the community, not spelled
      out in Supabase's own docs. Verify it once after this project's first
      deploy: schedule (or manually trigger) one `net.http_post` with
      `timeout_milliseconds := 300000` against a throwaway Vercel endpoint
      that sleeps for roughly 280 seconds and then returns 200, then check
      `net._http_response` for that 200 rather than a timeout. If it times
      out early, the worker route needs to respond faster than pg_net will
      wait, not the other way around.

The cron job's command text embeds the worker URL as a literal (it is a
public Vercel route, not a secret) but never the bearer token: the token goes
into Vault, and the SQL the job actually runs
(`public.trigger_drain_runs(worker_url)`) reads it back through a
SECURITY DEFINER helper at call time, so `cron.job`, which is queryable by
more than just this script, never holds it in the clear.

pg_net failures are silent from this script's side: `net.http_post` returns a
request id immediately and the actual delivery happens later, out of band, so
a dropped or timed-out trigger raises nothing here and nothing in
`cron.job_run_details` either (that table only records whether pg_cron *fired*
the job, not whether the HTTP call it made succeeded). This module adds no
separate observability for that; the existing safety net is
`db.claim_next_run`'s stale-heartbeat reaper, which does not care why a
`running` row's heartbeat went quiet. A trigger that never reached the worker
looks identical, from the queue's point of view, to a worker that reached it
and then died, and both self-heal the same way: some later minute's cron tick
reaches a worker that claims the row again.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg  # noqa: E402

import db  # noqa: E402

# --------------------------------------------------------------------------
# The table
# --------------------------------------------------------------------------

CREATE_TABLE_SQL = """
create table if not exists public.runs (
  id text primary key,
  user_id text not null,
  status text not null default 'queued',
  query text not null,
  profile text not null default '',
  inputs jsonb not null,
  progress jsonb not null default '[]'::jsonb,
  report_id text references public.reports(id),
  error text,
  attempts int not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  heartbeat_at timestamptz
)
"""

# `attempts` is the retry counter db.claim_next_run increments on every claim and
# caps the stale-`running` reaper against (see db.MAX_RUN_ATTEMPTS). A fresh
# database gets it straight from CREATE_TABLE_SQL above; a database created before
# the column existed needs it added in place. `add column if not exists` does both:
# it is a no-op on the freshly-created table and an in-place upgrade on the old
# one, so a single run of this script converges either database on the same shape.
ADD_ATTEMPTS_COLUMN_SQL = (
    "alter table public.runs add column if not exists attempts int not null default 0"
)

# Postgres has no `add constraint if not exists` for check constraints, so
# idempotency comes from dropping first: a constraint that was never there is
# a no-op to drop, and the add that follows always leaves exactly one copy of
# it, which also means changing RUN_STATUSES later and rerunning this script
# is enough to change the constraint in place.
RUN_STATUSES = ("queued", "running", "succeeded", "failed", "cancelling", "cancelled")
DROP_STATUS_CONSTRAINT_SQL = "alter table public.runs drop constraint if exists runs_status_check"
ADD_STATUS_CONSTRAINT_SQL = "alter table public.runs add constraint runs_status_check check (status in ({}))".format(
    ", ".join("'{}'".format(status) for status in RUN_STATUSES)
)

# `create index if not exists` is native idempotency; no drop-then-add dance
# needed the way the check constraint above needs one.
INDEX_USER_CREATED_SQL = (
    "create index if not exists runs_user_id_created_at_idx "
    "on public.runs (user_id, created_at desc)"
)
INDEX_STATUS_SQL = "create index if not exists runs_status_idx on public.runs (status)"

# RLS with no policies, same as reports/drafts: the backend connects as
# postgres and bypasses it, and a zero-policy table returns nothing to the
# anon key regardless. Enabling RLS twice is a no-op, so this needs no guard.
ENABLE_RLS_SQL = "alter table public.runs enable row level security"

# Supabase grants anon/authenticated the full CRUD set on a table it creates,
# by default, the same gap migrate_auth.py closed on reports and drafts. This
# closes it here too, at the privilege layer, so RLS is not the only thing
# standing between the anon key and the whole table. Guarded by `to_regclass`,
# exactly like migrate_auth.py, so this script can run before or after the
# table exists without erroring.
REVOKE_TABLES = ("public.runs",)
REVOKE_SQL = "revoke all privileges on table {} from anon, authenticated"


def create_table(cursor) -> None:
    cursor.execute(CREATE_TABLE_SQL)


def add_attempts_column(cursor) -> None:
    cursor.execute(ADD_ATTEMPTS_COLUMN_SQL)


def apply_status_constraint(cursor) -> None:
    cursor.execute(DROP_STATUS_CONSTRAINT_SQL)
    cursor.execute(ADD_STATUS_CONSTRAINT_SQL)


def create_indexes(cursor) -> None:
    cursor.execute(INDEX_USER_CREATED_SQL)
    cursor.execute(INDEX_STATUS_SQL)


def enable_rls(cursor) -> None:
    cursor.execute(ENABLE_RLS_SQL)


def revoke_browser_role_grants(cursor) -> list:
    """The same move scripts/migrate_auth.py makes for reports and drafts,
    repeated here for public.runs. Kept as its own copy rather than imported
    from migrate_auth.py: that script belongs to a different migration and a
    different point in time, and importing across one-off migration scripts
    would tie their futures together for no benefit either one needs.
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


# --------------------------------------------------------------------------
# pg_cron + pg_net: the queue's only trigger
# --------------------------------------------------------------------------
#
# The project is on Vercel Hobby, which has no Vercel Cron, so nothing on the
# Vercel side can wake the worker on its own. Supabase Postgres is where the
# queue already lives, so the trigger lives there too: pg_cron fires once a
# minute, and pg_net makes the one HTTP call that reaches the worker, entirely
# inside Postgres and with no external scheduler to operate or pay for.
CREATE_PG_CRON_SQL = "create extension if not exists pg_cron"
CREATE_PG_NET_SQL = "create extension if not exists pg_net"

CRON_JOB_NAME = "drain-runs"
CRON_SCHEDULE = "* * * * *"

# Where the bearer token lives. Not in the cron job definition itself:
# `cron.job` is an ordinary catalog table, readable by more than just this
# script, and a token sitting in it in the clear is a token every such reader
# now has. Vault keeps the ciphertext in `vault.secrets` and decrypts it only
# through `vault.decrypted_secrets`, which the SECURITY DEFINER helper below
# reads at call time so the scheduled SQL text never contains it.
VAULT_SECRET_NAME = "runs_cron_secret"
VAULT_SECRET_DESCRIPTION = "Bearer token the drain-runs cron job sends to the run-worker."

# SECURITY DEFINER is required here (an ordinary role cannot read
# `vault.decrypted_secrets`), so the function is kept to the one thing it
# does: read one named secret and POST it to the URL it is given. It takes no
# input that changes which secret it reads, so there is nothing for a caller
# to twist into reading a different one, and execute is revoked from
# everyone but the table owner immediately below, so it is not a public
# SECURITY DEFINER endpoint the way an unrevoked one in `public` would be.
#
# `timeout_milliseconds := 300000` is not pg_net's default (5000ms); it is set
# explicitly because the whole point of this feature is that the worker route
# runs the full pipeline synchronously, up to roughly 285 seconds, while
# pg_net holds the request open. The default 5s timeout would disconnect
# minutes before the worker finishes, which is the exact bug a queue is
# replacing, just moved one layer down. Supabase's managed pg_net is reported
# (community-confirmed, not found in Supabase's own docs at the time this was
# written) to honour a request-level timeout this long rather than silently
# capping it; verify this once after deploy rather than trusting either
# source blindly. See the note in the operator checklist at the bottom of
# this file's docstring for how.
CREATE_DRAIN_FUNCTION_SQL = """
create or replace function public.trigger_drain_runs(worker_url text)
returns void
language plpgsql
security definer
set search_path = public, vault, net
as $$
declare
  v_token text;
begin
  select decrypted_secret into v_token
  from vault.decrypted_secrets
  where name = '{secret_name}';

  perform net.http_post(
    url := worker_url,
    headers := jsonb_build_object(
      'Content-Type', 'application/json',
      'Authorization', 'Bearer ' || v_token
    ),
    body := '{{}}'::jsonb,
    timeout_milliseconds := 300000
  );
end;
$$;
""".format(secret_name=VAULT_SECRET_NAME)

# A function's default EXECUTE grant is to PUBLIC, which anon and
# authenticated inherit; left alone, this would be a public API endpoint that
# reads a secret and fires an HTTP call on anyone's request. Revoked so only
# the role that scheduled the cron job (postgres, since this script connects
# the same way db.py does) can call it.
REVOKE_DRAIN_FUNCTION_SQL = "revoke all on function public.trigger_drain_runs(text) from public"

# --------------------------------------------------------------------------
# Dispatch on insert: what makes a run start now instead of on the next minute
# --------------------------------------------------------------------------
#
# The cron job above is the queue's floor, not its latency budget. On its own it
# means a run waits for the next minute boundary before a worker is even woken:
# zero to sixty seconds of doing nothing, averaging thirty, on top of a pipeline
# that already takes minutes. That wait was measured as the single largest
# avoidable delay in the product.
#
# So the insert itself wakes a worker. An AFTER INSERT trigger on public.runs
# calls the same trigger_drain_runs the cron job calls, which means one code path
# for "wake a worker" and no second copy of the secret handling. pg_net does not
# make the HTTP call inline: it enqueues the request in its own table and its
# background worker sends it after this transaction commits, so a queued run is
# never visible to a worker before the row it is about to claim exists.
#
# The cron job stays exactly as it is. Every failure mode here (pg_net down, the
# request dropped, a worker that dies before claiming) degrades to precisely the
# old behaviour: the next minute tick picks the run up. Two workers arriving at
# once is already safe, because claim_next_run takes its row with FOR UPDATE SKIP
# LOCKED, so the loser claims a different run or none at all.
#
# The trigger fires per row and only for a row that is actually waiting to be
# run. `when (new.status = 'queued')` keeps a row inserted in any other state
# (a backfill, a test fixture, a future import) from firing an HTTP call nobody
# asked for.
DISPATCH_FUNCTION_NAME = "dispatch_queued_run"
DISPATCH_TRIGGER_NAME = "runs_dispatch_after_insert"

# The worker URL is embedded as a literal for the same reason the cron command
# embeds it: this is SQL Postgres stores and executes later, not a query this
# script runs now, so there is no bind parameter to use. `quote_literal` does
# the escaping, in Postgres, before the body is assembled.
CREATE_DISPATCH_FUNCTION_SQL = """
create or replace function public.{function}()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  perform public.trigger_drain_runs({url});
  return null;
end;
$$;
"""

REVOKE_DISPATCH_FUNCTION_SQL = (
    "revoke all on function public.{}() from public".format(DISPATCH_FUNCTION_NAME)
)

DROP_DISPATCH_TRIGGER_SQL = "drop trigger if exists {} on public.runs".format(
    DISPATCH_TRIGGER_NAME
)

CREATE_DISPATCH_TRIGGER_SQL = """
create trigger {trigger}
after insert on public.runs
for each row
when (new.status = 'queued')
execute function public.{function}()
""".format(trigger=DISPATCH_TRIGGER_NAME, function=DISPATCH_FUNCTION_NAME)


def enable_cron_extensions(cursor) -> None:
    cursor.execute(CREATE_PG_CRON_SQL)
    cursor.execute(CREATE_PG_NET_SQL)


def upsert_vault_secret(cursor, secret: str) -> None:
    """Store `secret` under VAULT_SECRET_NAME, creating it the first time and
    updating it in place on every rerun, so rotating CRON_SECRET is just
    running this again with the new value.
    """
    cursor.execute("select id from vault.secrets where name = %s", (VAULT_SECRET_NAME,))
    row = cursor.fetchone()
    if row is None:
        cursor.execute(
            "select vault.create_secret(%s, %s, %s)",
            (secret, VAULT_SECRET_NAME, VAULT_SECRET_DESCRIPTION),
        )
    else:
        cursor.execute("select vault.update_secret(%s, %s)", (row[0], secret))


def create_drain_function(cursor) -> None:
    cursor.execute(CREATE_DRAIN_FUNCTION_SQL)
    cursor.execute(REVOKE_DRAIN_FUNCTION_SQL)


def create_dispatch_trigger(cursor, worker_url: str) -> None:
    """(Re)create the AFTER INSERT trigger that wakes a worker immediately.

    Idempotent: the function is replaced in place and the trigger is dropped
    before it is created, so rerunning with a new URL moves the dispatch rather
    than leaving a second trigger pointing at the old deployment.
    """
    cursor.execute("select quote_literal(%s)", (worker_url,))
    quoted_url = cursor.fetchone()[0]
    cursor.execute(
        CREATE_DISPATCH_FUNCTION_SQL.format(
            function=DISPATCH_FUNCTION_NAME, url=quoted_url
        )
    )
    cursor.execute(REVOKE_DISPATCH_FUNCTION_SQL)
    cursor.execute(DROP_DISPATCH_TRIGGER_SQL)
    cursor.execute(CREATE_DISPATCH_TRIGGER_SQL)


def drop_dispatch_trigger(cursor) -> None:
    """Remove the insert dispatch, leaving the cron job as the only trigger.

    The escape hatch if immediate dispatch ever misbehaves in production: runs
    keep working, they just go back to waiting for the next minute tick.
    """
    cursor.execute(DROP_DISPATCH_TRIGGER_SQL)


def _unschedule_if_exists(cursor, job_name: str) -> bool:
    cursor.execute("select 1 from cron.job where jobname = %s", (job_name,))
    if cursor.fetchone() is None:
        return False
    cursor.execute("select cron.unschedule(%s)", (job_name,))
    return True


def schedule_cron(cursor, worker_url: str) -> None:
    """(Re)point the drain-runs cron job at worker_url.

    Idempotent by unscheduling any existing job of the same name first, so a
    rerun with a new URL moves the schedule instead of adding a second job
    alongside the old one. The worker URL is embedded as a SQL literal in the
    command pg_cron stores and later runs verbatim; `quote_literal` escapes it
    the same way psycopg would, it is just done in Postgres because the value
    is going into text pg_cron will execute later, not into a query this
    script executes now.
    """
    _unschedule_if_exists(cursor, CRON_JOB_NAME)
    cursor.execute("select quote_literal(%s)", (worker_url,))
    quoted_url = cursor.fetchone()[0]
    command = "select public.trigger_drain_runs({})".format(quoted_url)
    cursor.execute("select cron.schedule(%s, %s, %s)", (CRON_JOB_NAME, CRON_SCHEDULE, command))


def unschedule_cron(cursor) -> bool:
    return _unschedule_if_exists(cursor, CRON_JOB_NAME)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Create/repair public.runs, and optionally (re)point the pg_cron "
            "job that drains it."
        ),
    )
    cron_action = parser.add_mutually_exclusive_group()
    cron_action.add_argument(
        "--schedule-cron",
        action="store_true",
        help="enable pg_cron/pg_net and (re)schedule the drain-runs job",
    )
    cron_action.add_argument(
        "--unschedule-cron",
        action="store_true",
        help="remove the drain-runs cron job without touching the table",
    )
    parser.add_argument(
        "--no-dispatch-on-insert",
        dest="dispatch_on_insert",
        action="store_false",
        help=(
            "do not create the AFTER INSERT trigger that starts a queued run "
            "immediately; runs then wait for the next cron tick (up to 60s)"
        ),
    )
    parser.add_argument(
        "--drop-dispatch-on-insert",
        action="store_true",
        help="remove the insert dispatch trigger and leave the cron job as the only trigger",
    )
    parser.add_argument(
        "--worker-url",
        default=os.environ.get("WORKER_URL"),
        help="worker endpoint the cron job posts to (default: $WORKER_URL)",
    )
    parser.add_argument(
        "--cron-secret",
        default=os.environ.get("CRON_SECRET"),
        help="bearer token the cron job sends (default: $CRON_SECRET)",
    )
    args = parser.parse_args()

    if not db.is_configured():
        print(
            "POSTGRES_URL is not set. Run `vercel env pull` first, or export it for this shell.",
            file=sys.stderr,
        )
        return 1

    if args.schedule_cron:
        if not args.worker_url:
            print(
                "--schedule-cron needs a worker URL: pass --worker-url or set WORKER_URL.",
                file=sys.stderr,
            )
            return 2
        if not args.cron_secret:
            print(
                "--schedule-cron needs a bearer secret: pass --cron-secret or set CRON_SECRET.",
                file=sys.stderr,
            )
            return 2

    try:
        with psycopg.connect(db._dsn(), connect_timeout=db.CONNECT_TIMEOUT_SECONDS) as conn:
            with conn.cursor() as cur:
                if args.drop_dispatch_on_insert:
                    drop_dispatch_trigger(cur)
                    print(
                        "Removed the {} trigger; queued runs now wait for the "
                        "next cron tick.".format(DISPATCH_TRIGGER_NAME)
                    )
                    return 0

                if args.unschedule_cron:
                    removed = unschedule_cron(cur)
                    if removed:
                        print("Removed the {} cron job.".format(CRON_JOB_NAME))
                    else:
                        print("No {} cron job was scheduled.".format(CRON_JOB_NAME))
                    return 0

                create_table(cur)
                print("public.runs is present.")
                add_attempts_column(cur)
                print("public.runs has the attempts retry counter.")
                apply_status_constraint(cur)
                print("runs_status_check enforces: {}.".format(", ".join(RUN_STATUSES)))
                create_indexes(cur)
                print("Indexes on (user_id, created_at desc) and (status) are present.")
                enable_rls(cur)
                print(
                    "Row level security is enabled on public.runs (no policies; "
                    "the backend connects as postgres and bypasses it)."
                )
                revoke_browser_role_grants(cur)

                if args.schedule_cron:
                    enable_cron_extensions(cur)
                    print("pg_cron and pg_net are enabled.")
                    upsert_vault_secret(cur, args.cron_secret)
                    print(
                        "The cron secret is stored in Supabase Vault as '{}'.".format(
                            VAULT_SECRET_NAME
                        )
                    )
                    create_drain_function(cur)
                    print(
                        "public.trigger_drain_runs() is present; execute is revoked "
                        "from everyone but the table owner."
                    )
                    schedule_cron(cur, args.worker_url)
                    print(
                        "Scheduled '{}' ('{}') to call {}.".format(
                            CRON_JOB_NAME, CRON_SCHEDULE, args.worker_url
                        )
                    )
                    if args.dispatch_on_insert:
                        create_dispatch_trigger(cur, args.worker_url)
                        print(
                            "Trigger '{}' now wakes a worker the moment a run is "
                            "queued; the cron job above stays as the safety net.".format(
                                DISPATCH_TRIGGER_NAME
                            )
                        )
    except db.StorageUnavailable as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - psycopg raises several unrelated types
        # Deliberately the exception class and not str(exc): a psycopg
        # connection error stringifies the host, the user and sometimes the
        # password, and this prints to a terminal that may well be in a
        # screen share.
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
