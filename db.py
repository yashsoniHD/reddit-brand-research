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


def save_report(report_id: str, query: str, profile: str, bundle: dict) -> None:
    """Insert one finished run. Raises StorageUnavailable on any failure."""
    version = bundle.get("bundle_version") if isinstance(bundle, dict) else None
    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                insert into public.reports (id, query, profile, bundle, bundle_version)
                values (%s, %s, %s, %s, coalesce(%s, 4))
                """,
                (report_id, query, profile, Json(bundle), version),
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
