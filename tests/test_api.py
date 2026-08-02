"""FastAPI layer: the NDJSON streaming contract, the sample endpoint, exports and storage.

This file adds no business logic of its own (see api/index.py's docstring), so these
tests are about the adapter, not the engine: does a valid run stream progress then a
terminal report line, does invalid input short-circuit to one "invalid" line, does an
engine failure become one clean "error" line, does the sample endpoint read the file
fresh every time. Fully offline: pipeline.run_report and the report_export builders
are monkeypatched, so nothing here touches a real model or the Reddit archive.

Storage is faked for every test by the `fake_db` fixture in conftest.py, so no test
in this file opens a database connection either.
"""

import json
import logging
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

import api.index as api_index
import db as db_module
import pipeline
from api.index import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    """The in-memory rate limiter (api_index._rate_limit_hits) is module-level
    state shared by every test in this process; without a reset between tests,
    a handful of tests each posting one valid /api/report body would add up
    across the file and start tripping the real 6-per-hour default, which is
    a test-isolation artefact, not something any single test is exercising."""
    api_index._rate_limit_hits.clear()
    yield
    api_index._rate_limit_hits.clear()


VALID_BODY = {
    "query": "ai automation ideas for freelancers",
    "profile": "",
}


class TestReportCreateHappyPath:
    """POST /api/report no longer runs anything: it validates, queues one run, and
    returns its id. The work happens later, in the worker (TestTheWorkerRoute and
    TestExecuteRun), driven by an external minute-cron. A closed tab is now a no-op
    server-side, not a cancel signal."""

    def test_valid_input_returns_202_with_a_run_id(self, fake_db):
        response = client.post("/api/report", json=VALID_BODY)
        assert response.status_code == 202
        run_id = response.json()["run_id"]
        assert api_index.REPORT_ID_PATTERN.match(run_id)

    def test_the_run_is_queued_for_the_caller_with_the_validated_inputs(self, fake_db):
        run_id = client.post("/api/report", json=VALID_BODY).json()["run_id"]
        row = fake_db.runs[run_id]
        assert row["status"] == "queued"
        # Queued as the fixed signed-in user, with the run's own cleaned query.
        assert row["user_id"] == "test-user"
        assert row["query"] == VALID_BODY["query"]
        assert row["profile"] == ""
        # The whole run request is frozen onto the row for the worker to replay.
        assert set(row["inputs"]) >= {
            "days", "subreddits", "relevance_filter", "content_types", "phrases", "channels"
        }

    def test_two_runs_get_different_ids(self, fake_db):
        first = client.post("/api/report", json=VALID_BODY).json()["run_id"]
        second = client.post("/api/report", json=VALID_BODY).json()["run_id"]
        assert first != second

    def test_the_engine_is_not_touched_at_create_time(self, monkeypatch, fake_db):
        # Creating a run must not run it: a run_report that raises when called
        # proves the create path never reaches the engine.
        def exploding(*args, **kwargs):
            raise AssertionError("run_report must not be called when creating a run")

        monkeypatch.setattr(api_index.pipeline, "run_report", exploding)
        assert client.post("/api/report", json=VALID_BODY).status_code == 202


class TestReportCreateValidation:
    """Validation still short-circuits before anything is queued, and returns the
    same {"type": "invalid", "errors": [...]} body the old stream's first line
    carried, now as plain JSON rather than one NDJSON line."""

    @staticmethod
    def _invalid(response):
        assert response.status_code == 200
        body = response.json()
        assert body["type"] == "invalid"
        return body["errors"]

    def test_invalid_input_returns_a_single_invalid_body_using_real_validate_inputs(self):
        # No monkeypatch: pipeline.validate_inputs runs for real, so this also proves
        # the API is wired to the actual validation rules, not a stand-in for them.
        errors = self._invalid(client.post("/api/report", json={"query": "short", "profile": ""}))
        assert len(errors) == 1  # too-short query
        assert all(message[0].isupper() and message.endswith(".") for message in errors)

    def test_invalid_input_queues_no_run(self, fake_db):
        client.post("/api/report", json={"query": "short", "profile": ""})
        assert fake_db.create_run_calls == []

    def test_unknown_fields_from_an_old_client_are_ignored(self, fake_db):
        response = client.post("/api/report", json={**VALID_BODY, "platform": "linkedin"})
        assert response.status_code == 202

    def test_a_bad_time_range_is_invalid(self):
        errors = self._invalid(client.post("/api/report", json={**VALID_BODY, "days": 3}))
        assert any("time range" in message for message in errors)

    def test_bad_subreddit_input_is_invalid(self):
        self._invalid(
            client.post(
                "/api/report",
                json={**VALID_BODY, "subreddits": "one,two,three,four,five,six,seven,eight,nine"},
            )
        )

    def test_empty_query_is_invalid(self):
        self._invalid(client.post("/api/report", json={**VALID_BODY, "query": ""}))

    def test_garbage_body_is_reported_as_invalid_not_a_500(self):
        response = client.post(
            "/api/report", content="not json at all", headers={"content-type": "application/json"}
        )
        self._invalid(response)

    def test_non_object_json_body_is_reported_as_invalid(self):
        self._invalid(client.post("/api/report", json=[1, 2, 3]))

    def test_missing_body_fields_are_reported_as_invalid(self):
        assert self._invalid(client.post("/api/report", json={}))


class TestReportInputsPassThrough:
    """The advanced settings are frozen onto the queued row for the worker to
    replay; old clients send one content_type and it lands as a one-item list."""

    @staticmethod
    def _inputs(fake_db):
        assert len(fake_db.create_run_calls) == 1
        # create_run(run_id, user_id, query, profile, inputs)
        return fake_db.create_run_calls[0][4]

    def test_a_list_of_content_types_is_stored(self, fake_db):
        response = client.post("/api/report", json={**VALID_BODY, "content_types": ["poll", "text"]})
        assert response.status_code == 202
        assert self._inputs(fake_db)["content_types"] == ["poll", "text"]

    def test_the_legacy_single_field_is_stored_as_a_list(self, fake_db):
        # A shared link or a replayed history entry from before the setting took
        # several values must not quietly lose the format it asked for.
        response = client.post("/api/report", json={**VALID_BODY, "content_type": "carousel"})
        assert response.status_code == 202
        assert self._inputs(fake_db)["content_types"] == ["carousel"]

    def test_no_content_types_is_stored_as_an_empty_list(self, fake_db):
        client.post("/api/report", json=VALID_BODY)
        assert self._inputs(fake_db)["content_types"] == []

    def test_an_unknown_content_type_is_invalid_before_anything_is_queued(self, fake_db):
        response = client.post("/api/report", json={**VALID_BODY, "content_types": ["reel"]})
        body = response.json()
        assert body["type"] == "invalid"
        assert '"reel"' in body["errors"][0]
        assert fake_db.create_run_calls == []

    def test_a_list_of_channels_is_stored(self, fake_db):
        response = client.post("/api/report", json={**VALID_BODY, "channels": ["tiktok", "instagram"]})
        assert response.status_code == 202
        assert self._inputs(fake_db)["channels"] == ["instagram", "tiktok"]

    def test_no_channels_is_stored_as_an_empty_list(self, fake_db):
        client.post("/api/report", json=VALID_BODY)
        assert self._inputs(fake_db)["channels"] == []

    def test_an_unknown_channel_is_invalid_before_anything_is_queued(self, fake_db):
        response = client.post("/api/report", json={**VALID_BODY, "channels": ["myspace"]})
        body = response.json()
        assert body["type"] == "invalid"
        assert '"myspace"' in body["errors"][0]
        assert fake_db.create_run_calls == []

    def test_channels_that_are_not_a_list_are_invalid(self):
        assert client.post("/api/report", json={**VALID_BODY, "channels": 7}).json()["type"] == "invalid"

    def test_content_types_that_are_not_a_list_are_invalid(self):
        assert client.post("/api/report", json={**VALID_BODY, "content_types": 7}).json()["type"] == "invalid"


class TestReportCreateStorage:
    """Unlike a saved report (best-effort persistence on top of a run that already
    happened), a run cannot exist without its durable row: no queue, no run. So a
    storage failure at create time is a clean 503 the frontend can surface, not a
    swallowed warning."""

    def test_storage_unavailable_is_a_503(self, fake_db):
        fake_db.unavailable.add("create_run")
        response = client.post("/api/report", json=VALID_BODY)
        assert response.status_code == 503
        assert response.json()["message"] == db_module.UNAVAILABLE_MESSAGE

    def test_an_unexpected_storage_failure_leaks_nothing(self, fake_db):
        fake_db.boom.add("create_run")
        response = client.post("/api/report", json=VALID_BODY)
        assert response.status_code == 503
        assert "hunter2" not in response.text
        assert response.json() == {"message": db_module.UNAVAILABLE_MESSAGE}


class TestSampleEndpoint:
    def test_the_real_bundled_sample_is_actually_served(self):
        """The repository's own sample_report.json, not a fixture standing in for it.

        Every other test here writes its own bundle to a tmp path, so all of them
        passed while production served {"available": false}: the API had its own
        hand-written `BUNDLE_VERSION = 4` and the engine had moved to 5, and
        nothing compared the file that actually ships against the guard that
        actually runs. This is that comparison.
        """
        response = client.get("/api/sample")
        assert response.status_code == 200
        body = response.json()
        assert body["available"] is True, (
            "sample_report.json is at version {} but the API serves {}".format(
                json.loads(api_index.SAMPLE_PATH.read_text()).get("bundle_version"),
                sorted(api_index.SUPPORTED_BUNDLE_VERSIONS),
            )
        )
        assert body["bundle"]["bundle_version"] in api_index.SUPPORTED_BUNDLE_VERSIONS

    def test_the_current_engine_version_is_served(self):
        # A bump that forgets this list is the exact failure above.
        assert pipeline.BUNDLE_VERSION in api_index.SUPPORTED_BUNDLE_VERSIONS

    def test_available_when_the_file_is_a_v4_bundle(self, tmp_path, monkeypatch):
        sample_path = tmp_path / "sample.json"
        sample_path.write_text(json.dumps({"bundle_version": 4, "inputs": {"query": "x"}}))
        monkeypatch.setattr(api_index, "SAMPLE_PATH", sample_path)

        response = client.get("/api/sample")
        assert response.status_code == 200
        body = response.json()
        assert body["available"] is True
        assert body["bundle"]["bundle_version"] == 4

    def test_unavailable_when_the_file_is_still_v1(self, tmp_path, monkeypatch):
        sample_path = tmp_path / "sample.json"
        sample_path.write_text(json.dumps({"bundle_version": 1, "inputs": {}}))
        monkeypatch.setattr(api_index, "SAMPLE_PATH", sample_path)

        assert client.get("/api/sample").json() == {"available": False}

    def test_unavailable_when_the_file_is_still_v3(self, tmp_path, monkeypatch):
        sample_path = tmp_path / "sample.json"
        sample_path.write_text(json.dumps({"bundle_version": 3, "inputs": {}}))
        monkeypatch.setattr(api_index, "SAMPLE_PATH", sample_path)

        assert client.get("/api/sample").json() == {"available": False}

    def test_unavailable_when_the_file_is_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(api_index, "SAMPLE_PATH", tmp_path / "does-not-exist.json")
        assert client.get("/api/sample").json() == {"available": False}

    def test_unavailable_when_the_file_is_not_valid_json(self, tmp_path, monkeypatch):
        sample_path = tmp_path / "sample.json"
        sample_path.write_text("not json")
        monkeypatch.setattr(api_index, "SAMPLE_PATH", sample_path)
        assert client.get("/api/sample").json() == {"available": False}

    def test_unavailable_when_the_file_is_a_json_array_not_an_object(self, tmp_path, monkeypatch):
        sample_path = tmp_path / "sample.json"
        sample_path.write_text("[1, 2, 3]")
        monkeypatch.setattr(api_index, "SAMPLE_PATH", sample_path)
        assert client.get("/api/sample").json() == {"available": False}


class TestExportEndpoints:
    BUNDLE = {
        "bundle_version": 3,
        "inputs": {"query": "ai tools for founders"},
        "generated_at": "2026-07-26 10:00 UTC",
    }

    @staticmethod
    def _patch_exporter(monkeypatch, kind, body, media_type, extension, suffix):
        """EXPORTERS binds the builder function at import time, so patching
        `report_export.<name>` after the fact would silently miss; the dict entry
        itself is what post_export actually consults, so that is what gets patched."""
        monkeypatch.setitem(
            api_index.EXPORTERS, kind, (lambda bundle: body, media_type, extension, suffix)
        )

    def test_markdown_export_sets_content_disposition_from_filename_stem(self, monkeypatch):
        self._patch_exporter(
            monkeypatch, "markdown", "# Report body", "text/markdown; charset=utf-8", "md", "report"
        )

        response = client.post("/api/export/markdown", json={"bundle": self.BUNDLE})
        assert response.status_code == 200
        assert response.text == "# Report body"
        assert response.headers["content-type"].startswith("text/markdown")
        assert response.headers["content-disposition"] == (
            'attachment; filename="content-ideas-ai-tools-for-founders-2026-07-26-report.md"'
        )

    def test_ideas_csv_export_uses_the_csv_media_type_and_extension(self, monkeypatch):
        self._patch_exporter(
            monkeypatch, "ideas-csv", "title,hook\nA,B\n", "text/csv; charset=utf-8", "csv", "ideas"
        )

        response = client.post("/api/export/ideas-csv", json={"bundle": self.BUNDLE})
        assert response.status_code == 200
        assert response.text == "title,hook\nA,B\n"
        assert response.headers["content-type"].startswith("text/csv")
        assert response.headers["content-disposition"].endswith(
            'content-ideas-ai-tools-for-founders-2026-07-26-ideas.csv"'
        )

    def test_posts_csv_export_uses_the_posts_builder(self, monkeypatch):
        self._patch_exporter(
            monkeypatch, "posts-csv", "subreddit,title\nx,y\n", "text/csv; charset=utf-8", "csv", "posts"
        )

        response = client.post("/api/export/posts-csv", json={"bundle": self.BUNDLE})
        assert response.status_code == 200
        assert response.text == "subreddit,title\nx,y\n"
        assert response.headers["content-disposition"].endswith(
            'content-ideas-ai-tools-for-founders-2026-07-26-posts.csv"'
        )

    def test_comments_csv_export_uses_the_comments_builder(self, monkeypatch):
        self._patch_exporter(
            monkeypatch, "comments-csv", "subreddit,comment\nx,y\n", "text/csv; charset=utf-8", "csv", "comments"
        )

        response = client.post("/api/export/comments-csv", json={"bundle": self.BUNDLE})
        assert response.status_code == 200
        assert response.text == "subreddit,comment\nx,y\n"
        assert response.headers["content-disposition"].endswith(
            '-comments.csv"'
        )

    def test_the_three_csv_exports_never_share_a_filename(self, monkeypatch):
        """The regression this guards: all three CSVs used to be built from the
        bare stem plus ".csv", so a later download landed on top of an earlier
        one instead of getting its own file."""
        self._patch_exporter(
            monkeypatch, "ideas-csv", "a\n", "text/csv; charset=utf-8", "csv", "ideas"
        )
        self._patch_exporter(
            monkeypatch, "posts-csv", "b\n", "text/csv; charset=utf-8", "csv", "posts"
        )
        self._patch_exporter(
            monkeypatch, "comments-csv", "c\n", "text/csv; charset=utf-8", "csv", "comments"
        )

        ideas = client.post("/api/export/ideas-csv", json={"bundle": self.BUNDLE})
        posts = client.post("/api/export/posts-csv", json={"bundle": self.BUNDLE})
        comments = client.post("/api/export/comments-csv", json={"bundle": self.BUNDLE})
        names = {
            ideas.headers["content-disposition"],
            posts.headers["content-disposition"],
            comments.headers["content-disposition"],
        }
        assert len(names) == 3

    def test_unknown_export_kind_is_a_404(self):
        response = client.post("/api/export/spreadsheet", json={"bundle": self.BUNDLE})
        assert response.status_code == 404

    def test_garbage_body_is_a_400_not_a_500(self):
        response = client.post(
            "/api/export/markdown", content="not json at all", headers={"content-type": "application/json"}
        )
        assert response.status_code == 400

    def test_missing_bundle_key_is_a_400(self):
        response = client.post("/api/export/markdown", json={"not_the_bundle_key": True})
        assert response.status_code == 400

    def test_bundle_without_a_version_field_is_a_400(self):
        response = client.post("/api/export/markdown", json={"bundle": {"inputs": {}}})
        assert response.status_code == 400

    def test_bundle_that_is_not_an_object_is_a_400(self):
        response = client.post("/api/export/markdown", json={"bundle": "not an object"})
        assert response.status_code == 400

    def test_real_exporters_produce_a_download_end_to_end(self):
        # No patching here: exercises the real report_export functions once, so this
        # file also proves the wiring works, not only that mocks can be swapped in.
        bundle = {
            "bundle_version": 4,
            "inputs": {"query": "ai tools for founders", "profile": "",
                       "subreddits": ["testsub"]},
            "generated_at": "2026-07-26 10:00 UTC", "model_label": "test-model", "source": "Test archive",
            "discovery": {"communities": [], "considered": []},
            "notices": [], "posts": [], "comments": [], "items": [],
            "sample": {"posts_fetched": 0, "comments_fetched": 0, "posts_analysed": 0,
                       "comments_analysed": 0, "items_analysed": 0, "posts_truncated": False,
                       "comments_truncated": False, "low_data": True, "capacity_limited": False},
            "analysis": {"executive_summary": "", "topics": [], "pain_points": [], "questions": [],
                         "content_ideas": [], "item_sentiments": []},
            "stats": {"sentiment": {"counts": {"pos": 0, "neu": 0, "neg": 0},
                                     "percentages": {"pos": 0, "neu": 0, "neg": 0},
                                     "classified": 0, "unclassified": 0},
                      "engagement": {"score": 0, "comments": 0},
                      "date_range": {"oldest": "", "newest": "", "newest_ts": 0, "label": "n/a",
                                     "short_label": "n/a"},
                      "lag_hours": 0.0, "trend": []},
        }
        response = client.post("/api/export/markdown", json={"bundle": bundle})
        assert response.status_code == 200
        assert "content-ideas-ai-tools-for-founders-2026-07-26" in response.headers["content-disposition"]


class TestDraftEndpoint:
    """POST /api/draft: one plain-JSON call, no streaming. This file adds no business
    logic of its own, so these tests patch `api.index.analysis.draft_post` and check
    only the adapter's shaping: status codes, {"post"}/{"error"} shape, and the
    client-error/service-error split between 422 and 500."""

    VALID_DRAFT_BODY = {
        "idea": {
            "title": "One system", "hook": "Stop juggling five apps.", "format": "Text post",
            "angle": "Consolidate", "addresses": "Too many tools",
        },
        "evidence": ["We quoted 20 hours and delivered 65.", "Every retainer starts with a favour."],
        "query": "ai automation ideas for freelancers",
        "profile": "",
    }

    def test_happy_path_returns_the_post(self, monkeypatch):
        captured = {}

        def fake_draft_post(idea, evidence, query, profile):
            captured["args"] = (idea, evidence, query, profile)
            return {"post": "Stop juggling five apps.\n\nHere is why."}

        monkeypatch.setattr(api_index.analysis, "draft_post", fake_draft_post)

        response = client.post("/api/draft", json=self.VALID_DRAFT_BODY)

        assert response.status_code == 200
        assert response.json() == {"post": "Stop juggling five apps.\n\nHere is why."}
        idea, evidence, query, profile = captured["args"]
        assert idea["hook"] == "Stop juggling five apps."
        assert evidence == self.VALID_DRAFT_BODY["evidence"]
        assert query == self.VALID_DRAFT_BODY["query"]
        assert profile == ""

    def test_a_known_channel_reaches_the_engine(self, monkeypatch):
        captured = {}

        def fake_draft_post(idea, evidence, query, profile, channel=None):
            captured["channel"] = channel
            return {"post": "ok"}

        monkeypatch.setattr(api_index.analysis, "draft_post", fake_draft_post)
        response = client.post(
            "/api/draft", json={**self.VALID_DRAFT_BODY, "channel": "instagram"}
        )

        assert response.status_code == 200
        assert captured["channel"] == "instagram"

    def test_an_unknown_channel_is_dropped_rather_than_refused(self, monkeypatch):
        # The post is still writable channel-neutral, so a stale client must not
        # lose a draft over a channel this build does not know.
        captured = {}

        def fake_draft_post(idea, evidence, query, profile, channel=None):
            captured["channel"] = channel
            return {"post": "ok"}

        monkeypatch.setattr(api_index.analysis, "draft_post", fake_draft_post)
        response = client.post(
            "/api/draft", json={**self.VALID_DRAFT_BODY, "channel": "myspace"}
        )

        assert response.status_code == 200
        assert captured["channel"] is None

    def test_a_channel_of_the_wrong_type_is_dropped(self, monkeypatch):
        captured = {}

        def fake_draft_post(idea, evidence, query, profile, channel=None):
            captured["channel"] = channel
            return {"post": "ok"}

        monkeypatch.setattr(api_index.analysis, "draft_post", fake_draft_post)
        response = client.post(
            "/api/draft", json={**self.VALID_DRAFT_BODY, "channel": 7}
        )

        assert response.status_code == 200
        assert captured["channel"] is None

    def test_a_draft_engine_that_predates_channels_is_never_passed_one(self, monkeypatch):
        # The channel goes through the same signature probe the report kwargs do,
        # so an older engine keeps working instead of raising a TypeError.
        captured = {}

        def old_signature(idea, evidence, query, profile):
            captured["called"] = True
            return {"post": "ok"}

        monkeypatch.setattr(api_index.analysis, "draft_post", old_signature)
        response = client.post(
            "/api/draft", json={**self.VALID_DRAFT_BODY, "channel": "tiktok"}
        )

        assert response.status_code == 200
        assert captured["called"] is True

    def test_value_error_from_the_engine_becomes_a_500_with_the_verbatim_message(self, monkeypatch):
        def fake_draft_post(idea, evidence, query, profile):
            raise ValueError("We could not draft that post. Please try again.")

        monkeypatch.setattr(api_index.analysis, "draft_post", fake_draft_post)
        response = client.post("/api/draft", json=self.VALID_DRAFT_BODY)

        assert response.status_code == 500
        assert response.json() == {"error": "We could not draft that post. Please try again."}

    def test_unexpected_exception_becomes_the_generic_draft_error(self, monkeypatch):
        def fake_draft_post(idea, evidence, query, profile):
            raise RuntimeError("some internal detail nobody outside should see")

        monkeypatch.setattr(api_index.analysis, "draft_post", fake_draft_post)
        response = client.post("/api/draft", json=self.VALID_DRAFT_BODY)

        assert response.status_code == 500
        assert response.json() == {"error": api_index.GENERIC_DRAFT_ERROR_MESSAGE}
        assert "internal detail" not in response.text

    def test_garbage_body_is_a_422_not_a_500(self):
        response = client.post(
            "/api/draft", content="not json at all", headers={"content-type": "application/json"}
        )
        assert response.status_code == 422
        assert "error" in response.json()

    def test_non_object_body_is_a_422(self):
        response = client.post("/api/draft", json=[1, 2, 3])
        assert response.status_code == 422

    def test_missing_idea_is_a_422(self):
        body = {**self.VALID_DRAFT_BODY}
        del body["idea"]
        response = client.post("/api/draft", json=body)
        assert response.status_code == 422
        assert "error" in response.json()

    def test_idea_that_is_not_a_dict_is_a_422(self, monkeypatch):
        # Any draft_post call here would be a bug: a malformed idea must be rejected
        # before the engine is ever reached.
        def unexpected_call(*args, **kwargs):
            raise AssertionError("draft_post should not be called for a malformed idea")

        monkeypatch.setattr(api_index.analysis, "draft_post", unexpected_call)
        response = client.post("/api/draft", json={**self.VALID_DRAFT_BODY, "idea": "not a dict"})
        assert response.status_code == 422

    def test_evidence_that_is_not_a_list_is_a_422(self, monkeypatch):
        def unexpected_call(*args, **kwargs):
            raise AssertionError("draft_post should not be called for malformed evidence")

        monkeypatch.setattr(api_index.analysis, "draft_post", unexpected_call)
        response = client.post("/api/draft", json={**self.VALID_DRAFT_BODY, "evidence": "not a list"})
        assert response.status_code == 422

    def test_missing_evidence_defaults_to_an_empty_list(self, monkeypatch):
        captured = {}

        def fake_draft_post(idea, evidence, query, profile):
            captured["evidence"] = evidence
            return {"post": "A post."}

        monkeypatch.setattr(api_index.analysis, "draft_post", fake_draft_post)
        body = {**self.VALID_DRAFT_BODY}
        del body["evidence"]
        response = client.post("/api/draft", json=body)

        assert response.status_code == 200
        assert captured["evidence"] == []

    def test_evidence_longer_than_six_is_truncated_before_reaching_the_engine(self, monkeypatch):
        captured = {}

        def fake_draft_post(idea, evidence, query, profile):
            captured["evidence"] = evidence
            return {"post": "A post."}

        monkeypatch.setattr(api_index.analysis, "draft_post", fake_draft_post)
        body = {**self.VALID_DRAFT_BODY, "evidence": ["excerpt {}".format(i) for i in range(20)]}
        client.post("/api/draft", json=body)

        assert len(captured["evidence"]) == 6
        assert captured["evidence"] == ["excerpt {}".format(i) for i in range(6)]

    def test_idea_fields_are_coerced_to_strings(self, monkeypatch):
        captured = {}

        def fake_draft_post(idea, evidence, query, profile):
            captured["idea"] = idea
            return {"post": "A post."}

        monkeypatch.setattr(api_index.analysis, "draft_post", fake_draft_post)
        body = {
            **self.VALID_DRAFT_BODY,
            "idea": {"title": 123, "hook": None, "format": "Text post", "angle": True, "addresses": "x"},
        }
        response = client.post("/api/draft", json=body)

        assert response.status_code == 200
        assert captured["idea"]["title"] == "123"
        assert captured["idea"]["hook"] == ""
        assert captured["idea"]["angle"] == "True"
        assert all(isinstance(value, str) for value in captured["idea"].values())


# ── Saved reports and drafts ────────────────────────────────────────────────────


def _fake_run(monkeypatch, bundle=None):
    """Stub the engine with a run that succeeds instantly.

    Used two ways now: by the rate-limit and quota tests, which only need
    /api/report to accept a body (the engine is never reached at create time), and
    by the worker tests, where _execute_run does call it. Harmless in the first
    case, load-bearing in the second."""

    def fake_run_report(query, profile, progress=None, **advanced):
        return bundle if bundle is not None else {"bundle_version": 4, "inputs": {"query": query}}

    monkeypatch.setattr(api_index.pipeline, "run_report", fake_run_report)


# A run id shaped like a real one (REPORT_ID_PATTERN), for tests that seed a row
# directly rather than through POST /api/report.
SEEDED_RUN_ID = "seededRun_-01"
WORKER_SECRET = "cron-secret-under-test"


def _default_inputs():
    return {
        "days": 7,
        "subreddits": None,
        "relevance_filter": True,
        "content_types": [],
        "phrases": [],
        "channels": [],
    }


def _seed_run(fake_db, run_id=SEEDED_RUN_ID, user_id="test-user", inputs=None):
    """Put one queued run on the fake and hand back the row shape claim_next_run
    would return, so a test can drive _execute_run directly."""
    fake_db.create_run(run_id, user_id, VALID_BODY["query"], "", inputs or _default_inputs())
    return fake_db.get_run(run_id)


class TestExecuteRun:
    """_execute_run takes one claimed row to a terminal state. This is where the
    engine actually runs now, so the success/cancel/error mapping and the
    server-side save that used to ride the stream's terminal line live here."""

    def test_success_saves_the_report_and_marks_succeeded(self, monkeypatch, fake_db):
        _fake_run(monkeypatch, bundle={"bundle_version": 4, "inputs": {"query": "x"}})
        run = _seed_run(fake_db)

        api_index._execute_run(run)

        row = fake_db.runs[SEEDED_RUN_ID]
        assert row["status"] == "succeeded"
        report_id = row["report_id"]
        assert api_index.REPORT_ID_PATTERN.match(report_id)
        # Saved with the run's own inputs and owner, not a client's claim, and the
        # id sits next to the bundle in the row, never inside the stored bundle.
        saved_id, query, profile, bundle = fake_db.saved_report_calls[0]
        assert saved_id == report_id
        assert query == VALID_BODY["query"]
        assert fake_db.saved_report_users == ["test-user"]
        assert "report_id" not in bundle

    def test_a_storage_miss_on_save_still_succeeds_without_a_report_id(self, monkeypatch, fake_db):
        # A database that cannot store the bundle costs the reader a share link,
        # never the run: the run still lands `succeeded`, just with no report_id.
        fake_db.unavailable.add("save_report")
        _fake_run(monkeypatch)
        run = _seed_run(fake_db)

        api_index._execute_run(run)

        row = fake_db.runs[SEEDED_RUN_ID]
        assert row["status"] == "succeeded"
        assert row["report_id"] is None

    def test_run_cancelled_marks_cancelled_and_saves_nothing(self, monkeypatch, fake_db, caplog):
        def fake_run_report(query, profile, progress=None, **advanced):
            raise pipeline.RunCancelled("selecting")

        monkeypatch.setattr(api_index.pipeline, "run_report", fake_run_report)
        run = _seed_run(fake_db)

        with caplog.at_level(logging.INFO, logger=api_index.logger.name):
            api_index._execute_run(run)

        assert fake_db.runs[SEEDED_RUN_ID]["status"] == "cancelled"
        assert fake_db.set_result_calls[-1] == (SEEDED_RUN_ID, "cancelled", None, None)
        assert fake_db.saved_report_calls == []
        # A cancel is the system working: info, no traceback.
        assert not any(record.exc_info for record in caplog.records)

    def test_run_error_marks_failed_with_the_verbatim_message(self, monkeypatch, fake_db):
        def fake_run_report(query, profile, progress=None, **advanced):
            raise pipeline.RunError("We could not read anything usable from r/testsub.")

        monkeypatch.setattr(api_index.pipeline, "run_report", fake_run_report)
        run = _seed_run(fake_db)

        api_index._execute_run(run)

        row = fake_db.runs[SEEDED_RUN_ID]
        assert row["status"] == "failed"
        assert row["error"] == "We could not read anything usable from r/testsub."
        assert fake_db.saved_report_calls == []

    def test_value_error_marks_failed_with_its_message(self, monkeypatch, fake_db):
        def fake_run_report(query, profile, progress=None, **advanced):
            raise ValueError("The analysis service has reached its rate limit.")

        monkeypatch.setattr(api_index.pipeline, "run_report", fake_run_report)
        run = _seed_run(fake_db)

        api_index._execute_run(run)
        assert fake_db.runs[SEEDED_RUN_ID]["error"] == "The analysis service has reached its rate limit."

    def test_an_unexpected_exception_is_the_generic_message_and_still_logs_a_traceback(
        self, monkeypatch, fake_db, caplog
    ):
        def fake_run_report(query, profile, progress=None, **advanced):
            raise RuntimeError("some internal detail nobody outside should see")

        monkeypatch.setattr(api_index.pipeline, "run_report", fake_run_report)
        run = _seed_run(fake_db)

        with caplog.at_level(logging.INFO, logger=api_index.logger.name):
            api_index._execute_run(run)

        row = fake_db.runs[SEEDED_RUN_ID]
        assert row["status"] == "failed"
        assert row["error"] == api_index.GENERIC_ERROR_MESSAGE
        assert "internal detail" not in row["error"]
        # The genuine last resort still logs a traceback, as the old stream did.
        assert any(record.exc_info for record in caplog.records)

    def test_progress_is_coalesced_onto_the_row(self, monkeypatch, fake_db):
        def fake_run_report(query, profile, progress=None, **advanced):
            progress("Finding communities...")
            progress("Fetching posts...")
            progress("Synthesising...")
            return {"bundle_version": 4}

        monkeypatch.setattr(api_index.pipeline, "run_report", fake_run_report)
        run = _seed_run(fake_db)

        api_index._execute_run(run)

        # The terminal flush always lands the full accumulated log, in order.
        assert fake_db.runs[SEEDED_RUN_ID]["progress"] == [
            "Finding communities...", "Fetching posts...", "Synthesising...",
        ]
        assert fake_db.progress_calls  # at least one flush happened

    def test_the_inputs_are_replayed_into_run_report(self, monkeypatch, fake_db):
        captured = {}

        def fake_run_report(query, profile, progress=None, **advanced):
            captured["args"] = (query, profile)
            captured["advanced"] = advanced
            return {"bundle_version": 4}

        monkeypatch.setattr(api_index.pipeline, "run_report", fake_run_report)
        inputs = {**_default_inputs(), "content_types": ["poll"], "channels": ["tiktok"], "days": 30}
        run = _seed_run(fake_db, inputs=inputs)

        api_index._execute_run(run)

        assert captured["args"] == (VALID_BODY["query"], "")
        assert captured["advanced"]["content_types"] == ["poll"]
        assert captured["advanced"]["channels"] == ["tiktok"]
        assert captured["advanced"]["days"] == 30

    def test_the_owner_pressing_cancel_stops_the_run(self, monkeypatch, fake_db):
        # End to end through the real poller: the owner marks the run cancelling,
        # the daemon poller sees is_cancelling and sets the event, and run_report
        # (which honours it at a stage boundary) raises RunCancelled.
        monkeypatch.setattr(api_index, "CANCEL_POLL_SECONDS", 0.01)
        run = _seed_run(fake_db)
        assert fake_db.mark_run_cancelling(SEEDED_RUN_ID, "test-user") is True

        def fake_run_report(query, profile, progress=None, cancel=None, deadline=None, **advanced):
            deadline_at = time.monotonic() + 2.0
            while time.monotonic() < deadline_at:
                if cancel is not None and cancel.is_set():
                    raise pipeline.RunCancelled("stage boundary")
                time.sleep(0.01)
            return {"bundle_version": 4}

        monkeypatch.setattr(api_index.pipeline, "run_report", fake_run_report)

        api_index._execute_run(run)
        assert fake_db.runs[SEEDED_RUN_ID]["status"] == "cancelled"

    def test_a_transient_blip_on_the_terminal_write_is_retried_then_lands(
        self, monkeypatch, fake_db
    ):
        # The pipeline finished, so the one write that records `succeeded` must
        # not be lost to a momentary StorageUnavailable: a lost terminal write
        # leaves the row `running` for the reaper to re-execute. The first
        # set_run_result raises, the retry lands, and the run ends `succeeded`.
        monkeypatch.setattr(api_index, "TERMINAL_WRITE_RETRY_SECONDS", 0)
        _fake_run(monkeypatch)
        run = _seed_run(fake_db)
        fake_db.transient_failures["set_run_result"] = 1

        api_index._execute_run(run)

        assert fake_db.runs[SEEDED_RUN_ID]["status"] == "succeeded"
        # One rejected attempt plus the one that landed.
        set_result_names = [call[0] for call in fake_db.set_result_calls]
        assert set_result_names.count(SEEDED_RUN_ID) == 2

    def test_a_terminal_write_that_never_lands_is_logged_and_not_raised(
        self, monkeypatch, fake_db, caplog
    ):
        # Every retry fails. _execute_run must not raise (that would 500 the
        # worker route and read as "retry this finished run"); it logs loudly and
        # returns, leaving the row as the reaper's residual case, now bounded by
        # the attempts cap.
        monkeypatch.setattr(api_index, "TERMINAL_WRITE_RETRY_SECONDS", 0)
        _fake_run(monkeypatch)
        run = _seed_run(fake_db)
        fake_db.unavailable.add("set_run_result")

        with caplog.at_level(logging.ERROR, logger=api_index.logger.name):
            api_index._execute_run(run)  # must not raise

        assert "terminal status" in caplog.text
        # It tried the full budget before giving up.
        assert len(fake_db.set_result_calls) == api_index.TERMINAL_WRITE_ATTEMPTS


class TestTheWorkerRoute:
    """POST /api/run-worker: the cron/pg_net entrypoint. Not require_user; gated on
    a shared CRON_SECRET, and reachable server-to-server (no Sec-Fetch-Site, no
    session), so it is deliberately exempt from _reject_cross_origin."""

    @staticmethod
    def _call(secret=WORKER_SECRET):
        return client.post("/api/run-worker", headers={"Authorization": "Bearer {}".format(secret)})

    def test_no_cron_secret_configured_fails_closed(self, monkeypatch, caplog):
        monkeypatch.delenv("CRON_SECRET", raising=False)
        with caplog.at_level(logging.ERROR, logger=api_index.logger.name):
            response = self._call("anything")
        assert response.status_code == 401
        assert "CRON_SECRET" in caplog.text

    def test_a_missing_bearer_is_401(self, monkeypatch):
        monkeypatch.setenv("CRON_SECRET", WORKER_SECRET)
        assert client.post("/api/run-worker").status_code == 401

    def test_a_wrong_secret_is_401(self, monkeypatch):
        monkeypatch.setenv("CRON_SECRET", WORKER_SECRET)
        assert self._call("not-the-secret").status_code == 401

    def test_the_right_secret_with_an_empty_queue_is_a_noop(self, monkeypatch, fake_db):
        monkeypatch.setenv("CRON_SECRET", WORKER_SECRET)
        response = self._call()
        assert response.status_code == 200
        assert response.json() == {"ran": None}

    def test_the_right_secret_runs_one_claimed_run(self, monkeypatch, fake_db):
        monkeypatch.setenv("CRON_SECRET", WORKER_SECRET)
        _fake_run(monkeypatch)
        _seed_run(fake_db)

        response = self._call()
        assert response.status_code == 200
        assert response.json() == {"ran": SEEDED_RUN_ID}
        assert fake_db.runs[SEEDED_RUN_ID]["status"] == "succeeded"

    def test_it_is_not_blocked_by_reject_cross_origin(self, monkeypatch, fake_db):
        # pg_net sends no Sec-Fetch-Site and does not carry a browser origin; the
        # worker is exempt from _reject_cross_origin entirely (CRON_SECRET is its
        # only gate), so a call with none of those headers still reaches the claim.
        monkeypatch.setenv("CRON_SECRET", WORKER_SECRET)
        response = self._call()
        assert response.status_code == 200  # not 403 or 415

    def test_a_stale_running_run_is_reclaimed(self, monkeypatch, fake_db):
        # A worker that died mid-run leaves a `running` row with an old heartbeat;
        # the reaper inside claim_next_run picks it up on the next tick.
        monkeypatch.setenv("CRON_SECRET", WORKER_SECRET)
        _fake_run(monkeypatch)
        _seed_run(fake_db)
        fake_db.runs[SEEDED_RUN_ID]["status"] = "running"
        fake_db.runs[SEEDED_RUN_ID]["heartbeat_at"] = time.time() - (
            fake_db.STALE_HEARTBEAT_SECONDS + 10
        )

        response = self._call()
        assert response.json() == {"ran": SEEDED_RUN_ID}
        assert fake_db.runs[SEEDED_RUN_ID]["status"] == "succeeded"

    def test_a_healthy_running_run_is_not_reclaimed(self, monkeypatch, fake_db):
        monkeypatch.setenv("CRON_SECRET", WORKER_SECRET)
        _fake_run(monkeypatch)
        _seed_run(fake_db)
        fake_db.runs[SEEDED_RUN_ID]["status"] = "running"
        fake_db.runs[SEEDED_RUN_ID]["heartbeat_at"] = time.time()

        assert self._call().json() == {"ran": None}

    def test_the_route_sweeps_an_exhausted_run_to_failed(self, monkeypatch, fake_db):
        # A `running` row retried up to the cap with a dead heartbeat is ended by
        # the sweep the route runs before it claims, not reclaimed a further time.
        monkeypatch.setenv("CRON_SECRET", WORKER_SECRET)
        _seed_run(fake_db)
        row = fake_db.runs[SEEDED_RUN_ID]
        row["status"] = "running"
        row["attempts"] = fake_db.MAX_RUN_ATTEMPTS
        row["heartbeat_at"] = time.time() - (fake_db.STALE_HEARTBEAT_SECONDS + 10)

        assert self._call().json() == {"ran": None}
        assert row["status"] == "failed"
        assert row["error"] == db_module.RUN_EXHAUSTED_MESSAGE

    def test_the_route_sweeps_a_queued_run_past_the_ttl_to_failed(self, monkeypatch, fake_db):
        # A run no worker ever claimed (the trigger path is down) is not left
        # queued forever for the browser to poll; the sweep fails it.
        monkeypatch.setenv("CRON_SECRET", WORKER_SECRET)
        _seed_run(fake_db)
        row = fake_db.runs[SEEDED_RUN_ID]
        row["created_at"] = time.time() - (fake_db.QUEUED_TTL_SECONDS + 10)

        assert self._call().json() == {"ran": None}
        assert row["status"] == "failed"
        assert row["error"] == db_module.RUN_NOT_STARTED_MESSAGE


class TestTheRetryCapAndSweep:
    """The attempts cap and the stalled-run sweep, exercised through FakeDb the
    same way db.py enforces them: a run is claimed at most MAX_RUN_ATTEMPTS times,
    a healthy run is never reaped whatever its attempts, and both give-up cases
    (exhaustion, never-started) end in a `failed` row carrying the right
    sentence."""

    def test_a_run_is_claimed_at_most_max_attempts_times(self, fake_db):
        _seed_run(fake_db)
        # Each claim is one attempt; the worker then dies mid-run, leaving the row
        # `running` with a heartbeat old enough for the reaper to consider it.
        for _ in range(fake_db.MAX_RUN_ATTEMPTS):
            claimed = fake_db.claim_next_run()
            assert claimed is not None
            fake_db.runs[SEEDED_RUN_ID]["heartbeat_at"] = time.time() - (
                fake_db.STALE_HEARTBEAT_SECONDS + 10
            )
        assert fake_db.runs[SEEDED_RUN_ID]["attempts"] == fake_db.MAX_RUN_ATTEMPTS
        # The cap is reached: the reaper refuses to claim it a further time.
        assert fake_db.claim_next_run() is None

    def test_exhaustion_moves_the_run_to_failed_with_the_message(self, fake_db):
        _seed_run(fake_db)
        row = fake_db.runs[SEEDED_RUN_ID]
        row["status"] = "running"
        row["attempts"] = fake_db.MAX_RUN_ATTEMPTS
        row["heartbeat_at"] = time.time() - (fake_db.STALE_HEARTBEAT_SECONDS + 10)

        assert fake_db.fail_stalled_runs() == 1
        assert row["status"] == "failed"
        assert row["error"] == db_module.RUN_EXHAUSTED_MESSAGE

    def test_a_healthy_run_is_never_reaped_regardless_of_attempts(self, fake_db):
        _seed_run(fake_db)
        row = fake_db.runs[SEEDED_RUN_ID]
        row["status"] = "running"
        row["attempts"] = fake_db.MAX_RUN_ATTEMPTS  # even sitting at the cap
        row["heartbeat_at"] = time.time()  # a fresh heartbeat means it is alive

        # Neither the reaper nor the exhaustion sweep touches a live run.
        assert fake_db.claim_next_run() is None
        assert fake_db.fail_stalled_runs() == 0
        assert row["status"] == "running"

    def test_a_queued_run_past_the_ttl_is_failed_with_the_not_started_message(self, fake_db):
        _seed_run(fake_db)
        row = fake_db.runs[SEEDED_RUN_ID]
        row["created_at"] = time.time() - (fake_db.QUEUED_TTL_SECONDS + 10)

        assert fake_db.fail_stalled_runs() == 1
        assert row["status"] == "failed"
        assert row["error"] == db_module.RUN_NOT_STARTED_MESSAGE

    def test_a_fresh_queued_run_is_left_alone(self, fake_db):
        _seed_run(fake_db)  # created just now, well within QUEUED_TTL
        assert fake_db.fail_stalled_runs() == 0
        assert fake_db.runs[SEEDED_RUN_ID]["status"] == "queued"


class TestRunStatusEndpoint:
    """GET /api/runs/{run_id}: the browser's poll. Private to its owner (a 404 for
    anyone else), shape-checked, and never cached."""

    def test_the_owner_sees_status_progress_report_id_and_error(self, fake_db):
        _seed_run(fake_db)
        fake_db.runs[SEEDED_RUN_ID]["status"] = "running"
        fake_db.runs[SEEDED_RUN_ID]["progress"] = ["Finding communities..."]

        response = client.get("/api/runs/{}".format(SEEDED_RUN_ID))
        assert response.status_code == 200
        assert response.json() == {
            "status": "running",
            "progress": ["Finding communities..."],
            "report_id": None,
            "error": None,
        }
        assert response.headers["cache-control"] == "no-store"

    def test_a_run_owned_by_someone_else_is_a_404(self, fake_db):
        _seed_run(fake_db, user_id="someone-else")
        response = client.get("/api/runs/{}".format(SEEDED_RUN_ID))
        assert response.status_code == 404
        assert response.json() == {"message": api_index.RUN_NOT_FOUND_MESSAGE}

    def test_an_unknown_run_is_a_404(self, fake_db):
        response = client.get("/api/runs/doesNotExist01")
        assert response.status_code == 404
        assert response.json() == {"message": api_index.RUN_NOT_FOUND_MESSAGE}

    @pytest.mark.parametrize("bad_id", ["short", "plus+sign", "a" * 65, "dots.are.out"])
    def test_a_malformed_id_is_refused_before_the_database_is_touched(self, bad_id, fake_db):
        response = client.get("/api/runs/{}".format(bad_id))
        assert response.status_code == 404
        assert fake_db.get_run_calls == []

    def test_storage_being_down_is_a_503(self, fake_db):
        fake_db.unavailable.add("get_run")
        response = client.get("/api/runs/{}".format(SEEDED_RUN_ID))
        assert response.status_code == 503
        assert response.json()["message"] == db_module.UNAVAILABLE_MESSAGE


class TestRunListEndpoint:
    """GET /api/runs: the caller's own runs, and only theirs."""

    def test_it_lists_only_the_callers_runs_newest_first(self, fake_db, fake_auth):
        fake_auth.user = api_index.AuthenticatedUser("owner-42", "")
        fake_db.create_run("mineOldest01", "owner-42", "first", "", _default_inputs())
        fake_db.create_run("mineNewest02", "owner-42", "second", "", _default_inputs())
        fake_db.create_run("theirsRun_003", "someone-else", "third", "", _default_inputs())

        response = client.get("/api/runs")
        assert response.status_code == 200
        ids = [row["id"] for row in response.json()["runs"]]
        assert ids == ["mineNewest02", "mineOldest01"]
        assert response.headers["cache-control"] == "no-store"

    def test_storage_being_down_is_a_503(self, fake_db):
        fake_db.unavailable.add("list_runs")
        response = client.get("/api/runs")
        assert response.status_code == 503
        assert response.json()["message"] == db_module.UNAVAILABLE_MESSAGE


class TestCancelEndpoint:
    """POST /api/runs/{run_id}/cancel: owner-checked, and the flip a worker observes."""

    def test_the_owner_can_flip_a_queued_run_to_cancelling(self, fake_db):
        _seed_run(fake_db)
        response = client.post("/api/runs/{}/cancel".format(SEEDED_RUN_ID))
        assert response.status_code == 200
        assert response.json() == {"status": "cancelling"}
        assert fake_db.runs[SEEDED_RUN_ID]["status"] == "cancelling"

    def test_a_run_owned_by_someone_else_cannot_be_cancelled(self, fake_db):
        _seed_run(fake_db, user_id="someone-else")
        response = client.post("/api/runs/{}/cancel".format(SEEDED_RUN_ID))
        assert response.status_code == 404
        assert response.json() == {"message": api_index.RUN_NOT_FOUND_MESSAGE}
        assert fake_db.runs[SEEDED_RUN_ID]["status"] == "queued"

    def test_an_unknown_run_is_a_404(self, fake_db):
        response = client.post("/api/runs/doesNotExist01/cancel")
        assert response.status_code == 404

    @pytest.mark.parametrize("bad_id", ["short", "plus+sign", "a" * 65])
    def test_a_malformed_id_is_a_404(self, bad_id):
        assert client.post("/api/runs/{}/cancel".format(bad_id)).status_code == 404

    def test_a_worker_observes_the_flag_after_the_cancel(self, monkeypatch, fake_db):
        # The endpoint flips the row; is_cancelling then reads True, which is what
        # the worker's poller watches for.
        _seed_run(fake_db)
        assert fake_db.is_cancelling(SEEDED_RUN_ID) is False
        client.post("/api/runs/{}/cancel".format(SEEDED_RUN_ID))
        assert fake_db.is_cancelling(SEEDED_RUN_ID) is True


class TestGetSavedReport:
    def test_a_stored_report_comes_back_with_its_drafts(self, fake_db):
        fake_db.reports["abc123XYZ_-def"] = {"bundle_version": 4, "inputs": {"query": "x"}}
        fake_db.drafts[("abc123XYZ_-def", 2)] = "The edited post."

        response = client.get("/api/reports/abc123XYZ_-def")

        assert response.status_code == 200
        assert response.json() == {
            "bundle": {"bundle_version": 4, "inputs": {"query": "x"}},
            "drafts": {"2": "The edited post."},
        }
        # A share link is the only credential a saved report has, so nothing in
        # front of this function may keep a copy.
        assert response.headers["cache-control"] == "no-store"

    def test_a_report_with_no_drafts_returns_an_empty_map_not_null(self, fake_db):
        fake_db.reports["nodrafts0000"] = {"bundle_version": 4}
        assert client.get("/api/reports/nodrafts0000").json()["drafts"] == {}

    def test_an_unknown_id_is_a_friendly_404(self):
        response = client.get("/api/reports/doesnotexist0")
        assert response.status_code == 404
        assert response.json() == {"message": api_index.REPORT_NOT_FOUND_MESSAGE}

    @pytest.mark.parametrize(
        "bad_id",
        # A slash is not in this list because a path with one is a different
        # route entirely and never reaches this handler.
        ["short", "has spaces here", "plus+sign", "a" * 65, "dots.are.out", "semi;colon"],
    )
    def test_a_malformed_id_is_refused_before_the_database_is_touched(self, bad_id, fake_db):
        response = client.get("/api/reports/{}".format(bad_id))
        assert response.status_code == 404
        assert response.json() == {"message": api_index.REPORT_NOT_FOUND_MESSAGE}
        assert fake_db.get_report_calls == []

    def test_storage_being_down_is_a_503_with_a_sentence_not_a_404(self, fake_db):
        fake_db.unavailable.add("get_report")
        response = client.get("/api/reports/abc123XYZ_-def")
        assert response.status_code == 503
        assert response.json()["message"] == db_module.UNAVAILABLE_MESSAGE
        assert response.headers["cache-control"] == "no-store"

    def test_an_unexpected_storage_failure_leaks_nothing(self, fake_db):
        fake_db.boom.add("get_report")
        response = client.get("/api/reports/abc123XYZ_-def")
        assert response.status_code == 503
        assert "hunter2" not in response.text
        assert response.json() == {"message": db_module.UNAVAILABLE_MESSAGE}


class TestSaveDraftEndpoint:
    VALID = {"report_id": "abc123XYZ_-def", "idea_index": 3, "post": "An edited post."}

    def test_a_valid_body_is_upserted(self, fake_db):
        response = client.post("/api/drafts", json=self.VALID)
        assert response.status_code == 200
        assert response.json() == {"saved": True}
        assert fake_db.upsert_calls == [("abc123XYZ_-def", 3, "An edited post.")]

    def test_saving_twice_replaces_the_stored_post(self, fake_db):
        client.post("/api/drafts", json=self.VALID)
        client.post("/api/drafts", json={**self.VALID, "post": "The second version."})
        assert fake_db.drafts[("abc123XYZ_-def", 3)] == "The second version."

    def test_idea_index_zero_is_valid(self, fake_db):
        response = client.post("/api/drafts", json={**self.VALID, "idea_index": 0})
        assert response.status_code == 200
        assert fake_db.upsert_calls[0][1] == 0

    @pytest.mark.parametrize(
        "body",
        [
            {"idea_index": 1, "post": "x"},
            {"report_id": "not a valid id", "idea_index": 1, "post": "x"},
            {"report_id": "abc123XYZ_-def", "post": "x"},
            {"report_id": "abc123XYZ_-def", "idea_index": "3", "post": "x"},
            {"report_id": "abc123XYZ_-def", "idea_index": True, "post": "x"},
            {"report_id": "abc123XYZ_-def", "idea_index": -1, "post": "x"},
            {"report_id": "abc123XYZ_-def", "idea_index": 10000, "post": "x"},
            {"report_id": "abc123XYZ_-def", "idea_index": 1},
            {"report_id": "abc123XYZ_-def", "idea_index": 1, "post": "   "},
            {"report_id": "abc123XYZ_-def", "idea_index": 1, "post": 12},
        ],
    )
    def test_malformed_bodies_are_422_and_never_reach_storage(self, body, fake_db):
        response = client.post("/api/drafts", json=body)
        assert response.status_code == 422
        assert response.json()["message"].endswith(".")
        assert fake_db.upsert_calls == []

    def test_a_body_that_is_not_json_is_a_422(self, fake_db):
        response = client.post(
            "/api/drafts", content="not json", headers={"content-type": "application/json"}
        )
        assert response.status_code == 422
        assert fake_db.upsert_calls == []

    def test_an_oversized_post_is_refused_rather_than_silently_truncated(self, fake_db):
        long_post = "x" * (api_index.DRAFT_POST_MAX_CHARS + 1)
        response = client.post("/api/drafts", json={**self.VALID, "post": long_post})
        assert response.status_code == 413
        assert fake_db.upsert_calls == []

    def test_a_post_right_at_the_cap_is_accepted(self, fake_db):
        at_cap = "x" * api_index.DRAFT_POST_MAX_CHARS
        response = client.post("/api/drafts", json={**self.VALID, "post": at_cap})
        assert response.status_code == 200
        assert len(fake_db.upsert_calls[0][2]) == api_index.DRAFT_POST_MAX_CHARS

    def test_storage_being_down_is_a_503_the_client_can_see(self, fake_db):
        # The one draft path that reports failure: a reader who edited a post
        # must not be told it saved when it did not.
        fake_db.unavailable.add("upsert_draft")
        response = client.post("/api/drafts", json=self.VALID)
        assert response.status_code == 503
        assert response.json()["message"] == db_module.UNAVAILABLE_MESSAGE

    def test_the_save_bucket_has_its_own_limit(self, monkeypatch):
        monkeypatch.setattr(api_index, "DRAFT_SAVES_PER_HOUR", 1)
        assert client.post("/api/drafts", json=self.VALID).status_code == 200
        limited = client.post("/api/drafts", json=self.VALID)
        assert limited.status_code == 429
        assert "draft saves" in limited.json()["message"]

    def test_a_form_content_type_is_refused(self, fake_db):
        response = client.post("/api/drafts", content="a=1", headers={"content-type": "text/plain"})
        assert response.status_code == 415
        assert fake_db.upsert_calls == []


class TestGeneratedDraftsArePersisted:
    """POST /api/draft writes the post it just paid for, when it is told where."""

    BODY = dict(TestDraftEndpoint.VALID_DRAFT_BODY)

    @staticmethod
    def _patch_draft(monkeypatch, post="A generated post."):
        monkeypatch.setattr(
            api_index.analysis, "draft_post", lambda idea, evidence, query, profile: {"post": post}
        )

    def test_a_generated_draft_is_saved_when_the_request_names_a_report(
        self, monkeypatch, fake_db
    ):
        self._patch_draft(monkeypatch)
        response = client.post(
            "/api/draft", json={**self.BODY, "report_id": "abc123XYZ_-def", "idea_index": 4}
        )
        assert response.status_code == 200
        assert fake_db.upsert_calls == [("abc123XYZ_-def", 4, "A generated post.")]

    def test_nothing_is_saved_when_the_request_names_no_report(self, monkeypatch, fake_db):
        self._patch_draft(monkeypatch)
        assert client.post("/api/draft", json=self.BODY).status_code == 200
        assert fake_db.upsert_calls == []

    def test_a_malformed_target_is_ignored_rather_than_refusing_the_draft(
        self, monkeypatch, fake_db
    ):
        self._patch_draft(monkeypatch)
        response = client.post(
            "/api/draft", json={**self.BODY, "report_id": "no good", "idea_index": 4}
        )
        assert response.status_code == 200
        assert response.json() == {"post": "A generated post."}
        assert fake_db.upsert_calls == []

    def test_a_storage_failure_never_costs_the_reader_the_post(self, monkeypatch, fake_db):
        fake_db.unavailable.add("upsert_draft")
        self._patch_draft(monkeypatch)
        response = client.post(
            "/api/draft", json={**self.BODY, "report_id": "abc123XYZ_-def", "idea_index": 0}
        )
        assert response.status_code == 200
        assert response.json() == {"post": "A generated post."}


# ── Authentication ──────────────────────────────────────────────────────────────
# Everything above this line runs as the fixed signed-in user conftest.py's
# `fake_auth` fixture stands in. These tests are the ones that take it away, so
# they exercise api/index.py's real token verification: the fixture hands the
# request back to the genuine implementation when `enabled` is False.

TEST_ISSUER_URL = "https://project-under-test.supabase.co"

# The app verifies only asymmetric tokens (ES256), against a public key it fetches
# from the project's JWKS. A test cannot reach the real JWKS, so it generates its
# own EC keypair, signs tokens with the private half, and stubs the JWKS client to
# hand back the public half. This is the only branch the live path has now that
# the HS256 shared-secret path is gone, so it is the branch every auth test below
# exercises through the `real_auth` fixture.
_TEST_EC_PRIVATE_KEY = ec.generate_private_key(ec.SECP256R1())
_TEST_EC_PUBLIC_KEY = _TEST_EC_PRIVATE_KEY.public_key()


class _StubSigningKey:
    key = _TEST_EC_PUBLIC_KEY


class _StubJwkClient:
    """Stands in for jwt.PyJWKClient: returns the test public key, no network."""

    def get_signing_key_from_jwt(self, token):
        return _StubSigningKey()

# Every route this API exposes, as (method, path, kwargs). Written out rather
# than read off app.routes so that adding a route without a session check fails
# here loudly instead of quietly inheriting an empty list.
ALL_ROUTES = [
    ("post", "/api/report", {"json": VALID_BODY}),
    ("get", "/api/reports/abc123XYZ_-def", {}),
    ("get", "/api/sample", {}),
    ("get", "/api/sample/available", {}),
    ("post", "/api/draft", {"json": {"idea": {"title": "t"}, "evidence": []}}),
    ("post", "/api/drafts", {"json": {"report_id": "abc123XYZ_-def", "idea_index": 0, "post": "p"}}),
    ("post", "/api/export/markdown", {"json": {"bundle": {"bundle_version": 4}}}),
    ("get", "/api/runs", {}),
    ("get", "/api/runs/abc123XYZ_-def", {}),
    ("post", "/api/runs/abc123XYZ_-def/cancel", {}),
]
ROUTE_IDS = ["{} {}".format(method.upper(), path) for method, path, _ in ALL_ROUTES]


def _signed_token(**overrides):
    """One ES256 access token shaped like the ones a Supabase Google session
    carries: an `authenticated` role, a `google` provider, and a real `sub`.

    Signed with the test EC private key so the stubbed JWKS client (which returns
    the matching public key) verifies it. Any claim can be overridden, and a
    passed value of None drops that claim entirely, which is how the tests build a
    token that is missing `exp`, `sub`, or a provider.
    """
    claims = {
        "sub": "11111111-2222-3333-4444-555555555555",
        "aud": "authenticated",
        "iss": TEST_ISSUER_URL + "/auth/v1",
        "email": "reader@example.com",
        "role": "authenticated",
        "app_metadata": {"provider": "google", "providers": ["google"]},
        "is_anonymous": False,
        "exp": int(time.time()) + 3600,
    }
    claims.update(overrides)
    claims = {name: value for name, value in claims.items() if value is not None}
    return jwt.encode(claims, _TEST_EC_PRIVATE_KEY, algorithm="ES256")


@pytest.fixture
def real_auth(fake_auth, monkeypatch):
    """Turn the fake user off and verify against the test EC keypair.

    Points SUPABASE_URL at the test project and stubs the JWKS client so the
    asymmetric branch resolves to the test public key instead of a network fetch.
    """
    monkeypatch.setenv("SUPABASE_URL", TEST_ISSUER_URL)
    monkeypatch.setattr(api_index, "_jwks_client_for", lambda base_url: _StubJwkClient())
    fake_auth.enabled = False
    return fake_auth


def _bearer(token):
    return {"Authorization": "Bearer {}".format(token)}


# The API routes that are allowed to be reachable without a session. The worker
# entrypoint is the one: pg_net cannot hold a Supabase session, so it carries a
# CRON_SECRET instead of require_user, and it is listed here so that exemption is
# deliberate. A route that should be public is added here on purpose, which is the
# point: the introspective test below fails for any /api/* route that carries
# neither the auth dependency nor a place here.
PUBLIC_API_ROUTES: set = {"/api/run-worker"}


class TestEveryApiRouteCarriesTheAuthDependency:
    """Introspective rather than hand-maintained: it reads app.routes and asserts
    every /api/* route (bar an explicit public set) carries `require_user`, so a
    new route added without the dependency fails CI instead of shipping open."""

    def test_no_api_route_is_unguarded(self):
        from api.index import app as real_app
        from api.index import require_user

        unguarded = []
        for route in real_app.routes:
            path = getattr(route, "path", "")
            if not path.startswith("/api/") or path in PUBLIC_API_ROUTES:
                continue
            dependant = getattr(route, "dependant", None)
            calls = [dep.call for dep in dependant.dependencies] if dependant else []
            if require_user not in calls:
                unguarded.append(path)
        assert unguarded == [], "unguarded /api routes: {}".format(unguarded)

    def test_the_hand_written_route_list_covers_every_api_route(self):
        # ALL_ROUTES (used by the negative-auth parametrized tests) is maintained
        # by hand with example paths, so it cannot be compared to the templated
        # routes by string; counting keeps it honest that a newly added route was
        # given a negative-auth case too.
        from api.index import app as real_app

        actual = {
            getattr(route, "path", "")
            for route in real_app.routes
            if getattr(route, "path", "").startswith("/api/")
            and getattr(route, "path", "") not in PUBLIC_API_ROUTES
        }
        assert len(ALL_ROUTES) == len(actual)


class TestEveryRouteRequiresASession:
    @pytest.mark.parametrize("method,path,kwargs", ALL_ROUTES, ids=ROUTE_IDS)
    def test_no_token_is_a_401(self, real_auth, method, path, kwargs):
        response = getattr(client, method)(path, **kwargs)
        assert response.status_code == 401

    @pytest.mark.parametrize("method,path,kwargs", ALL_ROUTES, ids=ROUTE_IDS)
    def test_a_garbage_token_is_a_401(self, real_auth, method, path, kwargs):
        response = getattr(client, method)(path, headers=_bearer("not-a-jwt"), **kwargs)
        assert response.status_code == 401

    @pytest.mark.parametrize("method,path,kwargs", ALL_ROUTES, ids=ROUTE_IDS)
    def test_an_expired_token_is_a_401(self, real_auth, method, path, kwargs):
        expired = _signed_token(exp=int(time.time()) - 60)
        response = getattr(client, method)(path, headers=_bearer(expired), **kwargs)
        assert response.status_code == 401

    def test_a_run_is_refused_before_the_engine_is_ever_called(self, real_auth, monkeypatch):
        # The check happens in the dependency, before the handler queues anything,
        # so an unauthenticated request never even creates a run to be worked.
        def exploding_run_report(*args, **kwargs):
            raise AssertionError("the engine must not run for an unauthenticated request")

        monkeypatch.setattr(api_index.pipeline, "run_report", exploding_run_report)
        response = client.post("/api/report", json=VALID_BODY)
        assert response.status_code == 401

    def test_a_run_is_refused_before_anything_is_saved(self, real_auth, fake_db):
        client.post("/api/report", json=VALID_BODY)
        assert fake_db.saved_report_calls == []

    def test_a_saved_report_is_never_read_for_an_unauthenticated_caller(self, real_auth, fake_db):
        client.get("/api/reports/abc123XYZ_-def")
        assert fake_db.get_report_calls == []


class TestTheShapeOfA401:
    def test_it_is_a_message_not_a_detail(self, real_auth):
        # Every other error this API produces says "message", and the frontend
        # reads that one key. FastAPI's own HTTPException would say "detail".
        response = client.get("/api/sample")
        assert response.status_code == 401
        assert response.json() == {"message": api_index.AUTH_REQUIRED_MESSAGE}

    def test_it_advertises_bearer_and_is_never_cached(self, real_auth):
        response = client.get("/api/sample")
        assert response.headers["www-authenticate"] == "Bearer"
        assert response.headers["cache-control"] == "no-store"

    def test_the_sentence_says_nothing_about_why(self, real_auth, monkeypatch):
        # "expired", "wrong key" and "not a JWT" are one answer on purpose, and so
        # is a server-side misconfiguration: a caller must not be able to tell a
        # broken deployment apart from a stale token by reading the sentence.
        wrong_key = ec.generate_private_key(ec.SECP256R1())
        forged = _signed_token()  # a good token...
        forged = jwt.encode(
            jwt.decode(forged, options={"verify_signature": False}),
            wrong_key,
            algorithm="ES256",
        )  # ...re-signed with a key this project does not publish.
        reasons = [
            "not-a-jwt",
            _signed_token(exp=int(time.time()) - 60),
            forged,
        ]
        messages = {
            client.get("/api/sample", headers=_bearer(token)).json()["message"]
            for token in reasons
        }
        # And with SUPABASE_URL gone, the server-side failure answers the same way.
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.delenv("NEXT_PUBLIC_SUPABASE_URL", raising=False)
        messages.add(client.get("/api/sample", headers=_bearer("anything")).json()["message"])
        assert messages == {api_index.AUTH_REQUIRED_MESSAGE}


class TestAVerifiedTokenIsHonoured:
    def test_a_valid_token_reaches_the_route(self, real_auth):
        response = client.get("/api/sample/available", headers=_bearer(_signed_token()))
        assert response.status_code == 200
        assert response.json() == {"available": True}

    def test_the_sub_and_email_come_off_the_claims(self, real_auth):
        user = api_index.verify_bearer_token(_signed_token())
        assert user.user_id == "11111111-2222-3333-4444-555555555555"
        assert user.email == "reader@example.com"

    def test_a_token_with_no_email_claim_is_still_valid(self, real_auth):
        user = api_index.verify_bearer_token(_signed_token(sub="abc", email=None))
        assert user == api_index.AuthenticatedUser("abc", "")

    def test_the_email_falls_back_to_user_metadata(self, real_auth):
        token = _signed_token(email=None, user_metadata={"email": "nested@example.com"})
        assert api_index.verify_bearer_token(token).email == "nested@example.com"

    def test_a_scheme_other_than_bearer_is_refused(self, real_auth):
        response = client.get(
            "/api/sample", headers={"Authorization": "Basic {}".format(_signed_token())}
        )
        assert response.status_code == 401

    def test_the_scheme_is_matched_case_insensitively(self, real_auth):
        response = client.get(
            "/api/sample/available",
            headers={"Authorization": "bearer {}".format(_signed_token())},
        )
        assert response.status_code == 200


class TestWhatVerificationRefuses:
    def test_a_token_for_a_different_project_is_refused(self, real_auth):
        other = _signed_token(iss="https://someone-elses-project.supabase.co/auth/v1")
        with pytest.raises(api_index.Unauthorized):
            api_index.verify_bearer_token(other)

    def test_a_token_for_the_wrong_audience_is_refused(self, real_auth):
        with pytest.raises(api_index.Unauthorized):
            api_index.verify_bearer_token(_signed_token(aud="anon"))

    def test_a_token_signed_with_the_wrong_key_is_refused(self, real_auth):
        # Correctly shaped and ES256, but signed with an EC key this project does
        # not publish, so the signature check against the JWKS public key fails.
        wrong_key = ec.generate_private_key(ec.SECP256R1())
        forged = jwt.encode(
            {
                "sub": "abc",
                "aud": "authenticated",
                "iss": TEST_ISSUER_URL + "/auth/v1",
                "role": "authenticated",
                "exp": int(time.time()) + 60,
            },
            wrong_key,
            algorithm="ES256",
        )
        with pytest.raises(api_index.Unauthorized):
            api_index.verify_bearer_token(forged)

    def test_an_unsigned_token_is_refused(self, real_auth):
        # `alg: none` is the classic one. Only asymmetric algorithms are ever
        # tried, so this never reaches a decode that could accept it.
        unsigned = jwt.encode(
            {"sub": "abc", "aud": "authenticated", "exp": int(time.time()) + 60},
            key="",
            algorithm="none",
        )
        with pytest.raises(api_index.Unauthorized):
            api_index.verify_bearer_token(unsigned)

    def test_a_token_with_no_expiry_is_refused(self, real_auth):
        with pytest.raises(api_index.Unauthorized):
            api_index.verify_bearer_token(_signed_token(exp=None))

    def test_a_token_with_no_subject_is_refused(self, real_auth):
        with pytest.raises(api_index.Unauthorized):
            api_index.verify_bearer_token(_signed_token(sub=None))


class TestHs256IsNoLongerAccepted:
    """The legacy HS256 shared-secret path is gone: a validly HS256-signed token,
    however well-formed, is refused because HS256 is not an accepted algorithm."""

    def test_an_hs256_token_is_refused_over_the_wire(self, real_auth):
        token = jwt.encode(
            {
                "sub": "abc",
                "aud": "authenticated",
                "iss": TEST_ISSUER_URL + "/auth/v1",
                "role": "authenticated",
                "app_metadata": {"provider": "google"},
                "exp": int(time.time()) + 60,
            },
            "any-secret-at-all",
            algorithm="HS256",
        )
        response = client.get("/api/sample", headers=_bearer(token))
        assert response.status_code == 401

    def test_verify_bearer_token_refuses_hs256_directly(self, real_auth):
        token = jwt.encode({"sub": "abc", "exp": int(time.time()) + 60}, "s", algorithm="HS256")
        with pytest.raises(api_index.Unauthorized):
            api_index.verify_bearer_token(token)


class TestTheAuthorizationRule:
    """`authorization_reason` is pure: it takes claims and the configured
    allowlists and returns None (permitted) or a short reason (403). The env
    wrapper is exercised over the wire in the classes below."""

    GOOGLE = frozenset({"google"})
    NONE = frozenset()

    @staticmethod
    def _claims(**overrides):
        claims = {
            "role": "authenticated",
            "app_metadata": {"provider": "google", "providers": ["google"]},
            "email": "reader@example.com",
        }
        claims.update(overrides)
        return claims

    def _reason(self, claims, *, providers=GOOGLE, emails=NONE, domains=NONE):
        return api_index.authorization_reason(
            claims, allowed_providers=providers, allowed_emails=emails, allowed_domains=domains
        )

    def test_a_permitted_google_user_passes(self):
        assert self._reason(self._claims()) is None

    def test_an_anonymous_token_is_refused(self):
        assert self._reason(self._claims(is_anonymous=True)) == "anonymous"

    def test_a_non_authenticated_role_is_refused(self):
        assert self._reason(self._claims(role="anon")) == "role"

    def test_a_provider_off_the_list_is_refused(self):
        claims = self._claims(app_metadata={"provider": "email", "providers": ["email"]})
        assert self._reason(claims) == "provider"

    def test_widening_the_provider_set_admits_another_provider(self):
        claims = self._claims(app_metadata={"provider": "azure", "providers": ["azure"]})
        assert self._reason(claims, providers=frozenset({"google", "azure"})) is None

    def test_an_allowed_exact_email_passes(self):
        assert self._reason(self._claims(), emails=frozenset({"reader@example.com"})) is None

    def test_a_disallowed_email_is_refused(self):
        claims = self._claims(email="stranger@example.com")
        assert self._reason(claims, emails=frozenset({"reader@example.com"})) == "email"

    def test_an_allowed_domain_passes(self):
        assert self._reason(self._claims(), domains=frozenset({"example.com"})) is None

    def test_a_disallowed_domain_is_refused(self):
        claims = self._claims(email="reader@evil.example")
        assert self._reason(claims, domains=frozenset({"example.com"})) == "email"

    def test_with_neither_list_set_any_authenticated_email_is_allowed(self):
        # The shipped default: provider gate only, no email restriction.
        assert self._reason(self._claims(email="anyone@anywhere.example")) is None

    def test_the_email_claim_is_read_case_insensitively(self):
        claims = self._claims(email="Reader@Example.com")
        assert self._reason(claims, emails=frozenset({"reader@example.com"})) is None


class TestAuthorizationOverTheWire:
    """The env-driven wrapper: a validly signed but not-permitted token is a 403,
    distinct from the 401 an unverifiable token gets."""

    def test_an_anonymous_session_is_forbidden(self, real_auth):
        response = client.get("/api/sample", headers=_bearer(_signed_token(is_anonymous=True)))
        assert response.status_code == 403
        assert response.json() == {"message": api_index.NOT_PERMITTED_MESSAGE}

    def test_an_email_password_session_is_forbidden_by_default(self, real_auth):
        token = _signed_token(app_metadata={"provider": "email", "providers": ["email"]})
        response = client.get("/api/sample", headers=_bearer(token))
        assert response.status_code == 403
        assert response.json() == {"message": api_index.NOT_PERMITTED_MESSAGE}

    def test_a_403_omits_the_challenge_and_is_not_cached(self, real_auth):
        response = client.get("/api/sample", headers=_bearer(_signed_token(is_anonymous=True)))
        assert response.status_code == 403
        assert "www-authenticate" not in {k.lower() for k in response.headers}
        assert response.headers["cache-control"] == "no-store"

    def test_widening_the_provider_env_lets_another_provider_in(self, real_auth, monkeypatch):
        monkeypatch.setenv("AUTH_ALLOWED_PROVIDERS", "google,azure")
        token = _signed_token(app_metadata={"provider": "azure", "providers": ["azure"]})
        response = client.get("/api/sample/available", headers=_bearer(token))
        assert response.status_code == 200

    def test_an_email_off_the_env_allowlist_is_forbidden(self, real_auth, monkeypatch):
        monkeypatch.setenv("AUTH_ALLOWED_EMAILS", "allowed@example.com")
        # Provider is google (fine), but the email is not on the list.
        response = client.get("/api/sample", headers=_bearer(_signed_token(email="reader@example.com")))
        assert response.status_code == 403

    def test_an_email_on_the_env_allowlist_passes(self, real_auth, monkeypatch):
        monkeypatch.setenv("AUTH_ALLOWED_EMAILS", "reader@example.com")
        response = client.get("/api/sample/available", headers=_bearer(_signed_token()))
        assert response.status_code == 200


class TestAMisconfiguredDeployment:
    """A missing project URL or an unreachable JWKS is a server-side problem, and
    it must not resolve into "everyone is signed in". The caller sees the one
    ordinary 401 sentence (config state stays off the wire); the real reason goes
    to the log."""

    def test_no_project_url_refuses_every_token(self, fake_auth, monkeypatch, caplog):
        fake_auth.enabled = False
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.delenv("NEXT_PUBLIC_SUPABASE_URL", raising=False)
        with caplog.at_level(logging.ERROR, logger=api_index.logger.name):
            response = client.get("/api/sample", headers=_bearer(_signed_token()))
        assert response.status_code == 401
        # The same sentence a stale token gets, so the misconfiguration does not
        # announce itself to the caller (L1).
        assert response.json() == {"message": api_index.AUTH_REQUIRED_MESSAGE}
        assert "SUPABASE_URL" in caplog.text

    def test_an_unreachable_jwks_refuses_with_the_same_sentence(
        self, fake_auth, monkeypatch, caplog
    ):
        fake_auth.enabled = False
        monkeypatch.setenv("SUPABASE_URL", TEST_ISSUER_URL)

        def boom(base_url):
            raise RuntimeError("the JWKS endpoint is unreachable")

        monkeypatch.setattr(api_index, "_jwks_client_for", boom)
        with caplog.at_level(logging.ERROR, logger=api_index.logger.name):
            response = client.get("/api/sample", headers=_bearer(_signed_token()))
        assert response.status_code == 401
        assert response.json() == {"message": api_index.AUTH_REQUIRED_MESSAGE}
        assert "JWKS" in caplog.text


class TestTheJwksClientIsCached:
    def test_one_client_is_reused_for_the_same_project(self, monkeypatch):
        built = []

        class FakeJwkClient:
            def __init__(self, uri, **options):
                built.append(uri)

        monkeypatch.setattr(api_index, "_jwks_client", None)
        monkeypatch.setattr(api_index.jwt, "PyJWKClient", FakeJwkClient)

        first = api_index._jwks_client_for(TEST_ISSUER_URL)
        second = api_index._jwks_client_for(TEST_ISSUER_URL)

        assert first is second
        assert built == [TEST_ISSUER_URL + api_index.JWKS_PATH]

    def test_a_different_project_url_gets_its_own_client(self, monkeypatch):
        class FakeJwkClient:
            def __init__(self, uri, **options):
                self.uri = uri

        monkeypatch.setattr(api_index, "_jwks_client", None)
        monkeypatch.setattr(api_index.jwt, "PyJWKClient", FakeJwkClient)

        first = api_index._jwks_client_for(TEST_ISSUER_URL)
        second = api_index._jwks_client_for("https://another.supabase.co")
        assert first is not second


class TestTheAsymmetricBranchVerifiesAgainstTheJwks:
    """The one code path production actually uses: an ES256 token checked against
    a public key fetched from the project's JWKS. `real_auth` stubs
    `_jwks_client_for`, so this test goes one level lower and stubs the
    `jwt.PyJWKClient` it builds, exercising the real `_jwks_client_for` and the
    real asymmetric branch end to end with a generated EC keypair."""

    def test_a_real_es256_token_is_verified_through_a_stub_pyjwkclient(
        self, fake_auth, monkeypatch
    ):
        fake_auth.enabled = False
        monkeypatch.setenv("SUPABASE_URL", TEST_ISSUER_URL)
        monkeypatch.setattr(api_index, "_jwks_client", None)

        class FakePyJwkClient:
            def __init__(self, uri, **options):
                self.uri = uri

            def get_signing_key_from_jwt(self, token):
                return _StubSigningKey()

        monkeypatch.setattr(api_index.jwt, "PyJWKClient", FakePyJwkClient)

        user = api_index.verify_bearer_token(_signed_token(sub="ec-user"))
        assert user.user_id == "ec-user"
        assert user.email == "reader@example.com"


class TestRateLimitsFollowTheUser:
    @staticmethod
    def _request(headers=None, client_host="10.0.0.1"):
        raw = [(name.lower().encode(), value.encode()) for name, value in (headers or {}).items()]
        return api_index.Request(
            {"type": "http", "method": "GET", "path": "/", "headers": raw, "client": (client_host, 1)}
        )

    def test_the_signed_in_user_is_the_bucket(self):
        user = api_index.AuthenticatedUser("user-abc", "a@example.com")
        assert api_index._rate_limit_identity(self._request(), user) == "user:user-abc"

    def test_two_users_behind_one_address_do_not_share_a_bucket(self):
        one = api_index.AuthenticatedUser("user-one", "")
        two = api_index.AuthenticatedUser("user-two", "")
        request = self._request(headers={"x-forwarded-for": "1.1.1.1"})
        assert api_index._rate_limit_identity(request, one) != api_index._rate_limit_identity(
            request, two
        )

    def test_one_user_on_two_addresses_shares_a_bucket(self):
        user = api_index.AuthenticatedUser("user-one", "")
        first = self._request(headers={"x-forwarded-for": "1.1.1.1"})
        second = self._request(headers={"x-forwarded-for": "2.2.2.2"})
        assert api_index._rate_limit_identity(first, user) == api_index._rate_limit_identity(
            second, user
        )

    def test_the_address_is_the_floor_when_there_is_no_user(self):
        # The RIGHTMOST forwarded entry, which on Vercel is the platform-provided
        # client IP; the leftmost is caller-supplied and spoofable.
        request = self._request(headers={"x-forwarded-for": "9.9.9.9, 10.0.0.1"})
        assert api_index._rate_limit_identity(request, None) == "ip:10.0.0.1"

    def test_one_user_still_runs_out_of_report_budget(self, monkeypatch):
        monkeypatch.setattr(api_index, "REPORTS_PER_HOUR", 1)
        _fake_run(monkeypatch)
        assert client.post("/api/report", json=VALID_BODY).status_code == 202
        limited = client.post("/api/report", json=VALID_BODY)
        assert limited.status_code == 429
        assert "account" in limited.json()["message"]

    def test_a_second_user_has_their_own_report_budget(self, monkeypatch, fake_auth):
        monkeypatch.setattr(api_index, "REPORTS_PER_HOUR", 1)
        _fake_run(monkeypatch)
        fake_auth.user = api_index.AuthenticatedUser("user-one", "")
        assert client.post("/api/report", json=VALID_BODY).status_code == 202
        assert client.post("/api/report", json=VALID_BODY).status_code == 429
        fake_auth.user = api_index.AuthenticatedUser("user-two", "")
        assert client.post("/api/report", json=VALID_BODY).status_code == 202

    def test_the_draft_bucket_is_per_user_too(self, monkeypatch, fake_auth):
        monkeypatch.setattr(api_index, "DRAFTS_PER_HOUR", 1)
        monkeypatch.setattr(
            api_index.analysis, "draft_post", lambda idea, evidence, query, profile: {"post": "p"}
        )
        body = {"idea": {"title": "t"}, "evidence": [], "query": "q", "profile": ""}
        fake_auth.user = api_index.AuthenticatedUser("user-one", "")
        assert client.post("/api/draft", json=body).status_code == 200
        assert client.post("/api/draft", json=body).status_code == 429
        fake_auth.user = api_index.AuthenticatedUser("user-two", "")
        assert client.post("/api/draft", json=body).status_code == 200


class TestTheClientAddressIsTheVercelProvidedOne:
    """On Vercel, X-Forwarded-For is overwritten by the platform with the real
    client IP; the trustworthy value is the rightmost (platform-appended) one, not
    the leftmost, which a caller can set to anything."""

    @staticmethod
    def _request(headers=None, client_host="10.0.0.1"):
        raw = [(name.lower().encode(), value.encode()) for name, value in (headers or {}).items()]
        return api_index.Request(
            {"type": "http", "method": "GET", "path": "/", "headers": raw, "client": (client_host, 1)}
        )

    def test_the_rightmost_forwarded_entry_wins(self):
        request = self._request(headers={"x-forwarded-for": "1.1.1.1, 2.2.2.2, 3.3.3.3"})
        assert api_index._client_ip(request) == "3.3.3.3"

    def test_a_spoofed_leftmost_entry_is_ignored(self):
        # A caller prepends a fake address; the platform-appended real one is last.
        request = self._request(headers={"x-forwarded-for": "evil-spoof, 203.0.113.7"})
        assert api_index._client_ip(request) == "203.0.113.7"

    def test_x_real_ip_is_the_fallback(self):
        request = self._request(headers={"x-real-ip": "198.51.100.9"})
        assert api_index._client_ip(request) == "198.51.100.9"

    def test_the_socket_peer_is_the_last_resort(self):
        assert api_index._client_ip(self._request(client_host="192.0.2.5")) == "192.0.2.5"


class TestThePerIpCeiling:
    """Per-user budgets follow the person; a per-IP ceiling stops N accounts
    behind one address from multiplying cost without bound."""

    def test_accounts_behind_one_address_share_a_ceiling(self, monkeypatch, fake_auth):
        # Low per-user limit and a small multiple, so the ceiling is 1 * 2 = 2:
        # each of three fresh accounts is under its own budget, but the address
        # they share is not.
        monkeypatch.setattr(api_index, "REPORTS_PER_HOUR", 1)
        monkeypatch.setattr(api_index, "RATE_LIMIT_IP_CEILING_MULTIPLIER", 2)
        _fake_run(monkeypatch)

        fake_auth.user = api_index.AuthenticatedUser("user-one", "")
        assert client.post("/api/report", json=VALID_BODY).status_code == 202
        fake_auth.user = api_index.AuthenticatedUser("user-two", "")
        assert client.post("/api/report", json=VALID_BODY).status_code == 202
        # Third distinct account, its own budget untouched, but the address has
        # now spent the shared ceiling (2), so it is refused.
        fake_auth.user = api_index.AuthenticatedUser("user-three", "")
        blocked = client.post("/api/report", json=VALID_BODY)
        assert blocked.status_code == 429

    def test_the_ceiling_is_a_multiple_of_the_per_user_limit(self, monkeypatch):
        monkeypatch.setattr(api_index, "RATE_LIMIT_IP_CEILING_MULTIPLIER", 5)
        assert api_index._ip_ceiling(6) == 30
        # Never below the per-user limit, whatever the multiplier.
        monkeypatch.setattr(api_index, "RATE_LIMIT_IP_CEILING_MULTIPLIER", 0)
        assert api_index._ip_ceiling(6) == 6

    def test_one_user_alone_never_trips_the_ceiling_before_their_own_budget(
        self, monkeypatch, fake_auth
    ):
        # A single account uses its own budget, and the ceiling (a multiple of it)
        # is always the looser of the two for one person.
        monkeypatch.setattr(api_index, "REPORTS_PER_HOUR", 1)
        monkeypatch.setattr(api_index, "RATE_LIMIT_IP_CEILING_MULTIPLIER", 5)
        _fake_run(monkeypatch)
        fake_auth.user = api_index.AuthenticatedUser("solo", "")
        assert client.post("/api/report", json=VALID_BODY).status_code == 202
        limited = client.post("/api/report", json=VALID_BODY)
        assert limited.status_code == 429
        # Refused by the per-user budget, whose message names the account.
        assert "account" in limited.json()["message"]


class TestASavedReportRecordsWhoRanIt:
    """The save now happens in the worker (_execute_run), with the run's own owner,
    but the guarantees are unchanged: attribution is a column, never part of the
    bundle, and a share link is readable by anyone signed in."""

    def test_the_user_id_is_stored_with_the_report(self, monkeypatch, fake_db):
        _fake_run(monkeypatch)
        run = _seed_run(fake_db, user_id="owner-42")
        api_index._execute_run(run)
        assert fake_db.saved_report_users == ["owner-42"]

    def test_the_owner_is_never_written_into_the_stored_bundle(self, monkeypatch, fake_db):
        # Attribution is a column, not part of the report: a share link must not
        # tell whoever opens it who ran the report.
        _fake_run(monkeypatch, bundle={"bundle_version": 4})
        run = _seed_run(fake_db, user_id="owner-42")
        api_index._execute_run(run)
        _, _, _, bundle = fake_db.saved_report_calls[0]
        assert "user_id" not in bundle

    def test_anyone_signed_in_may_open_someone_elses_report(self, monkeypatch, fake_db, fake_auth):
        # The product decision behind share links: the id is the credential, and
        # a session is the only other requirement.
        _fake_run(monkeypatch, bundle={"bundle_version": 4})
        run = _seed_run(fake_db, user_id="owner-42")
        api_index._execute_run(run)
        report_id = fake_db.runs[SEEDED_RUN_ID]["report_id"]

        fake_auth.user = api_index.AuthenticatedUser("someone-else", "")
        response = client.get("/api/reports/{}".format(report_id))
        assert response.status_code == 200
        assert response.json()["bundle"]["bundle_version"] == 4


class TestTheAuthMigrationScript:
    """scripts/migrate_auth.py, without a database.

    The SQL is asserted as text on purpose: this script runs once, by hand,
    against the one production database, and there is no safe place to rehearse
    it. What can be checked here is that the statements are the shape they claim
    to be and that the claim is parameterised rather than interpolated.
    """

    @staticmethod
    def _script():
        import importlib.util
        import pathlib

        path = pathlib.Path(api_index._REPO_ROOT) / "scripts" / "migrate_auth.py"
        spec = importlib.util.spec_from_file_location("migrate_auth_under_test", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    class RecordingCursor:
        def __init__(self, rowcount=0, regclass="public.reports"):
            self.executed = []
            self.rowcount = rowcount
            # What `select to_regclass(...)` returns: a name when the table
            # exists, None when it does not.
            self._regclass = regclass

        def execute(self, sql, args=None):
            self.executed.append((sql, args))

        def fetchone(self):
            return (self._regclass,)

    def test_the_column_is_added_idempotently(self):
        script = self._script()
        cursor = self.RecordingCursor()
        script.add_column(cursor)

        sql, args = cursor.executed[0]
        normalised = " ".join(sql.split()).lower()
        assert normalised == "alter table public.reports add column if not exists user_id text"
        assert args is None

    def test_the_claim_only_touches_unowned_rows(self):
        script = self._script()
        cursor = self.RecordingCursor(rowcount=18)
        claimed = script.claim_unowned(cursor, "owner-42")

        sql, args = cursor.executed[0]
        normalised = " ".join(sql.split()).lower()
        assert normalised == "update public.reports set user_id = %s where user_id is null"
        # Parameterised, never interpolated: this runs against production once.
        assert args == ("owner-42",)
        assert "owner-42" not in sql
        assert claimed == 18

    def test_it_targets_both_app_tables(self):
        script = self._script()
        assert set(script.REVOKE_TABLES) == {"public.reports", "public.drafts"}

    def test_revoke_closes_the_browser_facing_roles(self):
        script = self._script()
        cursor = self.RecordingCursor(regclass="public.reports")
        touched = script.revoke_browser_role_grants(cursor)

        assert touched == ["public.reports", "public.drafts"]
        revokes = [
            " ".join(sql.split()).lower()
            for sql, _ in cursor.executed
            if sql.strip().lower().startswith("revoke")
        ]
        assert "revoke all privileges on table public.reports from anon, authenticated" in revokes
        assert "revoke all privileges on table public.drafts from anon, authenticated" in revokes

    def test_revoke_skips_a_table_that_is_not_present(self):
        script = self._script()
        cursor = self.RecordingCursor(regclass=None)
        touched = script.revoke_browser_role_grants(cursor)

        assert touched == []
        assert not any(sql.strip().lower().startswith("revoke") for sql, _ in cursor.executed)

    def test_revoke_leaves_rls_enablement_alone(self):
        # The pass revokes grants; it must not touch row-level security itself.
        script = self._script()
        cursor = self.RecordingCursor(regclass="public.reports")
        script.revoke_browser_role_grants(cursor)
        assert not any("row level security" in sql.lower() for sql, _ in cursor.executed)

    def test_the_column_is_nullable_so_existing_reports_survive_the_migration(self):
        script = self._script()
        assert "not null" not in " ".join(script.ADD_COLUMN_SQL.split()).lower()

    def test_it_refuses_to_run_without_a_database_url(self, monkeypatch, capsys):
        script = self._script()
        monkeypatch.setattr(script.db, "is_configured", lambda: False)
        monkeypatch.setattr(script.sys, "argv", ["migrate_auth.py"])
        assert script.main() == 1
        assert "POSTGRES_URL" in capsys.readouterr().err

    def test_an_empty_claim_is_refused_rather_than_claiming_nothing(self, monkeypatch, capsys):
        script = self._script()
        monkeypatch.setattr(script.sys, "argv", ["migrate_auth.py", "--claim", "  "])
        assert script.main() == 2
        assert "--claim" in capsys.readouterr().err

    def test_it_reuses_the_dsn_handling_db_already_has(self, monkeypatch):
        # Not its own copy of the `supa=` stripping and the sslmode default: the
        # one place that knows how to read POSTGRES_URL is db.py.
        script = self._script()
        monkeypatch.setenv(
            "POSTGRES_URL", "postgres://u:p@db.example.com:6543/postgres?supa=base-pooler.x"
        )
        dsn = script.db._dsn()
        assert "supa=" not in dsn
        assert dsn.endswith("sslmode=require")
