"""FastAPI layer: the NDJSON streaming contract, the sample endpoint, and exports.

This file adds no business logic of its own (see api/index.py's docstring), so these
tests are about the adapter, not the engine: does a valid run stream progress then a
terminal report line, does invalid input short-circuit to one "invalid" line, does an
engine failure become one clean "error" line, does the sample endpoint read the file
fresh every time. Fully offline: pipeline.run_report and the report_export builders
are monkeypatched, so nothing here touches a real model or the Reddit archive.
"""

import json

import pytest
from fastapi.testclient import TestClient

import api.index as api_index
import pipeline
from api.index import app

client = TestClient(app)

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
        assert lines[2] == {
            "type": "report",
            "bundle": {"bundle_version": 4, "inputs": {"query": VALID_BODY["query"]}, "sample": {}},
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
            client.post("/api/report", json={**VALID_BODY, "subreddits": "one,two,three,four,five"})
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


class TestSampleEndpoint:
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
    def _patch_exporter(monkeypatch, kind, body, media_type, extension):
        """EXPORTERS binds the builder function at import time, so patching
        `report_export.<name>` after the fact would silently miss; the dict entry
        itself is what post_export actually consults, so that is what gets patched."""
        monkeypatch.setitem(api_index.EXPORTERS, kind, (lambda bundle: body, media_type, extension))

    def test_markdown_export_sets_content_disposition_from_filename_stem(self, monkeypatch):
        self._patch_exporter(monkeypatch, "markdown", "# Report body", "text/markdown; charset=utf-8", "md")
        monkeypatch.setattr(
            api_index.report_export, "filename_stem",
            lambda bundle: "content-ideas-ai-tools-for-founders-2026-07-26",
        )

        response = client.post("/api/export/markdown", json={"bundle": self.BUNDLE})
        assert response.status_code == 200
        assert response.text == "# Report body"
        assert response.headers["content-type"].startswith("text/markdown")
        assert response.headers["content-disposition"] == (
            'attachment; filename="content-ideas-ai-tools-for-founders-2026-07-26.md"'
        )

    def test_ideas_csv_export_uses_the_csv_media_type_and_extension(self, monkeypatch):
        self._patch_exporter(monkeypatch, "ideas-csv", "title,hook\nA,B\n", "text/csv; charset=utf-8", "csv")
        monkeypatch.setattr(api_index.report_export, "filename_stem", lambda bundle: "content-ideas-x-2026-07-26")

        response = client.post("/api/export/ideas-csv", json={"bundle": self.BUNDLE})
        assert response.status_code == 200
        assert response.text == "title,hook\nA,B\n"
        assert response.headers["content-type"].startswith("text/csv")
        assert response.headers["content-disposition"].endswith('ideas-x-2026-07-26.csv"')

    def test_posts_csv_export_uses_the_posts_builder(self, monkeypatch):
        self._patch_exporter(monkeypatch, "posts-csv", "subreddit,title\nx,y\n", "text/csv; charset=utf-8", "csv")
        monkeypatch.setattr(api_index.report_export, "filename_stem", lambda bundle: "content-ideas-x-2026-07-26")

        response = client.post("/api/export/posts-csv", json={"bundle": self.BUNDLE})
        assert response.status_code == 200
        assert response.text == "subreddit,title\nx,y\n"

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
