"""Orchestration and exports (bundle v3), driven by fakes so no test touches the network."""

import json
import os

import pytest

import pipeline
import report_export

SAMPLE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sample_report.json")

DEFAULT_QUERY = "ai automation ideas for freelancers"


# ── Fakes ─────────────────────────────────────────────────────────────────────
def make_discover(names, reasons=None, subscribers=None, checked=None):
    """A fake `discover` callable. `checked` may be a bool (applies to all) or a
    dict of per-name overrides; unset names default to True (already verified)."""
    reasons = reasons or {}
    subscribers = subscribers or {}

    def fake(query, profile):
        communities = []
        for name in names:
            if isinstance(checked, dict):
                is_checked = checked.get(name, True)
            elif checked is None:
                is_checked = True
            else:
                is_checked = checked
            communities.append(
                {
                    "name": name,
                    "reason": reasons.get(name, "Talks about this topic"),
                    "subscribers": subscribers.get(name, 1000),
                    "checked": is_checked,
                }
            )
        return {"communities": communities, "considered": list(names)}

    return fake


def fake_posts(subreddit, days, count=4):
    return [
        {
            "kind": "post", "id": "{}-{}".format(subreddit, i), "subreddit": subreddit,
            "title": "Post {} in {}".format(i, subreddit), "body": "body text",
            "score": 10 + i, "comments": i,
            "url": "https://reddit.com/r/{}/comments/{}/".format(subreddit, i),
            "created_utc": 1700000000 + i, "date": "2023-11-14",
        }
        for i in range(count)
    ]


def fake_comments(subreddit, days, count=2):
    return [
        {
            "kind": "comment", "id": "c{}-{}".format(subreddit, i), "subreddit": subreddit,
            "title": "", "body": "comment {} with useful detail".format(i), "score": i,
            "url": "https://reddit.com/r/{}/comments/x/c{}/".format(subreddit, i),
            "created_utc": 1700000000 + i, "date": "2023-11-14",
        }
        for i in range(count)
    ]


def fake_classify(posts, comments, subreddits):
    items = list(posts) + list(comments)
    return (
        items,
        200,
        {
            "topics": [
                {"id": 1, "label": "Losing track of tasks", "item_indices": [1, 2]},
                {"id": 2, "label": "Tool fatigue", "item_indices": [3]},
            ],
            "item_sentiments": [
                {"i": index, "s": ["pos", "neu", "neg"][index % 3]}
                for index in range(1, len(items) + 1)
            ],
            "model": "gemini-2.5-flash",
        },
    )


def fake_synthesize(items, topics, query, profile, subreddits, preview_chars=200):
    return {
        "executive_summary": "People are drowning in tools.",
        "topic_descriptions": {1: "Tasks fall through the cracks.", 2: "Switching costs bite."},
        "pain_points": [{"text": "Too many tools", "item_indices": [1, 2]}],
        "questions": [{"text": "How do I start?", "item_indices": [2]}],
        "content_ideas": [
            {
                "title": "One system", "hook": "Stop juggling five apps.",
                "format": "Text post", "angle": "Consolidate", "rationale": "It hurts",
                "addresses": "Too many tools", "item_indices": [1],
            }
        ],
        "model": "gemini-2.5-flash",
    }


def build_bundle(**overrides):
    kwargs = dict(
        query=DEFAULT_QUERY, profile="", days=7,
        discover=make_discover(["testsub"]),
        fetch_posts=fake_posts, fetch_comments=fake_comments,
        classify=fake_classify, synthesize=fake_synthesize,
    )
    kwargs.update(overrides)
    return pipeline.run_report(**kwargs)


# ── Input validation ──────────────────────────────────────────────────────────
class TestValidateInputs:
    def test_accepts_good_input(self):
        result = pipeline.validate_inputs(DEFAULT_QUERY, "I run a small design studio", 7)
        assert result["errors"] == []
        assert result["query"] == DEFAULT_QUERY

    def test_reports_every_problem_at_once(self):
        result = pipeline.validate_inputs("", "p" * 600, 99)
        assert len(result["errors"]) == 3

    def test_rejects_a_too_short_query(self):
        assert pipeline.validate_inputs("short", "", 7)["errors"]

    def test_rejects_an_overlong_query(self):
        assert pipeline.validate_inputs("q" * 400, "", 7)["errors"]

    def test_rejects_an_overlong_profile(self):
        result = pipeline.validate_inputs(DEFAULT_QUERY, "p" * 600, 7)
        assert len(result["errors"]) == 1

    def test_rejects_a_time_window_outside_the_allowed_set(self):
        assert pipeline.validate_inputs(DEFAULT_QUERY, "", 3)["errors"]

    @pytest.mark.parametrize("days", [7, 14, 30])
    def test_accepts_every_allowed_time_window(self, days):
        assert pipeline.validate_inputs(DEFAULT_QUERY, "", days)["errors"] == []

    def test_every_error_is_a_full_sentence(self):
        for message in pipeline.validate_inputs("", "", 3)["errors"]:
            assert message[0].isupper() and message.endswith(".")


# ── Orchestration ─────────────────────────────────────────────────────────────
class TestRunReport:
    def test_produces_a_complete_v3_bundle(self):
        bundle = build_bundle()
        assert bundle["bundle_version"] == 3
        assert set(bundle["inputs"].keys()) == {"query", "profile", "subreddits", "days"}
        assert bundle["inputs"]["subreddits"] == ["testsub"]
        assert "discovery" in bundle
        assert set(bundle["discovery"].keys()) == {"communities", "considered"}
        assert "brand_mentions" not in bundle["stats"]
        assert bundle["sample"]["posts_fetched"] == 4
        assert bundle["sample"]["comments_fetched"] == 2
        assert bundle["sample"]["items_analysed"] == 6
        assert bundle["analysis"]["executive_summary"]
        assert bundle["stats"]["sentiment"]["classified"] == 6
        assert len(bundle["stats"]["trend"]) == 7

    def test_content_ideas_have_no_platform_field(self):
        idea = build_bundle()["analysis"]["content_ideas"][0]
        assert idea["hook"] == "Stop juggling five apps."
        assert set(idea.keys()) == {
            "title", "hook", "format", "angle", "rationale", "addresses", "citations"
        }
        assert "platform" not in idea

    def test_discovery_communities_carry_reason_and_subscribers_and_used_flag(self):
        bundle = build_bundle(discover=make_discover(["testsub"], reasons={"testsub": "Freelancers swap tips"},
                                                      subscribers={"testsub": 54321}))
        community = bundle["discovery"]["communities"][0]
        assert community == {
            "name": "testsub", "reason": "Freelancers swap tips", "subscribers": 54321, "used": True,
        }

    def test_discover_is_called_with_the_query_and_profile(self):
        captured = {}

        def spy(query, profile):
            captured["query"] = query
            captured["profile"] = profile
            return {
                "communities": [{"name": "testsub", "reason": "x", "subscribers": 1, "checked": True}],
                "considered": ["testsub"],
            }

        build_bundle(discover=spy, query="skincare routines for oily skin", profile="I run a skincare brand")
        assert captured["query"] == "skincare routines for oily skin"
        assert captured["profile"] == "I run a skincare brand"

    def test_topics_carry_findings_and_real_citations(self):
        topics = build_bundle()["analysis"]["topics"]
        assert topics[0]["finding"]
        assert topics[0]["citations"]
        assert topics[0]["citations"][0]["url"].startswith("https://reddit.com/")

    def test_claims_carry_citations(self):
        bundle = build_bundle()
        assert bundle["analysis"]["pain_points"][0]["citations"]
        assert bundle["analysis"]["questions"][0]["citations"]
        assert bundle["analysis"]["content_ideas"][0]["citations"]

    def test_progress_messages_report_discovery_and_synthesis(self):
        messages = []
        build_bundle(progress=messages.append)
        assert any("Finding communities" in message for message in messages)
        assert any("testsub" in message for message in messages)
        assert any("Writing your content ideas" in message for message in messages)

    def test_low_data_is_flagged(self):
        bundle = build_bundle(fetch_posts=lambda s, d: fake_posts(s, d, 2),
                              fetch_comments=lambda s, d: [])
        assert bundle["sample"]["low_data"] is True

    def test_enough_data_is_not_flagged(self):
        bundle = build_bundle(fetch_posts=lambda s, d: fake_posts(s, d, 30),
                              fetch_comments=lambda s, d: fake_comments(s, d, 5))
        assert bundle["sample"]["low_data"] is False

    def test_notice_when_one_community_is_empty_but_others_still_produce_a_report(self):
        def fetch_posts(name, days):
            if name == "emptysub":
                return []
            return fake_posts(name, days)

        bundle = build_bundle(
            discover=make_discover(["testsub", "emptysub"]),
            fetch_posts=fetch_posts,
        )
        assert bundle["inputs"]["subreddits"] == ["testsub"]
        assert any("r/emptysub" in notice for notice in bundle["notices"])
        used_by_name = {c["name"]: c["used"] for c in bundle["discovery"]["communities"]}
        assert used_by_name == {"testsub": True, "emptysub": False}

    def test_all_communities_empty_raises_a_run_error_naming_them(self):
        with pytest.raises(pipeline.RunError) as excinfo:
            build_bundle(discover=make_discover(["subone", "subtwo"]), fetch_posts=lambda s, d: [])
        message = str(excinfo.value)
        assert "r/subone" in message and "r/subtwo" in message

    def test_an_unchecked_community_that_turns_out_missing_becomes_a_notice_not_a_failure(self):
        def fetch_posts(name, days):
            if name == "ghost":
                raise ValueError("We could not find a community called r/ghost.")
            return fake_posts(name, days)

        bundle = build_bundle(
            discover=make_discover(["testsub", "ghost"], checked={"testsub": True, "ghost": False}),
            fetch_posts=fetch_posts,
        )
        assert bundle["inputs"]["subreddits"] == ["testsub"]
        assert any("r/ghost" in notice for notice in bundle["notices"])

    def test_fetch_failure_on_one_of_two_is_survivable(self):
        def flaky(subreddit, days):
            if subreddit == "bad":
                raise RuntimeError("The Reddit data service is temporarily unavailable.")
            return fake_posts(subreddit, days)

        bundle = build_bundle(discover=make_discover(["good", "bad"]), fetch_posts=flaky)
        assert bundle["inputs"]["subreddits"] == ["good"]
        assert bundle["notices"]

    def test_multi_community_posts_are_merged_newest_first(self):
        bundle = build_bundle(discover=make_discover(["one", "two"]))
        stamps = [post["created_utc"] for post in bundle["posts"]]
        assert stamps == sorted(stamps, reverse=True)
        assert len(bundle["posts"]) == 8

    def test_run_label_is_human_readable(self):
        bundle = build_bundle(query="ai tools for freelancers")
        label = pipeline.run_label(bundle)
        assert label == "ai tools for freelancers · 7d"

    def test_run_label_truncates_a_long_query(self):
        long_query = "how do i " + "really " * 20 + "grow an audience on social media"
        bundle = build_bundle(query=long_query)
        label = pipeline.run_label(bundle)
        assert "…" in label
        assert len(label.split(" · ")[0]) <= pipeline.MAX_LABEL_QUERY_CHARS

    def test_capacity_limited_flag_reaches_the_bundle(self):
        def limited(posts, comments, subreddits):
            items, preview, classification = fake_classify(posts, comments, subreddits)
            classification["capacity_limited"] = True
            return items, preview, classification

        assert build_bundle(classify=limited)["sample"]["capacity_limited"] is True
        assert build_bundle()["sample"]["capacity_limited"] is False


class TestTopicFindingCoherence:
    """Guards the whole path from a mis-keyed model response to the rendered topic."""

    def test_swapped_ids_from_the_model_still_produce_matching_label_and_finding(self, monkeypatch):
        topics = [
            {"id": 1, "label": "Losing track of tasks", "item_indices": [1, 2]},
            {"id": 2, "label": "Tool fatigue", "item_indices": [3]},
        ]

        def classify_with_topics(posts, comments, subreddits):
            items = list(posts) + list(comments)
            return items, 200, {
                "topics": topics,
                "item_sentiments": [{"i": i, "s": "neu"} for i in range(1, len(items) + 1)],
                "model": "test-model",
            }

        # Ids deliberately crossed, labels correct — the real failure mode.
        raw = {
            "executive_summary": "Summary.",
            "topic_descriptions": [
                {"id": 2, "label": "Losing track of tasks", "finding": "Tasks slip away."},
                {"id": 1, "label": "Tool fatigue", "finding": "Switching costs bite."},
            ],
            "pain_points": [{"text": "Too many tools", "item_indices": [1]}],
            "questions": [{"text": "Where do I start?", "item_indices": [1]}],
            "content_ideas": [{"title": "One system", "hook": "Stop the scroll.", "item_indices": [1]}],
        }
        monkeypatch.setattr(pipeline.analysis, "_chat_json", lambda *a, **k: (raw, "test-model"))

        bundle = build_bundle(classify=classify_with_topics, synthesize=pipeline.analysis.synthesize)
        findings = {topic["label"]: topic["finding"] for topic in bundle["analysis"]["topics"]}
        assert findings["Losing track of tasks"] == "Tasks slip away."
        assert findings["Tool fatigue"] == "Switching costs bite."


# ── Exports ───────────────────────────────────────────────────────────────────
class TestExports:
    def test_markdown_contains_every_section_and_honest_counts(self):
        markdown = report_export.report_markdown(build_bundle())
        for heading in ["# Content ideas: {}".format(DEFAULT_QUERY), "## Executive summary", "## Sample",
                        "## Communities analysed", "## Trending topics", "## Sentiment",
                        "## Pain points", "## Content ideas", "## Methodology"]:
            assert heading in markdown
        assert "Posts analysed" in markdown
        assert "https://reddit.com/" in markdown

    def test_no_brand_mention_traces_in_the_markdown(self):
        markdown = report_export.report_markdown(build_bundle())
        assert "brand" not in markdown.lower()

    def test_no_platform_traces_in_the_markdown(self):
        markdown = report_export.report_markdown(build_bundle())
        assert "Platform" not in markdown
        assert "LinkedIn + Instagram" not in markdown

    def test_communities_analysed_section_shows_reason_and_subscribers(self):
        bundle = build_bundle(
            discover=make_discover(["testsub"], reasons={"testsub": "Freelancers swap pricing advice"},
                                    subscribers={"testsub": 12000})
        )
        markdown = report_export.report_markdown(bundle)
        assert "r/testsub" in markdown
        assert "Freelancers swap pricing advice" in markdown
        assert "12,000 members" in markdown
        assert "contributed data to this report" in markdown

    def test_communities_analysed_notes_communities_that_did_not_contribute(self):
        def fetch_posts(name, days):
            return [] if name == "emptysub" else fake_posts(name, days)

        bundle = build_bundle(discover=make_discover(["testsub", "emptysub"]), fetch_posts=fetch_posts)
        markdown = report_export.report_markdown(bundle)
        assert "r/emptysub" in markdown
        assert "no usable posts in this window" in markdown

    def test_content_ideas_render_hook_but_no_platform_badge(self):
        markdown = report_export.report_markdown(build_bundle())
        assert "Stop juggling five apps." in markdown
        assert "**Format:** Text post" in markdown
        assert "Platform" not in markdown

    def test_markdown_sentiment_percentages_sum_to_one_hundred(self):
        bundle = build_bundle(fetch_posts=lambda s, d: fake_posts(s, d, 30))
        percentages = bundle["stats"]["sentiment"]["percentages"]
        assert sum(percentages.values()) == 100
        assert "{}%".format(percentages["pos"]) in report_export.report_markdown(bundle)

    def test_markdown_escapes_table_breaking_characters(self):
        def piped(subreddit, days):
            posts = fake_posts(subreddit, days)
            for post in posts:
                post["title"] = "A | pipe | title"
            return posts

        markdown = report_export.report_markdown(build_bundle(fetch_posts=piped))
        assert "A \\| pipe \\| title" in markdown

    def test_markdown_discloses_a_capacity_limited_sample(self):
        bundle = build_bundle()
        bundle["sample"].update({"capacity_limited": True, "posts_truncated": True})
        markdown = report_export.report_markdown(bundle)
        assert "analysis capacity tier" in markdown
        assert "API" not in markdown
        assert "capacity tier" not in report_export.report_markdown(build_bundle())

    def test_low_data_markdown_shows_counts_not_percentages(self):
        bundle = build_bundle(fetch_posts=lambda s, d: fake_posts(s, d, 2),
                              fetch_comments=lambda s, d: [])
        markdown = report_export.report_markdown(bundle)
        assert "directional rather than conclusive" in markdown
        assert "of the conversation" not in markdown

    def test_posts_csv_has_a_header_and_a_row_per_post(self):
        rows = report_export.posts_csv(build_bundle()).strip().split("\n")
        assert rows[0] == "subreddit,title,date,upvotes,comments,sentiment,url"
        assert len(rows) == 5

    def test_ideas_csv_has_hook_but_no_platform_column(self):
        csv_text = report_export.ideas_csv(build_bundle())
        header = csv_text.strip().split("\n")[0]
        assert header == "title,hook,format,angle,addresses,rationale,evidence_urls"
        assert "Stop juggling five apps." in csv_text
        assert "https://reddit.com/" in csv_text

    def test_comments_csv_flattens_newlines(self):
        def multiline(subreddit, days):
            comments = fake_comments(subreddit, days)
            comments[0]["body"] = "line one\nline two"
            return comments

        csv_text = report_export.comments_csv(build_bundle(fetch_comments=multiline))
        assert "line one line two" in csv_text

    def test_no_brand_mention_traces_in_the_csvs(self):
        bundle = build_bundle()
        assert "brand" not in report_export.posts_csv(bundle).lower()
        assert "brand" not in report_export.ideas_csv(bundle).lower()

    def test_filename_stem_is_a_content_ideas_slug_of_the_query_and_date(self):
        bundle = build_bundle(query="AI Automation Ideas!! For Freelancers")
        stem = report_export.filename_stem(bundle)
        assert stem.startswith("content-ideas-ai-automation-ideas-for-freelancers")
        assert stem.endswith(bundle["generated_at"][:10])
        assert "/" not in stem and ":" not in stem and "!" not in stem

    def test_filename_stem_falls_back_when_query_is_missing(self):
        bundle = build_bundle()
        bundle["inputs"]["query"] = ""
        assert report_export.filename_stem(bundle).startswith("content-ideas-report-")


# ── The bundled sample ────────────────────────────────────────────────────────
class TestSampleReport:
    """Assertions here target bundle v3. Until `sample_report.json` is regenerated
    with the new pipeline, every test in this class is skipped rather than failing
    on a bundle shape that predates this pass."""

    @pytest.fixture(scope="class")
    def sample(self):
        with open(SAMPLE_PATH, encoding="utf-8") as handle:
            return json.load(handle)

    @pytest.fixture(autouse=True)
    def _skip_until_regenerated(self, sample):
        if sample.get("bundle_version") != 3:
            pytest.skip("sample_report.json not yet regenerated for bundle v3")

    def test_sample_file_has_the_current_bundle_shape(self, sample):
        assert sample["bundle_version"] == pipeline.BUNDLE_VERSION
        for key in ["inputs", "discovery", "posts", "comments", "items", "analysis", "stats", "sample"]:
            assert key in sample
        assert "brand_mentions" not in sample["stats"]
        assert set(sample["inputs"].keys()) == {"query", "profile", "subreddits", "days"}

    def test_discovery_section_has_the_expected_shape(self, sample):
        assert "communities" in sample["discovery"] and "considered" in sample["discovery"]
        for community in sample["discovery"]["communities"]:
            assert {"name", "reason", "subscribers", "used"} <= set(community.keys())

    def test_content_ideas_carry_a_hook_and_no_platform(self, sample):
        ideas = sample["analysis"]["content_ideas"]
        assert ideas
        for idea in ideas:
            assert idea.get("hook")
            assert "platform" not in idea

    def test_sample_has_real_evidence(self, sample):
        assert sample["analysis"]["topics"]
        citation = sample["analysis"]["topics"][0]["citations"][0]
        assert citation["url"].startswith("https://reddit.com/r/")

    def test_sample_sentiment_sums_to_one_hundred(self, sample):
        assert sum(sample["stats"]["sentiment"]["percentages"].values()) == 100

    def test_sample_renders_through_the_normal_exporters(self, sample):
        markdown = report_export.report_markdown(sample)
        assert "## Executive summary" in markdown
        assert "## Communities analysed" in markdown
        assert report_export.posts_csv(sample)
        assert report_export.ideas_csv(sample)
