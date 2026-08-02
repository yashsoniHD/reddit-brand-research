"""Postgres persistence for saved reports and their drafts.

One table per thing worth keeping: `reports` holds the bundle a run produced,
`drafts` holds the post written for one idea inside it. A draft is keyed by report
and idea index alone, not by channel, so an idea drafted for two channels keeps
only the most recent one. Both live in a
Supabase project, and both have RLS enabled with no policies at all, so the
anon key can read nothing. Everything here connects directly to Postgres as the
`postgres` role, which bypasses RLS: the backend is the only reader and writer,
and a saved report is reachable only by knowing its id.

Three things about the connection are deliberate:

1. `POSTGRES_URL` as Vercel's Supabase integration writes it carries a `supa=`
   query parameter that psycopg rejects outright (ProgrammingError: invalid
   connection option "supa"). The query string is dropped and replaced with
   `sslmode=require`, which is what the endpoint needs anyway.
2. The URL points at Supabase's pgbouncer pooler, not at Postgres directly, so
   a connection is cheap and short-lived by design. Every function here opens
   one, does its work and closes it, which is also the only thing that can work
   on Vercel, where an instance can be frozen between requests.
3. Nothing in this module ever logs the connection string or any part of it.
   Callers get `StorageUnavailable` with a sentence that names no host, no user
   and no password.

Import-safe with no database configured: importing this module never touches
the network or reads anything but `os.environ`, and every function raises
`StorageUnavailable` when `POSTGRES_URL` is absent, so local development
without a database behaves exactly like a database that is briefly down.
"""

from __future__ import annotations

import os
import urllib.parse
from pathlib import Path
from typing import Optional

import psycopg
from psycopg.types.json import Json

# `analysis.load_dotenv()` reads `.env`, which is where the Google Cloud
# settings live. The database URL arrives from `vercel env pull`, which writes
# `.env.local` instead, and nothing else in the engine reads that file. This
# loads it once, without overriding anything already exported, so a real
# environment variable (Vercel in production, a shell export in a test) always
# wins over the file on disk.
_ENV_LOCAL = Path(__file__).resolve().parent / ".env.local"
if _ENV_LOCAL.exists():
    try:
        from dotenv import load_dotenv

        load_dotenv(_ENV_LOCAL, override=False)
    except ImportError:  # pragma: no cover - python-dotenv is a hard dependency
        pass

CONNECT_TIMEOUT_SECONDS = 10

# Enough for a very long post and nowhere near enough to be a storage problem.
MAX_POST_CHARS = 10000


class StorageUnavailable(RuntimeError):
    """Saved reports could not be reached: no database configured, or the
    database refused or dropped the connection. Carries a user-ready sentence,
    never a connection string."""


UNAVAILABLE_MESSAGE = "Saved reports are not available right now. Please try again later."


def is_configured() -> bool:
    """True when a database URL is present. Says nothing about reachability."""
    return bool(os.environ.get("POSTGRES_URL"))


def _dsn() -> str:
    raw = os.environ.get("POSTGRES_URL", "")
    if not raw:
        raise StorageUnavailable(UNAVAILABLE_MESSAGE)
    parts = urllib.parse.urlsplit(raw)
    # Query string replaced rather than merged: the only parameter Vercel puts
    # there is the one psycopg cannot parse, and sslmode is not optional for
    # this endpoint.
    return urllib.parse.urlunsplit(
        (parts.scheme, parts.netloc, parts.path, "sslmode=require", "")
    )


def _connect() -> "psycopg.Connection":
    try:
        return psycopg.connect(_dsn(), connect_timeout=CONNECT_TIMEOUT_SECONDS)
    except StorageUnavailable:
        raise
    except Exception:  # noqa: BLE001 - psycopg raises several unrelated types
        # `from None`: a psycopg connection error stringifies the host, the
        # user and sometimes the password, and an exception chain would carry
        # all of it into whatever logs the caller writes.
        raise StorageUnavailable(UNAVAILABLE_MESSAGE) from None


def save_report(
    report_id: str, query: str, profile: str, bundle: dict, user_id: Optional[str] = None
) -> None:
    """Insert one finished run. Raises StorageUnavailable on any failure.

    `user_id` is the Supabase `sub` of whoever ran it, and the column is nullable
    for two reasons: the roughly eighteen reports that predate sign-in have no
    owner to name (scripts/migrate_auth.py --claim assigns them in one go), and a
    run whose token carried no identity should still be stored rather than
    silently dropped. It is attribution only. Nothing reads it to decide who may
    open a report, because a share link is meant to work for anyone who can sign
    in; see `get_report`, which is unchanged.
    """
    version = bundle.get("bundle_version") if isinstance(bundle, dict) else None
    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                insert into public.reports (id, query, profile, bundle, bundle_version, user_id)
                values (%s, %s, %s, %s, coalesce(%s, 4), %s)
                """,
                (report_id, query, profile, Json(bundle), version, user_id),
            )
    except StorageUnavailable:
        raise
    except Exception:  # noqa: BLE001
        raise StorageUnavailable(UNAVAILABLE_MESSAGE) from None


def get_report(report_id: str) -> Optional[dict]:
    """The stored bundle and every draft saved against it, or None if there is
    no report with this id.

    Shape: {"bundle": {...}, "drafts": {"<idea_index>": "<post>", ...}}. The
    draft keys are strings because that is what they become in JSON anyway, and
    a caller reading them out of a parsed response should not have to know
    whether the server sent numbers this time.
    """
    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute("select bundle from public.reports where id = %s", (report_id,))
            row = cur.fetchone()
            if row is None:
                return None
            bundle = row[0]

            cur.execute(
                "select idea_index, post from public.drafts where report_id = %s",
                (report_id,),
            )
            drafts = {str(index): post for index, post in cur.fetchall()}
    except StorageUnavailable:
        raise
    except Exception:  # noqa: BLE001
        raise StorageUnavailable(UNAVAILABLE_MESSAGE) from None

    return {"bundle": bundle, "drafts": drafts}


STALE_HEARTBEAT_SECONDS = 300

# How many times one run may be handed to a worker before it is given up on.
# Every attempt is a fresh worker invocation that re-runs the whole pipeline (a
# Vertex synthesis call, Arctic Shift fetches, a Vercel minute), so a run that
# never writes a terminal status - SIGKILLed at Vercel's 300s limit mid-pipeline,
# or crashed before the terminal write - must not be reclaimed on every cron tick
# forever. claim_next_run counts each claim in `attempts` and refuses to reap a
# stale `running` row once it reaches this cap; fail_stalled_runs then moves that
# row to `failed`. Three is enough to ride out a transient kill or a cold start
# without turning a single run into an unbounded retry storm.
MAX_RUN_ATTEMPTS = 3

# How long a `queued` run may sit unclaimed before it is given up on. The drain
# cron fires once a minute, so a run still `queued` this long after it was
# created means the trigger path is broken (pg_cron down, or every pg_net call
# disconnecting), not merely busy. fail_stalled_runs moves it to `failed` with a
# clear sentence so the browser polling it stops instead of waiting forever.
# Fifteen minutes is generous headroom over the one-minute tick while still being
# a bound a waiting reader reaches.
QUEUED_TTL_SECONDS = 900

# The two sentences fail_stalled_runs writes into `error`. Both are user-ready
# (the run status endpoint hands `error` straight to the reader), and both name
# nothing about the machinery that gave up on the run.
RUN_EXHAUSTED_MESSAGE = "This run stopped responding and was retried the maximum number of times."
RUN_NOT_STARTED_MESSAGE = "This run did not start in time. Please try again."

# Column order every SELECT against public.runs uses, so the tuple a cursor
# hands back always lines up with `_run_row_to_dict` positionally.
_RUN_COLUMNS = (
    "id, user_id, status, query, profile, inputs, progress, report_id, "
    "error, created_at, updated_at, heartbeat_at"
)


def _run_row_to_dict(row) -> dict:
    (
        run_id,
        user_id,
        status,
        query,
        profile,
        inputs,
        progress,
        report_id,
        error,
        created_at,
        updated_at,
        heartbeat_at,
    ) = row
    return {
        "id": run_id,
        "user_id": user_id,
        "status": status,
        "query": query,
        "profile": profile,
        "inputs": inputs,
        "progress": progress,
        "report_id": report_id,
        "error": error,
        "created_at": created_at.isoformat(),
        "updated_at": updated_at.isoformat(),
        "heartbeat_at": heartbeat_at.isoformat() if heartbeat_at is not None else None,
    }


def create_run(run_id: str, user_id: str, query: str, profile: str, inputs: dict) -> None:
    """Insert one queued run. Raises StorageUnavailable on any failure.

    The id is minted by the caller (the API layer, with
    `secrets.token_urlsafe`, the same way a report id is), not here: this
    function only ever inserts the row it is handed, so nothing here decides
    the id format.
    """
    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                insert into public.runs (id, user_id, query, profile, inputs)
                values (%s, %s, %s, %s, %s)
                """,
                (run_id, user_id, query, profile, Json(inputs)),
            )
    except StorageUnavailable:
        raise
    except Exception:  # noqa: BLE001
        raise StorageUnavailable(UNAVAILABLE_MESSAGE) from None


def get_run(run_id: str) -> Optional[dict]:
    """One run by id, or None if there is no run with this id."""
    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                "select {} from public.runs where id = %s".format(_RUN_COLUMNS),
                (run_id,),
            )
            row = cur.fetchone()
    except StorageUnavailable:
        raise
    except Exception:  # noqa: BLE001
        raise StorageUnavailable(UNAVAILABLE_MESSAGE) from None
    return _run_row_to_dict(row) if row is not None else None


def list_runs(user_id: str, limit: int = 20) -> list:
    """A user's own runs, newest first. Deliberately a lighter shape than
    `get_run`: a list view has no use for `inputs` or `progress`, and leaving
    them out keeps a page of twenty runs from carrying twenty copies of a
    payload nobody there will read.
    """
    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                select id, status, query, created_at, report_id
                from public.runs
                where user_id = %s
                order by created_at desc
                limit %s
                """,
                (user_id, limit),
            )
            rows = cur.fetchall()
    except StorageUnavailable:
        raise
    except Exception:  # noqa: BLE001
        raise StorageUnavailable(UNAVAILABLE_MESSAGE) from None
    return [
        {
            "id": row_id,
            "status": status,
            "query": query,
            "created_at": created_at.isoformat(),
            "report_id": report_id,
        }
        for row_id, status, query, created_at, report_id in rows
    ]


def claim_next_run() -> Optional[dict]:
    """Atomically hand one run to the caller, or None if there is nothing to
    do.

    Picks the oldest `queued` row, or a `running` row whose heartbeat has not
    been touched in `STALE_HEARTBEAT_SECONDS` (a worker that died mid-run
    without marking anything terminal), and flips it to `running` with a fresh
    heartbeat before returning it. `for update skip locked` inside one
    transaction is what makes this safe under overlapping workers: two workers
    racing this query never see the same row, because whichever locks it first
    makes the other skip straight past it instead of blocking on it.

    Every claim increments `attempts`, and the stale-`running` reaper only picks
    up a row whose attempts are still below MAX_RUN_ATTEMPTS: a run that keeps
    dying before it can write a terminal status is retried a bounded number of
    times, not on every tick forever. fail_stalled_runs is what then ends the
    row the reaper has stopped touching.
    """
    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                select id from public.runs
                where status = 'queued'
                   or (status = 'running'
                       and heartbeat_at < now() - interval '{stale} seconds'
                       and attempts < {max_attempts})
                order by created_at asc
                for update skip locked
                limit 1
                """.format(stale=STALE_HEARTBEAT_SECONDS, max_attempts=MAX_RUN_ATTEMPTS)
            )
            row = cur.fetchone()
            if row is None:
                return None
            run_id = row[0]
            cur.execute(
                """
                update public.runs
                set status = 'running', heartbeat_at = now(), updated_at = now(),
                    attempts = attempts + 1
                where id = %s
                returning {}
                """.format(_RUN_COLUMNS),
                (run_id,),
            )
            updated = cur.fetchone()
    except StorageUnavailable:
        raise
    except Exception:  # noqa: BLE001
        raise StorageUnavailable(UNAVAILABLE_MESSAGE) from None
    return _run_row_to_dict(updated) if updated is not None else None


def fail_stalled_runs() -> int:
    """Deterministically end runs that can no longer make progress, moving each
    to `failed` with a user-ready sentence. Two independent cases, both swept in
    one call the worker route makes before it claims anything:

      - a `running` row whose heartbeat went stale AND whose attempts have
        reached MAX_RUN_ATTEMPTS. claim_next_run has already stopped reaping it
        (see its `attempts <` guard), so without this it would sit `running`
        forever; this is what actually turns "retried the maximum number of
        times" into a terminal `failed` the reader can see.
      - a `queued` row older than QUEUED_TTL_SECONDS. No worker ever claimed it
        (the drain trigger is down, or every pg_net call disconnects), so the
        browser polling it would otherwise wait forever.

    Two statements rather than one so each case carries its own message. Returns
    how many rows were failed in total, for the caller to log; it is safe to run
    on an idle queue, where it fails nothing and returns 0.
    """
    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                update public.runs
                set status = 'failed', error = %s, updated_at = now()
                where status = 'running'
                  and heartbeat_at < now() - interval '{stale} seconds'
                  and attempts >= {max_attempts}
                """.format(stale=STALE_HEARTBEAT_SECONDS, max_attempts=MAX_RUN_ATTEMPTS),
                (RUN_EXHAUSTED_MESSAGE,),
            )
            exhausted = cur.rowcount or 0
            cur.execute(
                """
                update public.runs
                set status = 'failed', error = %s, updated_at = now()
                where status = 'queued'
                  and created_at < now() - interval '{ttl} seconds'
                """.format(ttl=QUEUED_TTL_SECONDS),
                (RUN_NOT_STARTED_MESSAGE,),
            )
            stalled = cur.rowcount or 0
    except StorageUnavailable:
        raise
    except Exception:  # noqa: BLE001
        raise StorageUnavailable(UNAVAILABLE_MESSAGE) from None
    return exhausted + stalled


def update_run_progress(run_id: str, progress: list) -> None:
    """Overwrite the progress log and touch the heartbeat in one write, so a
    worker reporting where it is also proves to `claim_next_run`'s stale
    reaper that it is still alive.
    """
    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                update public.runs
                set progress = %s, heartbeat_at = now(), updated_at = now()
                where id = %s
                """,
                (Json(progress), run_id),
            )
    except StorageUnavailable:
        raise
    except Exception:  # noqa: BLE001
        raise StorageUnavailable(UNAVAILABLE_MESSAGE) from None


def set_run_result(
    run_id: str,
    status: str,
    report_id: Optional[str] = None,
    error: Optional[str] = None,
) -> None:
    """Move a run to a terminal state (`succeeded`, `failed`, or `cancelled`).

    Takes both `report_id` and `error` unconditionally rather than branching
    on `status` itself, because the caller already knows which of the two
    applies and a terminal write should be one statement, not a decision this
    function has to re-derive.
    """
    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                update public.runs
                set status = %s, report_id = %s, error = %s, updated_at = now()
                where id = %s
                """,
                (status, report_id, error, run_id),
            )
    except StorageUnavailable:
        raise
    except Exception:  # noqa: BLE001
        raise StorageUnavailable(UNAVAILABLE_MESSAGE) from None


def mark_run_cancelling(run_id: str, user_id: str) -> bool:
    """Ask that a run stop, if `user_id` owns it and it is still stoppable.

    The `where` clause carries both checks at once: a run belonging to someone
    else matches zero rows exactly like a run that has already finished, so
    both come back False from the same query rather than needing a separate
    ownership check that could race the status itself.
    """
    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                update public.runs
                set status = 'cancelling', updated_at = now()
                where id = %s and user_id = %s and status in ('queued', 'running')
                """,
                (run_id, user_id),
            )
            flipped = cur.rowcount > 0
    except StorageUnavailable:
        raise
    except Exception:  # noqa: BLE001
        raise StorageUnavailable(UNAVAILABLE_MESSAGE) from None
    return flipped


def is_cancelling(run_id: str) -> bool:
    """Whether the run's owner has asked it to stop. A worker polls this at
    stage boundaries rather than being interrupted, so a run only ever stops
    between stages, never mid-stage. A run id that does not exist reads as
    False rather than raising, the same way a missing row elsewhere in this
    module reads as absence rather than error.
    """
    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute("select status = 'cancelling' from public.runs where id = %s", (run_id,))
            row = cur.fetchone()
    except StorageUnavailable:
        raise
    except Exception:  # noqa: BLE001
        raise StorageUnavailable(UNAVAILABLE_MESSAGE) from None
    return bool(row[0]) if row is not None else False


def upsert_draft(report_id: str, idea_index: int, post: str) -> None:
    """Save the post written for one idea, replacing any earlier version.

    The foreign key means a draft for an unknown report is rejected by the
    database rather than stored as an orphan; that surfaces as
    StorageUnavailable like any other write failure, because from the caller's
    side both mean "this did not save".
    """
    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                insert into public.drafts (report_id, idea_index, post)
                values (%s, %s, %s)
                on conflict (report_id, idea_index)
                do update set post = excluded.post, updated_at = now()
                """,
                (report_id, idea_index, post[:MAX_POST_CHARS]),
            )
    except StorageUnavailable:
        raise
    except Exception:  # noqa: BLE001
        raise StorageUnavailable(UNAVAILABLE_MESSAGE) from None
