"""Shared test setup: a fake database and a fake signed-in user.

The database comes first. `db.py` reads `.env.local`, which on a developer
machine and on CI-with-secrets holds a live pooled connection string, and
`/api/report` now saves every finished run. Without the autouse fixture below,
any test in any file that posts a valid report body would write a real row to the
real database and wait on a real network round trip to do it. Every test
therefore talks to `FakeDb`, an in-memory stand-in, and a test that wants a
storage failure asks for it explicitly.

Authentication is the same idea. Every route in api/index.py now requires a
verified Supabase access token, and no test can mint one: the signing secret
belongs to a real project, and a JWKS fetch is a network call. `fake_auth`
stands in one fixed authenticated user for the whole suite, so a test about
NDJSON framing or CSV escaping stays a test about NDJSON framing or CSV
escaping. Tests that are actually about the gate turn it off (`enabled = False`)
or move the user (`fake_auth.user = ...`) themselves.
"""

import time

import pytest

import api.index as api_index
import db as db_module
import reddit_data


class FakeDb:
    """A stand-in for the db module: an in-memory store that records calls."""

    # Read off the real module, so a test asserting on the 503 sentence or the
    # length cap cannot drift from what the API actually uses.
    StorageUnavailable = db_module.StorageUnavailable
    UNAVAILABLE_MESSAGE = db_module.UNAVAILABLE_MESSAGE
    MAX_POST_CHARS = db_module.MAX_POST_CHARS
    # The same window the real claim_next_run reaps a stale `running` row after,
    # so a test that ages a heartbeat past it here is aging it past the real one.
    STALE_HEARTBEAT_SECONDS = db_module.STALE_HEARTBEAT_SECONDS
    # Read off the real module too, so the retry cap and the queued TTL a test
    # exercises here are the exact numbers db.py enforces, and the two sentences
    # fail_stalled_runs writes match what the API would show a reader.
    MAX_RUN_ATTEMPTS = db_module.MAX_RUN_ATTEMPTS
    QUEUED_TTL_SECONDS = db_module.QUEUED_TTL_SECONDS
    RUN_EXHAUSTED_MESSAGE = db_module.RUN_EXHAUSTED_MESSAGE
    RUN_NOT_STARTED_MESSAGE = db_module.RUN_NOT_STARTED_MESSAGE

    def __init__(self):
        self.reports = {}
        self.drafts = {}
        self.saved_report_calls = []
        # Kept apart from `saved_report_calls` rather than widened into it: the
        # tuples in that list are unpacked positionally by name in several
        # existing tests, and an owner is a different fact from the row itself.
        self.saved_report_users = []
        self.get_report_calls = []
        self.upsert_calls = []
        # Background runs. `runs` is the row store keyed by id; the *_calls lists
        # record the worker's writes the way the report/draft calls above do, so a
        # test can assert what the executor did without reaching into the rows.
        self.runs = {}
        self.create_run_calls = []
        self.get_run_calls = []
        self.progress_calls = []
        self.set_result_calls = []
        # A monotonically increasing stamp used for created_at ordering, so
        # claim_next_run and list_runs order rows the way the real created_at does
        # without depending on wall-clock ties between two rows made in one test.
        self._run_seq = 0
        # Names of the functions that should fail instead of working:
        # "unavailable" raises StorageUnavailable, "boom" raises something the
        # API never expects.
        self.unavailable = set()
        self.boom = set()
        # name -> how many of the next calls should raise StorageUnavailable
        # before the call starts working again. Models a transient database blip
        # (the terminal-write retry path leans on exactly this), distinct from
        # `unavailable`, which fails every call.
        self.transient_failures = {}

    def _maybe_fail(self, name):
        remaining = self.transient_failures.get(name, 0)
        if remaining > 0:
            self.transient_failures[name] = remaining - 1
            raise self.StorageUnavailable(self.UNAVAILABLE_MESSAGE)
        if name in self.unavailable:
            raise self.StorageUnavailable(self.UNAVAILABLE_MESSAGE)
        if name in self.boom:
            raise RuntimeError("postgres://user:hunter2@db.example.com/postgres is unreachable")

    def save_report(self, report_id, query, profile, bundle, user_id=None):
        self.saved_report_calls.append((report_id, query, profile, bundle))
        self.saved_report_users.append(user_id)
        self._maybe_fail("save_report")
        self.reports[report_id] = bundle

    def get_report(self, report_id):
        self.get_report_calls.append(report_id)
        self._maybe_fail("get_report")
        if report_id not in self.reports:
            return None
        drafts = {
            str(index): post
            for (stored_id, index), post in self.drafts.items()
            if stored_id == report_id
        }
        return {"bundle": self.reports[report_id], "drafts": drafts}

    def upsert_draft(self, report_id, idea_index, post):
        self.upsert_calls.append((report_id, idea_index, post))
        self._maybe_fail("upsert_draft")
        self.drafts[(report_id, idea_index)] = post[: self.MAX_POST_CHARS]

    # ── Background runs ──────────────────────────────────────────────────────────

    def create_run(self, run_id, user_id, query, profile, inputs):
        self.create_run_calls.append((run_id, user_id, query, profile, inputs))
        self._maybe_fail("create_run")
        self._run_seq += 1
        # A real wall-clock creation time, nudged by the sequence so two rows made
        # in one test still order strictly. The real created_at is a timestamp
        # that both orders rows and ages a `queued` run past QUEUED_TTL_SECONDS, so
        # modelling it as one lets the queued-TTL sweep be exercised by aging this
        # value rather than passing on an artefact of an integer counter.
        created = time.time() + self._run_seq * 1e-6
        self.runs[run_id] = {
            "id": run_id,
            "user_id": user_id,
            "status": "queued",
            "query": query,
            "profile": profile,
            "inputs": inputs,
            "progress": [],
            "report_id": None,
            "error": None,
            # Mirrors the real attempts column: claim_next_run bumps it on every
            # claim, and both the reaper cap and the exhaustion sweep read it.
            "attempts": 0,
            "created_at": created,
            "updated_at": created,
            "heartbeat_at": None,
        }

    def get_run(self, run_id):
        self.get_run_calls.append(run_id)
        self._maybe_fail("get_run")
        row = self.runs.get(run_id)
        return dict(row) if row is not None else None

    def list_runs(self, user_id, limit=20):
        self._maybe_fail("list_runs")
        rows = [row for row in self.runs.values() if row["user_id"] == user_id]
        rows.sort(key=lambda row: row["created_at"], reverse=True)
        return [
            {
                "id": row["id"],
                "status": row["status"],
                "query": row["query"],
                "created_at": row["created_at"],
                "report_id": row["report_id"],
            }
            for row in rows[:limit]
        ]

    def claim_next_run(self):
        self._maybe_fail("claim_next_run")
        now = time.time()
        candidates = [
            row
            for row in self.runs.values()
            if row["status"] == "queued"
            or (
                row["status"] == "running"
                and row["heartbeat_at"] is not None
                and now - row["heartbeat_at"] > self.STALE_HEARTBEAT_SECONDS
                # The reaper stops touching a stale row once it has burnt its
                # attempts, exactly as the real claim query's `attempts <` guard
                # does; fail_stalled_runs is what then ends such a row.
                and row["attempts"] < self.MAX_RUN_ATTEMPTS
            )
        ]
        if not candidates:
            return None
        row = min(candidates, key=lambda row: row["created_at"])
        row["status"] = "running"
        row["heartbeat_at"] = now
        row["attempts"] += 1
        return dict(row)

    def fail_stalled_runs(self):
        self._maybe_fail("fail_stalled_runs")
        now = time.time()
        failed = 0
        for row in self.runs.values():
            if (
                row["status"] == "running"
                and row["heartbeat_at"] is not None
                and now - row["heartbeat_at"] > self.STALE_HEARTBEAT_SECONDS
                and row["attempts"] >= self.MAX_RUN_ATTEMPTS
            ):
                row["status"] = "failed"
                row["error"] = self.RUN_EXHAUSTED_MESSAGE
                failed += 1
            elif (
                row["status"] == "queued"
                and now - row["created_at"] > self.QUEUED_TTL_SECONDS
            ):
                row["status"] = "failed"
                row["error"] = self.RUN_NOT_STARTED_MESSAGE
                failed += 1
        return failed

    def update_run_progress(self, run_id, progress):
        self.progress_calls.append((run_id, list(progress)))
        self._maybe_fail("update_run_progress")
        row = self.runs.get(run_id)
        if row is not None:
            row["progress"] = list(progress)
            row["heartbeat_at"] = time.time()

    def set_run_result(self, run_id, status, report_id=None, error=None):
        self.set_result_calls.append((run_id, status, report_id, error))
        self._maybe_fail("set_run_result")
        row = self.runs.get(run_id)
        if row is not None:
            row["status"] = status
            row["report_id"] = report_id
            row["error"] = error

    def mark_run_cancelling(self, run_id, user_id):
        self._maybe_fail("mark_run_cancelling")
        row = self.runs.get(run_id)
        if row is None or row["user_id"] != user_id or row["status"] not in ("queued", "running"):
            return False
        row["status"] = "cancelling"
        return True

    def is_cancelling(self, run_id):
        self._maybe_fail("is_cancelling")
        row = self.runs.get(run_id)
        return bool(row is not None and row["status"] == "cancelling")


@pytest.fixture(autouse=True)
def fake_db(monkeypatch):
    """Every test talks to FakeDb, never to Postgres."""
    fake = FakeDb()
    monkeypatch.setattr(api_index, "db", fake)
    return fake


TEST_USER = api_index.AuthenticatedUser("test-user", "test-user@example.com")


class FakeAuth:
    """A stand-in for api.index.authenticate_request.

    It replaces the whole check rather than just the token decode, because most
    requests in this suite send no Authorization header at all and a fake that
    only replaced the verifier would still reject them at the header. Turning it
    off (`enabled = False`) hands the request back to the real implementation,
    which is how the tests about the gate itself exercise a genuine 401.
    """

    def __init__(self, real):
        self._real = real
        self.user = TEST_USER
        self.enabled = True
        self.calls = 0

    def __call__(self, request):
        self.calls += 1
        if not self.enabled:
            return self._real(request)
        return self.user


@pytest.fixture(autouse=True)
def fake_auth(monkeypatch):
    """Every test runs as one fixed signed-in user unless it says otherwise."""
    fake = FakeAuth(api_index.authenticate_request)
    monkeypatch.setattr(api_index, "authenticate_request", fake)
    return fake


@pytest.fixture(autouse=True)
def unpaced_search(monkeypatch):
    """No real seconds spent on search pacing.

    `reddit_data` keeps text-search starts SEARCH_MIN_SPACING_SECONDS apart,
    which is correct against the live archive and pure waiting against a
    monkeypatched `_request_page`: it added roughly fifteen seconds to this
    file's own suite. The pacing itself is covered directly, with a fake clock,
    in tests/test_reddit_data.py (TestTheSearchPacer), so nothing about it is
    left untested by this being off everywhere else.

    Yields the real pacer it displaced, so a test that is about the module's own
    wiring can still assert on the thing production actually uses.
    """
    real = reddit_data._search_pacer
    monkeypatch.setattr(
        reddit_data, "_search_pacer", reddit_data._StartPacer(0), raising=True
    )
    monkeypatch.setattr(
        reddit_data, "_comment_pacer", reddit_data._StartPacer(0), raising=True
    )
    return real
