"""Security hardening added in this pass: API cost caps, origin checks, the
in-memory rate limiter, export-builder exception handling, CSV formula
injection guarding, filename date scrubbing, and the sample-availability probe.

Fully offline, same style as test_api.py: the engine (pipeline.run_report,
analysis.draft_post, the report_export builders) is monkeypatched everywhere,
so nothing here touches a real model or the Reddit archive.
"""

import json
import re

import pytest
from fastapi.testclient import TestClient

import api.index as api_index
import report_export
from api.index import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    """Module-level rate-limiter state is shared by every test in this
    process; without a reset, tests in this file (and test_api.py, run in the
    same session) would bleed hits into each other."""
    api_index._rate_limit_hits.clear()
    yield
    api_index._rate_limit_hits.clear()


VALID_DRAFT_BODY = {
    "idea": {
        "title": "One system", "hook": "Stop juggling five apps.", "format": "Text post",
        "angle": "Consolidate", "addresses": "Too many tools",
    },
    "evidence": ["We quoted 20 hours and delivered 65."],
    "query": "ai automation ideas for freelancers",
    "profile": "",
}

VALID_REPORT_BODY = {
    "query": "ai automation ideas for freelancers",
    "profile": "",
}


def _fake_run_report(query, profile, progress=None, **advanced):
    return {"bundle_version": 4}


def _fake_draft_post(idea, evidence, query, profile):
    return {"post": "A post."}


class TestDraftCostCaps:
    """The /api/draft boundary must not trust the caller: every field is
    length-capped before analysis.draft_post is ever called."""

    def test_idea_fields_are_truncated_at_300_chars(self, monkeypatch):
        captured = {}

        def fake_draft_post(idea, evidence, query, profile):
            captured["idea"] = idea
            return {"post": "A post."}

        monkeypatch.setattr(api_index.analysis, "draft_post", fake_draft_post)
        long_value = "x" * 5000
        body = {
            **VALID_DRAFT_BODY,
            "idea": {field: long_value for field in ("title", "hook", "format", "angle", "addresses")},
        }
        response = client.post("/api/draft", json=body)

        assert response.status_code == 200
        for value in captured["idea"].values():
            assert len(value) == 300

    def test_evidence_items_are_truncated_at_700_chars(self, monkeypatch):
        captured = {}

        def fake_draft_post(idea, evidence, query, profile):
            captured["evidence"] = evidence
            return {"post": "A post."}

        monkeypatch.setattr(api_index.analysis, "draft_post", fake_draft_post)
        body = {**VALID_DRAFT_BODY, "evidence": ["y" * 5000]}
        client.post("/api/draft", json=body)

        assert len(captured["evidence"][0]) == 700

    def test_query_and_profile_are_capped(self, monkeypatch):
        captured = {}

        def fake_draft_post(idea, evidence, query, profile):
            captured["query"] = query
            captured["profile"] = profile
            return {"post": "A post."}

        monkeypatch.setattr(api_index.analysis, "draft_post", fake_draft_post)
        body = {**VALID_DRAFT_BODY, "query": "q" * 5000, "profile": "p" * 5000}
        client.post("/api/draft", json=body)

        assert len(captured["query"]) <= api_index.pipeline.MAX_QUERY_CHARS
        assert len(captured["profile"]) <= api_index.pipeline.MAX_PROFILE_CHARS

    def test_oversized_body_is_rejected_before_reaching_the_engine(self, monkeypatch):
        def unexpected_call(*args, **kwargs):
            raise AssertionError("draft_post should not be called for an oversized body")

        monkeypatch.setattr(api_index.analysis, "draft_post", unexpected_call)
        body = {**VALID_DRAFT_BODY, "evidence": ["z" * 100_000]}
        response = client.post("/api/draft", json=body)

        assert response.status_code == 413
        assert "message" in response.json()


class TestOriginHardening:
    """Defense in depth on the two endpoints that cost money to run: a
    same-origin fetch() always sends a JSON content type and, in modern
    browsers, a same-origin Sec-Fetch-Site; anything else is rejected."""

    def test_wrong_content_type_is_rejected_on_report(self):
        response = client.post(
            "/api/report", content=json.dumps(VALID_REPORT_BODY),
            headers={"content-type": "text/plain"},
        )
        assert response.status_code == 415
        assert "message" in response.json()

    def test_wrong_content_type_is_rejected_on_draft(self):
        response = client.post(
            "/api/draft", content=json.dumps(VALID_DRAFT_BODY),
            headers={"content-type": "text/plain"},
        )
        assert response.status_code == 415
        assert "message" in response.json()

    def test_cross_site_sec_fetch_site_is_rejected_on_report(self):
        response = client.post(
            "/api/report", json=VALID_REPORT_BODY, headers={"sec-fetch-site": "cross-site"},
        )
        assert response.status_code == 403
        assert "message" in response.json()

    def test_cross_site_sec_fetch_site_is_rejected_on_draft(self):
        response = client.post(
            "/api/draft", json=VALID_DRAFT_BODY, headers={"sec-fetch-site": "cross-site"},
        )
        assert response.status_code == 403
        assert "message" in response.json()

    def test_same_origin_sec_fetch_site_is_allowed(self, monkeypatch):
        monkeypatch.setattr(api_index.pipeline, "run_report", _fake_run_report)
        response = client.post(
            "/api/report", json=VALID_REPORT_BODY, headers={"sec-fetch-site": "same-origin"},
        )
        assert response.status_code == 200

    def test_absent_sec_fetch_site_is_allowed(self, monkeypatch):
        # Older browsers and non-browser same-origin callers never send the
        # header at all; its absence must not be treated as suspicious.
        monkeypatch.setattr(api_index.analysis, "draft_post", _fake_draft_post)
        response = client.post("/api/draft", json=VALID_DRAFT_BODY)
        assert response.status_code == 200


class TestRateLimiter:
    """A per-instance, in-memory sliding window: best effort, not the durable
    fix (that is a Vercel WAF rule), but it should behave correctly on its
    own terms within a single process."""

    def test_report_rate_limit_returns_429_after_the_configured_count(self, monkeypatch):
        monkeypatch.setattr(api_index, "REPORTS_PER_HOUR", 2)
        monkeypatch.setattr(api_index.pipeline, "run_report", _fake_run_report)

        first = client.post("/api/report", json=VALID_REPORT_BODY)
        second = client.post("/api/report", json=VALID_REPORT_BODY)
        third = client.post("/api/report", json=VALID_REPORT_BODY)

        assert first.status_code == 200
        assert second.status_code == 200
        assert third.status_code == 429
        assert "message" in third.json()

    def test_draft_rate_limit_returns_429_after_the_configured_count(self, monkeypatch):
        monkeypatch.setattr(api_index, "DRAFTS_PER_HOUR", 1)
        monkeypatch.setattr(api_index.analysis, "draft_post", _fake_draft_post)

        first = client.post("/api/draft", json=VALID_DRAFT_BODY)
        second = client.post("/api/draft", json=VALID_DRAFT_BODY)

        assert first.status_code == 200
        assert second.status_code == 429
        assert "message" in second.json()

    def test_rate_limit_resets_after_the_window_elapses(self, monkeypatch):
        monkeypatch.setattr(api_index, "REPORTS_PER_HOUR", 1)
        monkeypatch.setattr(api_index.pipeline, "run_report", _fake_run_report)

        fake_now = [1_000.0]
        monkeypatch.setattr(api_index.time, "monotonic", lambda: fake_now[0])

        first = client.post("/api/report", json=VALID_REPORT_BODY)
        blocked = client.post("/api/report", json=VALID_REPORT_BODY)
        assert first.status_code == 200
        assert blocked.status_code == 429

        fake_now[0] += api_index.RATE_LIMIT_WINDOW_SECONDS + 1
        allowed_again = client.post("/api/report", json=VALID_REPORT_BODY)
        assert allowed_again.status_code == 200

    def test_a_different_ip_gets_its_own_budget(self, monkeypatch):
        monkeypatch.setattr(api_index, "REPORTS_PER_HOUR", 1)
        monkeypatch.setattr(api_index.pipeline, "run_report", _fake_run_report)

        first = client.post(
            "/api/report", json=VALID_REPORT_BODY, headers={"x-forwarded-for": "1.1.1.1"}
        )
        other_ip = client.post(
            "/api/report", json=VALID_REPORT_BODY, headers={"x-forwarded-for": "2.2.2.2"}
        )
        assert first.status_code == 200
        assert other_ip.status_code == 200


class TestExportBuilderExceptions:
    def test_export_builder_exception_becomes_a_400_with_a_message(self, monkeypatch):
        def exploding_builder(bundle):
            raise RuntimeError("boom")

        monkeypatch.setitem(
            api_index.EXPORTERS,
            "markdown",
            (exploding_builder, "text/markdown; charset=utf-8", "md", "report"),
        )
        response = client.post(
            "/api/export/markdown",
            json={"bundle": {"bundle_version": 4, "inputs": {"query": "x"}}},
        )
        assert response.status_code == 400
        assert response.json() == {"message": "That export could not be built."}


class TestMarkdownLinkTargets:
    """`_link` is the one place a raw URL is allowed to become `[label](url)`.
    A denylist of characters illegal in a URL, deliberately not an allowlist
    of permitted ones: Reddit permalinks carry raw Unicode (see the
    "cuánto_cobrarían" case below), and an ASCII allowlist was tested against
    the 2587 real URLs in sample_report.json and silently downgraded 32 of
    them to plain text."""

    def test_a_breakout_permalink_renders_as_text_not_a_link(self):
        url = "https://reddit.com/r/x/) [Free money](https://evil.example/"
        rendered = report_export._link("Free money", url)
        assert "](" not in rendered
        assert "evil.example" not in rendered

    def test_a_non_reddit_host_is_not_linked(self):
        rendered = report_export._link("Click here", "https://evil.example/steal")
        assert "](" not in rendered

    def test_a_javascript_url_is_not_linked(self):
        rendered = report_export._link("Click here", "javascript:alert(1)")
        assert "](" not in rendered

    def test_a_unicode_permalink_is_still_linked(self):
        # The regression guard: someone re-tightening _link into an ASCII
        # allowlist would break this exact case.
        url = "https://reddit.com/r/x/comments/1/cuánto_cobrarían_por_este_proyecto/"
        rendered = report_export._link("t", url)
        assert rendered == "[t]({})".format(url)

    def test_www_reddit_permalink_is_linked(self):
        url = "https://www.reddit.com/r/x/comments/1/a/"
        rendered = report_export._link("t", url)
        assert rendered == "[t]({})".format(url)

    def test_citation_links_routes_through_the_guard(self):
        citations = [{"text": "Free money", "url": "https://evil.example/", "date": "2026-01-01"}]
        rendered = report_export._citation_links(citations)
        assert "](https://evil.example" not in rendered

    def test_inline_topic_citation_line_routes_through_the_guard(self):
        bundle = {
            "inputs": {"query": "x"},
            "analysis": {
                "topics": [
                    {
                        "id": 1, "label": "Topic", "count": 1, "share": 100, "finding": "",
                        "item_indices": [1],
                        "citations": [
                            {"text": "t", "url": "https://evil.example/", "date": "2026-01-01", "score": 1}
                        ],
                    }
                ]
            },
        }
        markdown = report_export.report_markdown(bundle)
        assert "](https://evil.example" not in markdown

    def test_a_source_thread_title_cannot_break_out(self):
        item = {
            "kind": "post", "id": "p1", "subreddit": "x", "title": "| [ ] ( ) breakout",
            "body": "", "score": 1, "comments": 0,
            "url": "https://reddit.com/r/x/comments/1/a/", "date": "2026-01-01",
        }
        bundle = {"inputs": {"query": "x"}, "items": [item], "analysis": {"item_sentiments": []}}
        markdown = report_export.report_markdown(bundle)
        section = markdown[markdown.index("## Source threads"):]
        assert "\\| \\[ \\] \\(" in section

    def test_a_bare_url_in_a_quoted_body_is_still_defanged(self):
        item = {
            "kind": "post", "id": "p1", "subreddit": "x", "title": "t",
            "body": "check http://evil.example/x for details", "score": 1, "comments": 0,
            "url": "https://reddit.com/r/x/comments/1/a/", "date": "2026-01-01",
        }
        bundle = {"inputs": {"query": "x"}, "items": [item], "analysis": {"item_sentiments": []}}
        markdown = report_export.report_markdown(bundle)
        assert "`http://evil.example/x`" in markdown
        assert "[http://evil.example" not in markdown

    def test_a_backslash_cannot_neutralise_the_escaping(self):
        rendered = report_export._escape("\\[x\\]\\(/evil\\)")
        assert "[x](/evil)" not in rendered


class TestCsvFormulaInjectionGuard:
    def test_leading_equals_is_prefixed_with_a_quote(self):
        text = report_export._csv(["header"], [["=cmd|'/c calc'!A1"]])
        assert "'=cmd" in text

    def test_leading_plus_minus_at_tab_cr_are_all_prefixed(self):
        for char in ("=", "+", "-", "@", "\t", "\r"):
            cell = report_export._csv_safe_cell(char + "danger")
            assert cell.startswith("'")

    def test_ordinary_cells_are_untouched(self):
        assert report_export._csv_safe_cell("hello") == "hello"
        assert report_export._csv_safe_cell(5) == "5"
        assert report_export._csv_safe_cell(None) == ""


class TestFilenameStemScrubbing:
    def test_a_malicious_generated_at_is_scrubbed(self):
        bundle = {"inputs": {"query": "ai tools"}, "generated_at": '"\r\nSet-Cookie: x=1'}
        stem = report_export.filename_stem(bundle)
        assert "\r" not in stem and "\n" not in stem and '"' not in stem
        assert report_export._FILENAME_STEM_SAFE.match(stem)

    def test_stem_falls_back_to_report_when_it_cannot_be_made_safe(self, monkeypatch):
        # Forces the final safety check to reject every stem, to prove the
        # fallback branch itself works and not just that scrubbing already
        # prevents an unsafe stem from being built in the first place.
        monkeypatch.setattr(report_export, "_FILENAME_STEM_SAFE", re.compile(r"^$"))
        bundle = {"inputs": {"query": "ai tools"}, "generated_at": "2026-07-26"}
        assert report_export.filename_stem(bundle) == "report"


class TestSampleAvailableEndpoint:
    def test_available_true_when_the_file_exists_and_is_non_empty(self, tmp_path, monkeypatch):
        sample_path = tmp_path / "sample.json"
        sample_path.write_text(json.dumps({"bundle_version": 4}))
        monkeypatch.setattr(api_index, "SAMPLE_PATH", sample_path)

        response = client.get("/api/sample/available")
        assert response.status_code == 200
        assert response.json() == {"available": True}

    def test_available_false_when_the_file_is_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(api_index, "SAMPLE_PATH", tmp_path / "does-not-exist.json")
        assert client.get("/api/sample/available").json() == {"available": False}

    def test_available_true_even_for_a_stale_bundle_version(self, tmp_path, monkeypatch):
        # Deliberately looser than GET /api/sample: this is a cheap stat-only
        # probe with no JSON parse, so it does not know about bundle_version.
        sample_path = tmp_path / "sample.json"
        sample_path.write_text(json.dumps({"bundle_version": 1}))
        monkeypatch.setattr(api_index, "SAMPLE_PATH", sample_path)

        assert client.get("/api/sample/available").json() == {"available": True}

    def test_full_sample_endpoint_sets_cache_control(self, tmp_path, monkeypatch):
        sample_path = tmp_path / "sample.json"
        sample_path.write_text(json.dumps({"bundle_version": 4, "inputs": {"query": "x"}}))
        monkeypatch.setattr(api_index, "SAMPLE_PATH", sample_path)
        api_index._sample_cache.clear()

        response = client.get("/api/sample")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "public, max-age=3600"

    def test_full_sample_endpoint_cache_misses_on_a_regenerated_file(self, tmp_path, monkeypatch):
        sample_path = tmp_path / "sample.json"
        sample_path.write_text(json.dumps({"bundle_version": 4, "inputs": {"query": "first"}}))
        monkeypatch.setattr(api_index, "SAMPLE_PATH", sample_path)
        api_index._sample_cache.clear()

        first = client.get("/api/sample").json()
        assert first["bundle"]["inputs"]["query"] == "first"

        # A regenerated file gets a new mtime, which must miss the cache even
        # though the path is unchanged.
        import os
        import time as time_module

        sample_path.write_text(json.dumps({"bundle_version": 4, "inputs": {"query": "second"}}))
        os.utime(sample_path, (time_module.time() + 5, time_module.time() + 5))

        second = client.get("/api/sample").json()
        assert second["bundle"]["inputs"]["query"] == "second"
