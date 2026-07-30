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

import pytest
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


def _lines(response):
    return [json.loads(line) for line in response.text.strip().split("\n") if line]


class TestReportStreamHappyPath:
    def test_valid_input_streams_progress_then_a_terminal_report_line(self, monkeypatch):
        captured = {}

        def fake_run_report(query, profile, progress=None, **advanced):
            captured["args"] = (query, profile)
            captured["advanced"] = advanced
            progress("Finding communities talking about this...")
            progress("Found r/testsub - fetching posts...")
            return {"bundle_version": 4, "inputs": {"query": query}, "sample": {}}

        monkeypatch.setattr(api_index.pipeline, "run_report", fake_run_report)

        response = client.post("/api/report", json=VALID_BODY)

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/x-ndjson")

        lines = _lines(response)
        assert lines[0] == {"type": "progress", "message": "Finding communities talking about this..."}
        assert lines[1] == {"type": "progress", "message": "Found r/testsub - fetching posts..."}
        assert len(lines) == 3
        assert lines[2]["type"] == "report"
        assert lines[2]["bundle"] == {
            "bundle_version": 4, "inputs": {"query": VALID_BODY["query"]}, "sample": {}
        }
        assert captured["args"] == (VALID_BODY["query"], VALID_BODY["profile"])

    def test_only_the_last_line_carries_the_bundle(self, monkeypatch):
        def fake_run_report(query, profile, progress=None, **advanced):
            for message in ("one", "two", "three"):
                progress(message)
            return {"bundle_version": 4}

        monkeypatch.setattr(api_index.pipeline, "run_report", fake_run_report)
        lines = _lines(client.post("/api/report", json=VALID_BODY))
        assert [line["type"] for line in lines] == ["progress", "progress", "progress", "report"]


class TestReportStreamValidation:
    def test_invalid_input_streams_a_single_invalid_line_using_real_validate_inputs(self):
        # No monkeypatch: pipeline.validate_inputs runs for real, so this also proves
        # the API is wired to the actual validation rules, not a stand-in for them.
        response = client.post(
            "/api/report",
            json={"query": "short", "profile": ""},
        )
        assert response.status_code == 200
        lines = _lines(response)
        assert len(lines) == 1
        assert lines[0]["type"] == "invalid"
        assert len(lines[0]["errors"]) == 1  # too-short query
        assert all(message[0].isupper() and message.endswith(".") for message in lines[0]["errors"])

    def test_unknown_fields_from_an_old_client_are_ignored(self, monkeypatch):
        def fake_run_report(query, profile, progress=None, **advanced):
            return {"bundle_version": 4}

        monkeypatch.setattr(api_index.pipeline, "run_report", fake_run_report)
        lines = _lines(client.post("/api/report", json={**VALID_BODY, "platform": "linkedin"}))
        assert lines[-1]["type"] == "report"

    def test_a_bad_time_range_is_invalid(self):
        lines = _lines(client.post("/api/report", json={**VALID_BODY, "days": 3}))
        assert lines[0]["type"] == "invalid"
        assert any("time range" in message for message in lines[0]["errors"])

    def test_bad_subreddit_input_is_invalid(self):
        lines = _lines(
            client.post(
                "/api/report",
                json={**VALID_BODY, "subreddits": "one,two,three,four,five,six,seven,eight,nine"},
            )
        )
        assert lines[0]["type"] == "invalid"

    def test_empty_query_is_invalid(self):
        lines = _lines(client.post("/api/report", json={**VALID_BODY, "query": ""}))
        assert lines[0]["type"] == "invalid"

    def test_garbage_body_is_reported_as_invalid_not_a_500(self):
        response = client.post(
            "/api/report", content="not json at all", headers={"content-type": "application/json"}
        )
        assert response.status_code == 200
        lines = _lines(response)
        assert lines[0]["type"] == "invalid"

    def test_non_object_json_body_is_reported_as_invalid(self):
        lines = _lines(client.post("/api/report", json=[1, 2, 3]))
        assert lines[0]["type"] == "invalid"

    def test_missing_body_fields_are_reported_as_invalid(self):
        lines = _lines(client.post("/api/report", json={}))
        assert lines[0]["type"] == "invalid"
        assert lines[0]["errors"]


class TestContentTypesPassThrough:
    """The advanced setting takes several formats, and old clients send one."""

    @staticmethod
    def _capture(monkeypatch):
        captured = {}

        def fake_run_report(query, profile, progress=None, **advanced):
            captured.update(advanced)
            return {"bundle_version": 4}

        monkeypatch.setattr(api_index.pipeline, "run_report", fake_run_report)
        return captured

    def test_a_list_of_content_types_reaches_the_engine(self, monkeypatch):
        captured = self._capture(monkeypatch)
        lines = _lines(
            client.post(
                "/api/report", json={**VALID_BODY, "content_types": ["poll", "text"]}
            )
        )
        assert lines[-1]["type"] == "report"
        assert captured["content_types"] == ["poll", "text"]

    def test_the_legacy_single_field_still_reaches_the_engine_as_a_list(self, monkeypatch):
        # A shared link or a replayed history entry from before the setting took
        # several values must not quietly lose the format it asked for.
        captured = self._capture(monkeypatch)
        lines = _lines(
            client.post("/api/report", json={**VALID_BODY, "content_type": "carousel"})
        )
        assert lines[-1]["type"] == "report"
        assert captured["content_types"] == ["carousel"]

    def test_no_content_types_reaches_the_engine_as_an_empty_list(self, monkeypatch):
        captured = self._capture(monkeypatch)
        _lines(client.post("/api/report", json=VALID_BODY))
        assert captured["content_types"] == []

    def test_an_unknown_content_type_is_invalid_before_anything_runs(self):
        lines = _lines(
            client.post("/api/report", json={**VALID_BODY, "content_types": ["reel"]})
        )
        assert lines[0]["type"] == "invalid"
        assert '"reel"' in lines[0]["errors"][0]

    def test_a_list_of_channels_reaches_the_engine(self, monkeypatch):
        captured = self._capture(monkeypatch)
        lines = _lines(
            client.post(
                "/api/report", json={**VALID_BODY, "channels": ["tiktok", "instagram"]}
            )
        )
        assert lines[-1]["type"] == "report"
        assert captured["channels"] == ["instagram", "tiktok"]

    def test_no_channels_reaches_the_engine_as_an_empty_list(self, monkeypatch):
        captured = self._capture(monkeypatch)
        _lines(client.post("/api/report", json=VALID_BODY))
        assert captured["channels"] == []

    def test_an_unknown_channel_is_invalid_before_anything_runs(self):
        lines = _lines(
            client.post("/api/report", json={**VALID_BODY, "channels": ["myspace"]})
        )
        assert lines[0]["type"] == "invalid"
        assert '"myspace"' in lines[0]["errors"][0]

    def test_channels_that_are_not_a_list_are_invalid(self):
        lines = _lines(client.post("/api/report", json={**VALID_BODY, "channels": 7}))
        assert lines[0]["type"] == "invalid"

    def test_content_types_that_are_not_a_list_are_invalid(self):
        lines = _lines(
            client.post("/api/report", json={**VALID_BODY, "content_types": 7})
        )
        assert lines[0]["type"] == "invalid"


class TestReportStreamErrors:
    def test_run_error_streams_progress_then_a_terminal_error_line(self, monkeypatch):
        def fake_run_report(query, profile, progress=None, **advanced):
            progress("Finding communities talking about this...")
            raise pipeline.RunError("We could not read anything usable from r/testsub.")

        monkeypatch.setattr(api_index.pipeline, "run_report", fake_run_report)
        lines = _lines(client.post("/api/report", json=VALID_BODY))
        assert lines[0]["type"] == "progress"
        assert lines[-1] == {
            "type": "error", "message": "We could not read anything usable from r/testsub."
        }
        assert len(lines) == 2

    def test_value_error_from_the_engine_passes_its_message_through_verbatim(self, monkeypatch):
        def fake_run_report(query, profile, progress=None, **advanced):
            raise ValueError("The analysis service has reached its rate limit.")

        monkeypatch.setattr(api_index.pipeline, "run_report", fake_run_report)
        lines = _lines(client.post("/api/report", json=VALID_BODY))
        assert lines == [{"type": "error", "message": "The analysis service has reached its rate limit."}]

    def test_unexpected_exception_becomes_the_generic_message_not_a_leaked_traceback(self, monkeypatch):
        def fake_run_report(query, profile, progress=None, **advanced):
            raise RuntimeError("some internal detail nobody outside should see")

        monkeypatch.setattr(api_index.pipeline, "run_report", fake_run_report)
        lines = _lines(client.post("/api/report", json=VALID_BODY))
        assert lines == [{"type": "error", "message": api_index.GENERIC_ERROR_MESSAGE}]
        assert "internal detail" not in json.dumps(lines)


class TestClientCancellation:
    """A user pressing Cancel is the system working, not a failure.

    pipeline.RunCancelled is deliberately not a RunError, and before this was
    handled it fell through to the last-resort `except Exception`, which logged
    "unhandled error while building a report" with a full traceback and pushed
    the generic failure message at a client that had already gone.
    """

    @staticmethod
    def _drain_one(monkeypatch, exception):
        """Start the generator, take the one line it can produce, close it.

        The stream is driven by hand rather than through TestClient because a
        cancelled run pushes nothing at all, so a client reading to the end
        would sit through the heartbeat loop until the hard stop. The heartbeat
        is shortened to keep the wait to a moment.
        """

        def fake_run_report(query, profile, progress=None, **advanced):
            raise exception

        monkeypatch.setattr(api_index.pipeline, "run_report", fake_run_report)
        monkeypatch.setattr(api_index, "HEARTBEAT_SECONDS", 0.05)

        generator = api_index._run_report_lines("q", "", 30, [], True)
        try:
            return json.loads(next(generator))
        finally:
            generator.close()

    @staticmethod
    def _wait_for(caplog, message, seconds=2.0):
        """The worker logs from its own thread, so give it a moment to land."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if any(record.getMessage() == message for record in caplog.records):
                return True
            time.sleep(0.01)
        return False

    def test_cancellation_logs_at_info_and_pushes_no_error_line(self, monkeypatch, caplog):
        with caplog.at_level(logging.INFO, logger=api_index.logger.name):
            line = self._drain_one(monkeypatch, pipeline.RunCancelled("selecting"))
            assert self._wait_for(caplog, "run cancelled by client")

        # Nothing went on the queue, so the only line the stream can produce is
        # the idle heartbeat. An "error" line here would be the old behaviour.
        assert line["type"] == "progress"

        cancelled = [r for r in caplog.records if r.getMessage() == "run cancelled by client"]
        assert [r.levelno for r in cancelled] == [logging.INFO]
        # No traceback anywhere: a cancelled run is not an incident.
        assert not any(record.exc_info for record in caplog.records)
        assert not any("unhandled error" in r.getMessage() for r in caplog.records)

    def test_a_real_failure_still_logs_a_traceback(self, monkeypatch, caplog):
        """The guard above must not have swallowed the genuine last resort."""
        with caplog.at_level(logging.INFO, logger=api_index.logger.name):
            line = self._drain_one(monkeypatch, RuntimeError("boom"))
            assert self._wait_for(caplog, "unhandled error while building a report")

        assert line == {"type": "error", "message": api_index.GENERIC_ERROR_MESSAGE}
        assert any(record.exc_info for record in caplog.records)


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
    """A run that succeeds instantly, so a test can be about what happens after."""

    def fake_run_report(query, profile, progress=None, **advanced):
        return bundle if bundle is not None else {"bundle_version": 4, "inputs": {"query": query}}

    monkeypatch.setattr(api_index.pipeline, "run_report", fake_run_report)


class TestReportPersistence:
    """A finished run is saved server-side, and its id rides on the terminal line.

    The id sits next to the bundle rather than inside it, so the stored bundle
    is byte-for-byte what the engine produced and the line stays readable by a
    client that knows nothing about saved reports.
    """

    def test_terminal_line_carries_the_report_id_when_the_save_succeeds(
        self, monkeypatch, fake_db
    ):
        _fake_run(monkeypatch)
        lines = _lines(client.post("/api/report", json=VALID_BODY))

        terminal = lines[-1]
        assert terminal["type"] == "report"
        report_id = terminal["report_id"]
        assert api_index.REPORT_ID_PATTERN.match(report_id)
        # Saved with the run's own inputs, not with whatever the client claimed.
        saved_id, query, profile, bundle = fake_db.saved_report_calls[0]
        assert saved_id == report_id
        assert query == VALID_BODY["query"]
        assert profile == ""
        assert bundle == terminal["bundle"]
        assert "report_id" not in terminal["bundle"]

    def test_two_runs_get_different_ids(self, monkeypatch):
        _fake_run(monkeypatch)
        first = _lines(client.post("/api/report", json=VALID_BODY))[-1]["report_id"]
        second = _lines(client.post("/api/report", json=VALID_BODY))[-1]["report_id"]
        assert first != second

    def test_report_id_is_omitted_when_storage_is_unavailable(self, monkeypatch, fake_db):
        # The whole point: a database that is down or simply not configured
        # costs the reader a share link, never the report itself.
        fake_db.unavailable.add("save_report")
        _fake_run(monkeypatch)
        lines = _lines(client.post("/api/report", json=VALID_BODY))

        assert lines[-1]["type"] == "report"
        assert "report_id" not in lines[-1]
        assert lines[-1]["bundle"]["bundle_version"] == 4

    def test_an_unexpected_storage_failure_also_leaves_the_run_intact(
        self, monkeypatch, fake_db, caplog
    ):
        fake_db.boom.add("save_report")
        _fake_run(monkeypatch)
        with caplog.at_level(logging.INFO, logger=api_index.logger.name):
            lines = _lines(client.post("/api/report", json=VALID_BODY))

        assert lines[-1]["type"] == "report"
        assert "report_id" not in lines[-1]
        # The connection string in the raised exception must never reach the
        # response, only the log.
        assert "hunter2" not in json.dumps(lines)

    def test_a_failed_run_saves_nothing(self, monkeypatch, fake_db):
        def fake_run_report(query, profile, progress=None, **advanced):
            raise pipeline.RunError("We could not read anything usable from r/testsub.")

        monkeypatch.setattr(api_index.pipeline, "run_report", fake_run_report)
        lines = _lines(client.post("/api/report", json=VALID_BODY))

        assert lines[-1]["type"] == "error"
        assert fake_db.saved_report_calls == []


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
