"""Tests for the background-runs database surface: db.py's run helpers and
scripts/migrate_runs.py.

Neither is exercised against a live database, matching how the rest of this
project's db-adjacent code is tested (see TestTheAuthMigrationScript in
tests/test_api.py, which this mirrors for the sibling migration script).
db.py's helpers are unit tested here with a fake connection/cursor that
records what was executed, since api.index's tests stand in FakeDb instead of
calling db.py's real functions at all.
"""

from __future__ import annotations

import datetime
import importlib.util
import pathlib

import pytest

import db


def _normalize(sql: str) -> str:
    return " ".join(sql.split()).lower()


# ---------------------------------------------------------------------------
# A fake psycopg connection/cursor, just enough of the protocol db.py uses.
# ---------------------------------------------------------------------------


class FakeCursor:
    def __init__(self, fetchone_results=None, fetchall_results=None, rowcount=0):
        self.executed = []
        # Each call to fetchone()/fetchall() pops the next queued result, so a
        # test can script a cursor through more than one round trip (e.g.
        # claim_next_run's select-then-update) by queueing one entry per call.
        self._fetchone_queue = list(fetchone_results or [])
        self._fetchall_queue = list(fetchall_results or [])
        self.rowcount = rowcount

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return self._fetchone_queue.pop(0) if self._fetchone_queue else None

    def fetchall(self):
        return self._fetchall_queue.pop(0) if self._fetchall_queue else []

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


@pytest.fixture
def fake_cursor(monkeypatch):
    """Patches db._connect to hand back a FakeConnection wrapping this cursor."""

    def _install(cursor):
        monkeypatch.setattr(db, "_connect", lambda: FakeConnection(cursor))
        return cursor

    return _install


SOME_TIME = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)


def _full_run_row(
    run_id="run-1",
    user_id="user-1",
    status="queued",
    query="q",
    profile="p",
    inputs=None,
    progress=None,
    report_id=None,
    error=None,
    created_at=SOME_TIME,
    updated_at=SOME_TIME,
    heartbeat_at=None,
):
    return (
        run_id,
        user_id,
        status,
        query,
        profile,
        inputs if inputs is not None else {"k": "v"},
        progress if progress is not None else [],
        report_id,
        error,
        created_at,
        updated_at,
        heartbeat_at,
    )


# ---------------------------------------------------------------------------
# db.py: create_run / get_run / list_runs
# ---------------------------------------------------------------------------


class TestCreateRun:
    def test_inserts_the_row_with_the_given_fields(self, fake_cursor):
        cursor = fake_cursor(FakeCursor())
        db.create_run("run-1", "user-1", "brand x", "founder", {"a": 1})

        sql, params = cursor.executed[0]
        normalized = _normalize(sql)
        assert normalized.startswith("insert into public.runs")
        assert "values" in normalized
        run_id, user_id, query, profile, inputs = params
        assert (run_id, user_id, query, profile) == ("run-1", "user-1", "brand x", "founder")
        assert inputs.obj == {"a": 1}

    def test_does_not_mint_or_validate_the_id(self, fake_cursor):
        # The id is the caller's job; create_run stores whatever it is given,
        # including something that would fail api.index's REPORT_ID_PATTERN.
        cursor = fake_cursor(FakeCursor())
        db.create_run("not a valid id!!", "user-1", "q", "p", {})
        _, params = cursor.executed[0]
        assert params[0] == "not a valid id!!"

    def test_storage_unavailable_propagates(self, monkeypatch):
        def _boom():
            raise db.StorageUnavailable(db.UNAVAILABLE_MESSAGE)

        monkeypatch.setattr(db, "_connect", _boom)
        with pytest.raises(db.StorageUnavailable):
            db.create_run("run-1", "user-1", "q", "p", {})

    def test_unexpected_errors_become_storage_unavailable(self, fake_cursor):
        cursor = fake_cursor(FakeCursor())

        def _explode(sql, params=None):
            raise RuntimeError("postgres://user:hunter2@db.example.com/postgres is unreachable")

        cursor.execute = _explode
        with pytest.raises(db.StorageUnavailable) as exc_info:
            db.create_run("run-1", "user-1", "q", "p", {})
        assert "hunter2" not in str(exc_info.value)


class TestGetRun:
    def test_returns_none_for_a_missing_run(self, fake_cursor):
        fake_cursor(FakeCursor(fetchone_results=[None]))
        assert db.get_run("missing") is None

    def test_shapes_a_found_run(self, fake_cursor):
        fake_cursor(FakeCursor(fetchone_results=[_full_run_row()]))
        run = db.get_run("run-1")
        assert run["id"] == "run-1"
        assert run["user_id"] == "user-1"
        assert run["status"] == "queued"
        assert run["inputs"] == {"k": "v"}
        assert run["progress"] == []
        assert run["report_id"] is None
        assert run["error"] is None
        assert run["created_at"] == SOME_TIME.isoformat()
        assert run["updated_at"] == SOME_TIME.isoformat()
        assert run["heartbeat_at"] is None

    def test_heartbeat_is_iso_string_when_present(self, fake_cursor):
        fake_cursor(FakeCursor(fetchone_results=[_full_run_row(heartbeat_at=SOME_TIME)]))
        run = db.get_run("run-1")
        assert run["heartbeat_at"] == SOME_TIME.isoformat()

    def test_query_is_parameterised(self, fake_cursor):
        cursor = fake_cursor(FakeCursor(fetchone_results=[None]))
        db.get_run("run-1")
        sql, params = cursor.executed[0]
        assert "run-1" not in sql
        assert params == ("run-1",)


class TestListRuns:
    def test_shapes_the_lighter_list_view(self, fake_cursor):
        rows = [
            ("run-2", "succeeded", "q2", SOME_TIME, "report-9"),
            ("run-1", "queued", "q1", SOME_TIME, None),
        ]
        fake_cursor(FakeCursor(fetchall_results=[rows]))
        runs = db.list_runs("user-1")
        assert runs == [
            {
                "id": "run-2",
                "status": "succeeded",
                "query": "q2",
                "created_at": SOME_TIME.isoformat(),
                "report_id": "report-9",
            },
            {
                "id": "run-1",
                "status": "queued",
                "query": "q1",
                "created_at": SOME_TIME.isoformat(),
                "report_id": None,
            },
        ]

    def test_orders_newest_first_and_scopes_to_the_user(self, fake_cursor):
        cursor = fake_cursor(FakeCursor(fetchall_results=[[]]))
        db.list_runs("user-1", limit=5)
        sql, params = cursor.executed[0]
        normalized = _normalize(sql)
        assert "where user_id = %s" in normalized
        assert "order by created_at desc" in normalized
        assert params == ("user-1", 5)

    def test_default_limit_is_twenty(self, fake_cursor):
        cursor = fake_cursor(FakeCursor(fetchall_results=[[]]))
        db.list_runs("user-1")
        _, params = cursor.executed[0]
        assert params == ("user-1", 20)


# ---------------------------------------------------------------------------
# db.py: claim_next_run
# ---------------------------------------------------------------------------


class TestClaimNextRun:
    def test_returns_none_when_nothing_to_claim(self, fake_cursor):
        fake_cursor(FakeCursor(fetchone_results=[None]))
        assert db.claim_next_run() is None

    def test_claims_and_returns_the_updated_row(self, fake_cursor):
        updated_row = _full_run_row(status="running")
        cursor = fake_cursor(FakeCursor(fetchone_results=[("run-1",), updated_row]))
        claimed = db.claim_next_run()
        assert claimed["id"] == "run-1"
        assert claimed["status"] == "running"

    def test_select_uses_for_update_skip_locked(self, fake_cursor):
        cursor = fake_cursor(FakeCursor(fetchone_results=[None]))
        db.claim_next_run()
        sql, _ = cursor.executed[0]
        normalized = _normalize(sql)
        assert "for update skip locked" in normalized
        assert "status = 'queued'" in normalized
        assert "status = 'running'" in normalized
        assert "heartbeat_at <" in normalized

    def test_uses_the_stale_heartbeat_constant_in_the_select(self, fake_cursor):
        cursor = fake_cursor(FakeCursor(fetchone_results=[None]))
        db.claim_next_run()
        sql, _ = cursor.executed[0]
        assert "{} seconds".format(db.STALE_HEARTBEAT_SECONDS) in sql

    def test_update_sets_running_and_touches_heartbeat(self, fake_cursor):
        updated_row = _full_run_row(status="running")
        cursor = fake_cursor(FakeCursor(fetchone_results=[("run-1",), updated_row]))
        db.claim_next_run()
        sql, params = cursor.executed[1]
        normalized = _normalize(sql)
        assert "set status = 'running', heartbeat_at = now(), updated_at = now()" in normalized
        assert params == ("run-1",)

    def test_no_second_query_when_nothing_was_selected(self, fake_cursor):
        cursor = fake_cursor(FakeCursor(fetchone_results=[None]))
        db.claim_next_run()
        assert len(cursor.executed) == 1

    def test_the_claim_increments_the_attempts_counter(self, fake_cursor):
        updated_row = _full_run_row(status="running")
        cursor = fake_cursor(FakeCursor(fetchone_results=[("run-1",), updated_row]))
        db.claim_next_run()
        sql, _ = cursor.executed[1]
        assert "attempts = attempts + 1" in _normalize(sql)

    def test_the_reaper_is_capped_at_max_run_attempts(self, fake_cursor):
        # A stale `running` row is only reclaimed while its attempts are still
        # under the cap, so a run that keeps dying is retried a bounded number of
        # times rather than on every tick forever.
        cursor = fake_cursor(FakeCursor(fetchone_results=[None]))
        db.claim_next_run()
        sql, _ = cursor.executed[0]
        normalized = _normalize(sql)
        assert "attempts < {}".format(db.MAX_RUN_ATTEMPTS) in normalized


# ---------------------------------------------------------------------------
# db.py: fail_stalled_runs
# ---------------------------------------------------------------------------


class TestFailStalledRuns:
    def test_exhausted_running_rows_are_failed_with_the_exhaustion_message(self, fake_cursor):
        cursor = fake_cursor(FakeCursor(rowcount=1))
        db.fail_stalled_runs()
        sql, params = cursor.executed[0]
        normalized = _normalize(sql)
        assert normalized.startswith("update public.runs")
        assert "set status = 'failed'" in normalized
        assert "where status = 'running'" in normalized
        assert "heartbeat_at <" in normalized
        assert "attempts >= {}".format(db.MAX_RUN_ATTEMPTS) in normalized
        assert params == (db.RUN_EXHAUSTED_MESSAGE,)

    def test_queued_rows_past_the_ttl_are_failed_with_the_not_started_message(self, fake_cursor):
        cursor = fake_cursor(FakeCursor(rowcount=1))
        db.fail_stalled_runs()
        sql, params = cursor.executed[1]
        normalized = _normalize(sql)
        assert "where status = 'queued'" in normalized
        assert "created_at <" in normalized
        assert "{} seconds".format(db.QUEUED_TTL_SECONDS) in sql
        assert params == (db.RUN_NOT_STARTED_MESSAGE,)

    def test_it_returns_the_total_rows_it_failed(self, fake_cursor):
        # Both statements report one row each against this cursor, so the helper
        # sums them: the caller logs how many runs it gave up on.
        fake_cursor(FakeCursor(rowcount=1))
        assert db.fail_stalled_runs() == 2

    def test_storage_unavailable_propagates(self, monkeypatch):
        def _boom():
            raise db.StorageUnavailable(db.UNAVAILABLE_MESSAGE)

        monkeypatch.setattr(db, "_connect", _boom)
        with pytest.raises(db.StorageUnavailable):
            db.fail_stalled_runs()

    def test_unexpected_errors_become_storage_unavailable(self, fake_cursor):
        cursor = fake_cursor(FakeCursor())

        def _explode(sql, params=None):
            raise RuntimeError("postgres://user:hunter2@db.example.com/postgres is unreachable")

        cursor.execute = _explode
        with pytest.raises(db.StorageUnavailable) as exc_info:
            db.fail_stalled_runs()
        assert "hunter2" not in str(exc_info.value)


class TestRetryConstants:
    def test_the_cap_and_ttl_are_sane_bounds(self):
        assert db.MAX_RUN_ATTEMPTS >= 1
        # A queued run must be given a good deal longer than the reaper window
        # before it is declared never-started, or a merely busy tick would sweep
        # a run that is about to be claimed.
        assert db.QUEUED_TTL_SECONDS > db.STALE_HEARTBEAT_SECONDS


# ---------------------------------------------------------------------------
# db.py: update_run_progress / set_run_result
# ---------------------------------------------------------------------------


class TestUpdateRunProgress:
    def test_overwrites_progress_and_touches_heartbeat(self, fake_cursor):
        cursor = fake_cursor(FakeCursor())
        db.update_run_progress("run-1", ["step one", "step two"])
        sql, params = cursor.executed[0]
        normalized = _normalize(sql)
        assert normalized.startswith("update public.runs")
        assert "heartbeat_at = now()" in normalized
        assert "updated_at = now()" in normalized
        progress, run_id = params
        assert progress.obj == ["step one", "step two"]
        assert run_id == "run-1"


class TestSetRunResult:
    def test_succeeded_carries_the_report_id(self, fake_cursor):
        cursor = fake_cursor(FakeCursor())
        db.set_run_result("run-1", "succeeded", report_id="report-9")
        sql, params = cursor.executed[0]
        assert _normalize(sql).startswith("update public.runs")
        assert params == ("succeeded", "report-9", None, "run-1")

    def test_failed_carries_the_error(self, fake_cursor):
        cursor = fake_cursor(FakeCursor())
        db.set_run_result("run-1", "failed", error="pipeline blew up")
        _, params = cursor.executed[0]
        assert params == ("failed", None, "pipeline blew up", "run-1")

    def test_touches_updated_at(self, fake_cursor):
        cursor = fake_cursor(FakeCursor())
        db.set_run_result("run-1", "cancelled")
        sql, _ = cursor.executed[0]
        assert "updated_at = now()" in _normalize(sql)


# ---------------------------------------------------------------------------
# db.py: mark_run_cancelling / is_cancelling
# ---------------------------------------------------------------------------


class TestMarkRunCancelling:
    def test_true_when_a_row_flipped(self, fake_cursor):
        fake_cursor(FakeCursor(rowcount=1))
        assert db.mark_run_cancelling("run-1", "user-1") is True

    def test_false_when_nothing_matched(self, fake_cursor):
        fake_cursor(FakeCursor(rowcount=0))
        assert db.mark_run_cancelling("run-1", "user-1") is False

    def test_checks_ownership_and_status_in_one_where_clause(self, fake_cursor):
        cursor = fake_cursor(FakeCursor(rowcount=0))
        db.mark_run_cancelling("run-1", "user-1")
        sql, params = cursor.executed[0]
        normalized = _normalize(sql)
        assert "where id = %s and user_id = %s and status in ('queued', 'running')" in normalized
        assert params == ("run-1", "user-1")


class TestIsCancelling:
    def test_true_when_status_is_cancelling(self, fake_cursor):
        fake_cursor(FakeCursor(fetchone_results=[(True,)]))
        assert db.is_cancelling("run-1") is True

    def test_false_when_status_is_something_else(self, fake_cursor):
        fake_cursor(FakeCursor(fetchone_results=[(False,)]))
        assert db.is_cancelling("run-1") is False

    def test_false_when_the_run_does_not_exist(self, fake_cursor):
        fake_cursor(FakeCursor(fetchone_results=[None]))
        assert db.is_cancelling("missing") is False


# ---------------------------------------------------------------------------
# scripts/migrate_runs.py, without a database (mirrors
# TestTheAuthMigrationScript in tests/test_api.py for scripts/migrate_auth.py)
# ---------------------------------------------------------------------------


class TestTheRunsMigrationScript:
    @staticmethod
    def _script():
        path = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "migrate_runs.py"
        spec = importlib.util.spec_from_file_location("migrate_runs_under_test", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    class RecordingCursor:
        def __init__(self, rowcount=0, regclass="public.runs", fetchone_results=None):
            self.executed = []
            self.rowcount = rowcount
            self._regclass = regclass
            self._fetchone_queue = list(fetchone_results) if fetchone_results is not None else None

        def execute(self, sql, args=None):
            self.executed.append((sql, args))

        def fetchone(self):
            if self._fetchone_queue is not None:
                return self._fetchone_queue.pop(0) if self._fetchone_queue else None
            # Default: behaves like `select to_regclass(...)`.
            return (self._regclass,)

    def test_table_creation_is_if_not_exists(self):
        script = self._script()
        cursor = self.RecordingCursor()
        script.create_table(cursor)
        sql, args = cursor.executed[0]
        normalized = _normalize(sql)
        assert "create table if not exists public.runs" in normalized
        assert args is None

    def test_table_has_every_required_column(self):
        script = self._script()
        normalized = _normalize(script.CREATE_TABLE_SQL)
        for column in (
            "id text primary key",
            "user_id text not null",
            "status text not null default 'queued'",
            "query text not null",
            "profile text not null default ''",
            "inputs jsonb not null",
            "progress jsonb not null default '[]'::jsonb",
            "report_id text references public.reports(id)",
            "error text",
            "attempts int not null default 0",
            "created_at timestamptz not null default now()",
            "updated_at timestamptz not null default now()",
            "heartbeat_at timestamptz",
        ):
            assert column in normalized, column

    def test_attempts_column_is_added_idempotently_for_existing_tables(self):
        # A table created before the retry counter existed is upgraded in place;
        # `add column if not exists` is a no-op on a freshly-created one.
        script = self._script()
        cursor = self.RecordingCursor()
        script.add_attempts_column(cursor)
        sql, _ = cursor.executed[0]
        normalized = _normalize(sql)
        assert "alter table public.runs add column if not exists attempts int not null default 0" in normalized

    def test_status_constraint_is_dropped_before_added(self):
        script = self._script()
        cursor = self.RecordingCursor()
        script.apply_status_constraint(cursor)
        first_sql, _ = cursor.executed[0]
        second_sql, _ = cursor.executed[1]
        assert "drop constraint if exists runs_status_check" in _normalize(first_sql)
        assert "add constraint runs_status_check check" in _normalize(second_sql)

    def test_status_constraint_covers_every_run_status(self):
        script = self._script()
        normalized = _normalize(script.ADD_STATUS_CONSTRAINT_SQL)
        for status in (
            "queued",
            "running",
            "succeeded",
            "failed",
            "cancelling",
            "cancelled",
        ):
            assert "'{}'".format(status) in normalized

    def test_indexes_cover_user_created_and_status(self):
        script = self._script()
        cursor = self.RecordingCursor()
        script.create_indexes(cursor)
        statements = [_normalize(sql) for sql, _ in cursor.executed]
        assert any("on public.runs (user_id, created_at desc)" in sql for sql in statements)
        assert any("on public.runs (status)" in sql for sql in statements)
        assert all("if not exists" in sql for sql in statements)

    def test_rls_is_enabled_with_no_policy_statements(self):
        script = self._script()
        cursor = self.RecordingCursor()
        script.enable_rls(cursor)
        sql, _ = cursor.executed[0]
        assert "enable row level security" in _normalize(sql)
        assert not any("policy" in _normalize(s) for s, _ in cursor.executed)

    def test_it_only_targets_the_runs_table(self):
        script = self._script()
        assert script.REVOKE_TABLES == ("public.runs",)

    def test_revoke_closes_the_browser_facing_roles(self):
        script = self._script()
        cursor = self.RecordingCursor(regclass="public.runs")
        touched = script.revoke_browser_role_grants(cursor)
        assert touched == ["public.runs"]
        revokes = [
            _normalize(sql) for sql, _ in cursor.executed if sql.strip().lower().startswith("revoke")
        ]
        assert "revoke all privileges on table public.runs from anon, authenticated" in revokes

    def test_revoke_skips_a_table_that_is_not_present(self):
        script = self._script()
        cursor = self.RecordingCursor(regclass=None)
        touched = script.revoke_browser_role_grants(cursor)
        assert touched == []
        assert not any(sql.strip().lower().startswith("revoke") for sql, _ in cursor.executed)

    # -- pg_cron / pg_net -----------------------------------------------

    def test_enable_cron_extensions_uses_if_not_exists(self):
        script = self._script()
        cursor = self.RecordingCursor()
        script.enable_cron_extensions(cursor)
        statements = [_normalize(sql) for sql, _ in cursor.executed]
        assert "create extension if not exists pg_cron" in statements
        assert "create extension if not exists pg_net" in statements

    def test_upsert_vault_secret_creates_when_absent(self):
        script = self._script()
        cursor = self.RecordingCursor(fetchone_results=[None])
        script.upsert_vault_secret(cursor, "shh")
        sql, params = cursor.executed[-1]
        assert "vault.create_secret" in _normalize(sql)
        assert params[0] == "shh"
        assert params[1] == script.VAULT_SECRET_NAME

    def test_upsert_vault_secret_updates_when_present(self):
        script = self._script()
        cursor = self.RecordingCursor(fetchone_results=[("existing-id",)])
        script.upsert_vault_secret(cursor, "new-secret")
        sql, params = cursor.executed[-1]
        assert "vault.update_secret" in _normalize(sql)
        assert params == ("existing-id", "new-secret")

    def test_the_secret_never_appears_in_the_drain_function_sql(self):
        script = self._script()
        assert "shh" not in script.CREATE_DRAIN_FUNCTION_SQL
        assert script.VAULT_SECRET_NAME in script.CREATE_DRAIN_FUNCTION_SQL

    def test_drain_function_is_security_definer(self):
        script = self._script()
        assert "security definer" in _normalize(script.CREATE_DRAIN_FUNCTION_SQL)

    def test_drain_function_sets_a_five_minute_pg_net_timeout(self):
        # The default pg_net timeout is 5s; the worker route runs the full
        # pipeline (up to ~285s) while pg_net holds the connection, so the
        # default would disconnect mid-run, which is the exact bug the queue
        # exists to fix.
        script = self._script()
        normalized = _normalize(script.CREATE_DRAIN_FUNCTION_SQL)
        assert "timeout_milliseconds := 300000" in normalized

    def test_create_drain_function_revokes_public_execute(self):
        script = self._script()
        cursor = self.RecordingCursor()
        script.create_drain_function(cursor)
        statements = [_normalize(sql) for sql, _ in cursor.executed]
        assert any("security definer" in sql for sql in statements)
        assert "revoke all on function public.trigger_drain_runs(text) from public" in statements

    def test_schedule_cron_unschedules_before_scheduling(self):
        script = self._script()
        # First fetchone: existence check inside _unschedule_if_exists (job
        # exists). Second fetchone: quote_literal's result.
        cursor = self.RecordingCursor(fetchone_results=[(1,), ("'https://example.com/worker'",)])
        script.schedule_cron(cursor, "https://example.com/worker")
        statements = [_normalize(sql) for sql, _ in cursor.executed]
        unschedule_index = next(i for i, s in enumerate(statements) if "cron.unschedule" in s)
        schedule_index = next(i for i, s in enumerate(statements) if "cron.schedule" in s)
        assert unschedule_index < schedule_index

    def test_schedule_cron_does_not_unschedule_when_nothing_is_scheduled(self):
        script = self._script()
        cursor = self.RecordingCursor(fetchone_results=[None, ("'https://example.com/worker'",)])
        script.schedule_cron(cursor, "https://example.com/worker")
        statements = [_normalize(sql) for sql, _ in cursor.executed]
        assert not any("cron.unschedule" in s for s in statements)
        assert any("cron.schedule" in s for s in statements)

    def test_schedule_cron_passes_the_job_name_and_schedule_as_parameters(self):
        script = self._script()
        cursor = self.RecordingCursor(fetchone_results=[None, ("'https://example.com/worker'",)])
        script.schedule_cron(cursor, "https://example.com/worker")
        sql, params = cursor.executed[-1]
        assert "cron.schedule" in _normalize(sql)
        assert params[0] == script.CRON_JOB_NAME
        assert params[1] == script.CRON_SCHEDULE
        assert "trigger_drain_runs" in params[2]

    def test_cron_schedule_is_every_minute(self):
        script = self._script()
        assert script.CRON_SCHEDULE == "* * * * *"

    # -- dispatch on insert -------------------------------------------------
    # The cron job is the floor, not the latency budget: on its own a queued run
    # waits up to a minute for the next tick. These cover the AFTER INSERT
    # trigger that wakes a worker immediately, and the flag that removes it
    # again if pg_net ever misbehaves on insert.

    def test_dispatch_trigger_fires_after_insert_on_queued_rows_only(self):
        script = self._script()
        normalized = _normalize(script.CREATE_DISPATCH_TRIGGER_SQL)
        assert "after insert on public.runs" in normalized
        assert "for each row" in normalized
        # Without the WHEN clause a backfill or a fixture row inserted in any
        # other status would fire an HTTP call nobody asked for.
        assert "when (new.status = 'queued')" in normalized

    def test_dispatch_function_calls_the_same_drain_helper_the_cron_job_uses(self):
        # One code path for "wake a worker", so the secret handling is not
        # duplicated into a second SECURITY DEFINER function.
        script = self._script()
        assert "public.trigger_drain_runs" in script.CREATE_DISPATCH_FUNCTION_SQL

    def test_create_dispatch_trigger_quotes_the_url_and_revokes_public_execute(self):
        script = self._script()
        cursor = self.RecordingCursor(fetchone_results=[("'https://example.com/worker'",)])
        script.create_dispatch_trigger(cursor, "https://example.com/worker")
        statements = [_normalize(sql) for sql, _ in cursor.executed]
        assert any("quote_literal" in sql for sql in statements)
        assert any("'https://example.com/worker'" in sql for sql in statements)
        assert any(
            "revoke all on function public.dispatch_queued_run() from public" in sql
            for sql in statements
        )

    def test_create_dispatch_trigger_drops_the_old_trigger_first(self):
        # Idempotent reruns: pointing the dispatch at a new deployment must move
        # it, not leave a second trigger calling the old URL.
        script = self._script()
        cursor = self.RecordingCursor(fetchone_results=[("'https://example.com/worker'",)])
        script.create_dispatch_trigger(cursor, "https://example.com/worker")
        statements = [_normalize(sql) for sql, _ in cursor.executed]
        drop_index = next(i for i, s in enumerate(statements) if "drop trigger if exists" in s)
        create_index = next(
            i for i, s in enumerate(statements) if s.startswith("create trigger")
        )
        assert drop_index < create_index

    def test_drop_dispatch_trigger_leaves_the_cron_job_alone(self):
        script = self._script()
        cursor = self.RecordingCursor()
        script.drop_dispatch_trigger(cursor)
        statements = [_normalize(sql) for sql, _ in cursor.executed]
        assert statements == ["drop trigger if exists runs_dispatch_after_insert on public.runs"]

    def test_unschedule_cron_reports_whether_a_job_was_removed(self):
        script = self._script()
        cursor = self.RecordingCursor(fetchone_results=[(1,)])
        assert script.unschedule_cron(cursor) is True

        cursor = self.RecordingCursor(fetchone_results=[None])
        assert script.unschedule_cron(cursor) is False

    # -- main() -----------------------------------------------------------

    def test_it_refuses_to_run_without_a_database_url(self, monkeypatch, capsys):
        script = self._script()
        monkeypatch.setattr(script.db, "is_configured", lambda: False)
        monkeypatch.setattr(script.sys, "argv", ["migrate_runs.py"])
        assert script.main() == 1
        assert "POSTGRES_URL" in capsys.readouterr().err

    def test_schedule_cron_without_a_worker_url_is_refused(self, monkeypatch, capsys):
        script = self._script()
        monkeypatch.setattr(script.db, "is_configured", lambda: True)
        monkeypatch.delenv("WORKER_URL", raising=False)
        monkeypatch.setattr(
            script.sys,
            "argv",
            ["migrate_runs.py", "--schedule-cron", "--cron-secret", "shh"],
        )
        assert script.main() == 2
        assert "worker" in capsys.readouterr().err.lower()

    def test_schedule_cron_without_a_secret_is_refused(self, monkeypatch, capsys):
        script = self._script()
        monkeypatch.setattr(script.db, "is_configured", lambda: True)
        monkeypatch.delenv("CRON_SECRET", raising=False)
        monkeypatch.setattr(
            script.sys,
            "argv",
            [
                "migrate_runs.py",
                "--schedule-cron",
                "--worker-url",
                "https://example.com/worker",
            ],
        )
        assert script.main() == 2
        assert "secret" in capsys.readouterr().err.lower()

    def test_schedule_and_unschedule_are_mutually_exclusive(self, monkeypatch):
        script = self._script()
        monkeypatch.setattr(
            script.sys,
            "argv",
            ["migrate_runs.py", "--schedule-cron", "--unschedule-cron"],
        )
        with pytest.raises(SystemExit):
            script.main()

    def test_it_reuses_the_dsn_handling_db_already_has(self, monkeypatch):
        # Not its own copy of the `supa=` stripping and the sslmode default:
        # the one place that knows how to read POSTGRES_URL is db.py.
        script = self._script()
        monkeypatch.setenv(
            "POSTGRES_URL", "postgres://u:p@db.example.com:6543/postgres?supa=base-pooler.x"
        )
        dsn = script.db._dsn()
        assert "supa=" not in dsn
        assert dsn.endswith("sslmode=require")
