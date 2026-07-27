"""Shared test setup.

The one thing here is the database. `db.py` reads `.env.local`, which on a
developer machine and on CI-with-secrets holds a live pooled connection string,
and `/api/report` now saves every finished run. Without the autouse fixture
below, any test in any file that posts a valid report body would write a real
row to the real database and wait on a real network round trip to do it. Every
test therefore talks to `FakeDb`, an in-memory stand-in, and a test that wants
a storage failure asks for it explicitly.
"""

import pytest

import api.index as api_index
import db as db_module


class FakeDb:
    """A stand-in for the db module: an in-memory store that records calls."""

    # Read off the real module, so a test asserting on the 503 sentence or the
    # length cap cannot drift from what the API actually uses.
    StorageUnavailable = db_module.StorageUnavailable
    UNAVAILABLE_MESSAGE = db_module.UNAVAILABLE_MESSAGE
    MAX_POST_CHARS = db_module.MAX_POST_CHARS

    def __init__(self):
        self.reports = {}
        self.drafts = {}
        self.saved_report_calls = []
        self.get_report_calls = []
        self.upsert_calls = []
        # Names of the functions that should fail instead of working:
        # "unavailable" raises StorageUnavailable, "boom" raises something the
        # API never expects.
        self.unavailable = set()
        self.boom = set()

    def _maybe_fail(self, name):
        if name in self.unavailable:
            raise self.StorageUnavailable(self.UNAVAILABLE_MESSAGE)
        if name in self.boom:
            raise RuntimeError("postgres://user:hunter2@db.example.com/postgres is unreachable")

    def save_report(self, report_id, query, profile, bundle):
        self.saved_report_calls.append((report_id, query, profile, bundle))
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


@pytest.fixture(autouse=True)
def fake_db(monkeypatch):
    """Every test talks to FakeDb, never to Postgres."""
    fake = FakeDb()
    monkeypatch.setattr(api_index, "db", fake)
    return fake
