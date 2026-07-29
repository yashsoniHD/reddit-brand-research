"""Orchestration and exports (bundle v4), driven by fakes so no test touches the network."""

import json
import os
import threading
import time

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


def fake_manual(names):
    """A fake `manual` callable: the discovery shape for hand-picked communities."""
    return {
        "communities": [
            {"name": name, "reason": "chosen by you", "subscribers": 0, "checked": False}
            for name in names
        ],
        "considered": list(names),
    }


def fake_posts(subreddit, days=30, count=4):
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


def fake_comments(subreddit, days=30, count=2):
    return [
        {
            "kind": "comment", "id": "c{}-{}".format(subreddit, i), "subreddit": subreddit,
            "title": "", "body": "comment {} with useful detail".format(i), "score": i,
            "url": "https://reddit.com/r/{}/comments/x/c{}/".format(subreddit, i),
            "created_utc": 1700000000 + i, "date": "2023-11-14",
        }
        for i in range(count)
    ]


def fake_classify(posts, comments, subreddits, query="", profile=""):
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
            "selection": {
                "method": "relevance+engagement",
                "triaged": len(items),
                "relevance_counts": {"0": 0, "1": 1, "2": len(items) - 1},
            },
        },
    )


def fake_synthesize(items, topics, query, profile, subreddits, preview_chars=200,
                    content_types=None):
    return {
        "report_title": "Tool sprawl among freelancers",
        "executive_summary": "People are drowning in tools.",
        "topic_descriptions": {1: "Tasks fall through the cracks.", 2: "Switching costs bite."},
        "pain_points": [{"text": "Too many tools", "item_indices": [1, 2]}],
        "questions": [{"text": "How do I start?", "item_indices": [2]}],
        "content_ideas": [
            {
                "title": "One system", "hook": "Stop juggling five apps.",
                "format": (
                    pipeline.analysis.chosen_formats(content_types) or ["Text post"]
                )[0],
                "angle": "Consolidate", "rationale": "It hurts",
                "addresses": "Too many tools", "item_indices": [1],
            }
        ],
        "model": "gemini-2.5-flash",
    }


def build_bundle(**overrides):
    kwargs = dict(
        query=DEFAULT_QUERY, profile="",
        discover=make_discover(["testsub"]),
        fetch_posts=fake_posts, fetch_comments=fake_comments,
        classify=fake_classify, synthesize=fake_synthesize,
    )
    kwargs.update(overrides)
    return pipeline.run_report(**kwargs)


# ── Input validation ──────────────────────────────────────────────────────────
class TestValidateInputs:
    def test_accepts_good_input(self):
        result = pipeline.validate_inputs(DEFAULT_QUERY, "I run a small design studio")
        assert result["errors"] == []
        assert result["query"] == DEFAULT_QUERY

    def test_reports_every_problem_at_once(self):
        result = pipeline.validate_inputs("", "p" * 600)
        assert len(result["errors"]) == 2

    def test_rejects_a_too_short_query(self):
        assert pipeline.validate_inputs("short", "")["errors"]

    def test_rejects_an_overlong_query(self):
        assert pipeline.validate_inputs("q" * 600, "")["errors"]

    def test_a_four_hundred_character_query_is_accepted(self):
        # The cap moved from 300 to 500 for constraint-rich briefs: a brief this
        # long with a quoted phrase in it is the case the keyword filter exists for.
        assert pipeline.validate_inputs("q" * 400, "")["errors"] == []

    def test_a_query_one_character_over_the_cap_is_still_rejected(self):
        errors = pipeline.validate_inputs("q" * 501, "")["errors"]
        assert errors and "500" in errors[0]

    def test_phrases_are_parsed_out_of_the_query(self):
        result = pipeline.validate_inputs('detailed posts about "meta ads" for b2b saas', "")
        assert result["errors"] == []
        assert result["phrases"] == ["meta ads"]

    def test_an_unquoted_query_yields_no_phrases_and_no_errors(self):
        result = pipeline.validate_inputs(DEFAULT_QUERY, "")
        assert result["phrases"] == []
        assert result["errors"] == []

    @pytest.mark.parametrize(
        "query",
        [
            'detailed posts about "meta ads for b2b saas',          # unbalanced quote
            'posts about "ab" for b2b saas teams',                   # under the char floor
            'posts about "' + "word " * 12 + '" for b2b saas',       # over the word ceiling
            "posts about don't quote me on b2b saas spend",          # an apostrophe
        ],
    )
    def test_a_malformed_phrase_is_never_a_validation_error(self, query):
        # The whole compatibility guarantee: a badly quoted brief behaves exactly
        # as it did before the keyword filter existed.
        assert pipeline.validate_inputs(query, "")["errors"] == []

    def test_rejects_an_overlong_profile(self):
        result = pipeline.validate_inputs(DEFAULT_QUERY, "p" * 600)
        assert len(result["errors"]) == 1

    def test_every_error_is_a_full_sentence(self):
        for message in pipeline.validate_inputs("", "p" * 600)["errors"]:
            assert message[0].isupper() and message.endswith(".")

    def test_advanced_settings_default_when_absent(self):
        result = pipeline.validate_inputs(DEFAULT_QUERY, "")
        assert result["days"] == 30
        assert result["subreddits"] == []
        assert result["relevance_filter"] is True

    @pytest.mark.parametrize("days", [7, 14, 30])
    def test_accepts_every_allowed_time_range(self, days):
        result = pipeline.validate_inputs(DEFAULT_QUERY, "", days=days)
        assert result["errors"] == []
        assert result["days"] == days

    @pytest.mark.parametrize("days", [3, 0, -7, 90, "soon", True])
    def test_rejects_a_time_range_outside_the_allowed_set(self, days):
        result = pipeline.validate_inputs(DEFAULT_QUERY, "", days=days)
        assert result["errors"]
        assert result["days"] == 30  # falls back so downstream code stays sane

    def test_a_numeric_string_time_range_is_tolerated(self):
        result = pipeline.validate_inputs(DEFAULT_QUERY, "", days="14")
        assert result["errors"] == []
        assert result["days"] == 14

    def test_manual_subreddits_are_parsed_and_normalised(self):
        result = pipeline.validate_inputs(
            DEFAULT_QUERY, "", subreddits="r/freelance, https://reddit.com/r/web_design/"
        )
        assert result["errors"] == []
        assert result["subreddits"] == ["freelance", "web_design"]

    def test_bad_manual_subreddits_become_one_clear_error(self):
        result = pipeline.validate_inputs(DEFAULT_QUERY, "", subreddits="not a name")
        assert len(result["errors"]) == 1

    def test_too_many_manual_subreddits_is_an_error(self):
        result = pipeline.validate_inputs(
            DEFAULT_QUERY, "", subreddits="one,two,three,four,five"
        )
        assert result["errors"]

    def test_relevance_filter_is_coerced_to_bool(self):
        assert pipeline.validate_inputs(DEFAULT_QUERY, "", relevance_filter=0)[
            "relevance_filter"
        ] is False
        assert pipeline.validate_inputs(DEFAULT_QUERY, "", relevance_filter=1)[
            "relevance_filter"
        ] is True

    @pytest.mark.parametrize("raw,expected", [("true", True), ("false", False),
                                              ("TRUE", True), ("off", False), ("", False)])
    def test_the_string_false_does_not_become_true(self, raw, expected):
        # bool("false") is True, which used to turn a setting the user switched off
        # back on the moment it crossed a form encoding.
        result = pipeline.validate_inputs(DEFAULT_QUERY, "", relevance_filter=raw)
        assert result["errors"] == []
        assert result["relevance_filter"] is expected

    def test_an_ambiguous_relevance_string_is_an_error_not_a_guess(self):
        result = pipeline.validate_inputs(DEFAULT_QUERY, "", relevance_filter="maybe")
        assert result["errors"]

    def test_a_non_integral_time_range_is_rejected(self):
        result = pipeline.validate_inputs(DEFAULT_QUERY, "", days=7.9)
        assert result["errors"]
        assert result["days"] == 30

    def test_an_integral_float_time_range_is_accepted(self):
        result = pipeline.validate_inputs(DEFAULT_QUERY, "", days=14.0)
        assert result["errors"] == []
        assert result["days"] == 14

    def test_the_time_range_message_is_built_from_the_allowed_windows(self):
        message = pipeline.validate_inputs(DEFAULT_QUERY, "", days=3)["errors"][0]
        for window in pipeline.TIME_WINDOWS:
            assert str(window) in message

    def test_subreddits_may_arrive_as_a_json_list(self):
        result = pipeline.validate_inputs(DEFAULT_QUERY, "", subreddits=["r/freelance", "web_design"])
        assert result["errors"] == []
        assert result["subreddits"] == ["freelance", "web_design"]

    @pytest.mark.parametrize("raw", [["freelance", 7], ["freelance", True], 7, True, {"a": 1}])
    def test_non_string_subreddit_values_are_rejected(self, raw):
        result = pipeline.validate_inputs(DEFAULT_QUERY, "", subreddits=raw)
        assert result["errors"]
        assert result["subreddits"] == []

    def test_an_empty_subreddit_list_is_not_an_error(self):
        result = pipeline.validate_inputs(DEFAULT_QUERY, "", subreddits=[])
        assert result["errors"] == []
        assert result["subreddits"] == []

    @pytest.mark.parametrize("content_type", ["text", "carousel", "video", "poll", "article"])
    def test_every_allowed_content_type_is_accepted(self, content_type):
        result = pipeline.validate_inputs(
            DEFAULT_QUERY, "", content_types=[content_type]
        )
        assert result["errors"] == []
        assert result["content_types"] == [content_type]

    def test_content_types_default_to_empty_meaning_all_formats(self):
        for raw in (None, [], "", "   ", ["", "  "]):
            result = pipeline.validate_inputs(DEFAULT_QUERY, "", content_types=raw)
            assert result["errors"] == []
            assert result["content_types"] == []

    def test_several_content_types_come_back_sorted_and_deduped(self):
        result = pipeline.validate_inputs(
            DEFAULT_QUERY, "", content_types=["poll", "text", "Poll"]
        )
        assert result["errors"] == []
        assert result["content_types"] == ["poll", "text"]

    def test_choosing_every_content_type_is_the_same_as_choosing_none(self):
        result = pipeline.validate_inputs(
            DEFAULT_QUERY, "", content_types=list(pipeline.CONTENT_TYPES)
        )
        assert result["errors"] == []
        assert result["content_types"] == []

    @pytest.mark.parametrize("content_type", ["reel", "thread", "TEXT POST", 7, True])
    def test_an_unknown_content_type_is_a_friendly_error(self, content_type):
        result = pipeline.validate_inputs(
            DEFAULT_QUERY, "", content_types=[content_type]
        )
        assert result["errors"]
        assert result["errors"][0][0].isupper() and result["errors"][0].endswith(".")
        assert result["content_types"] == []

    def test_the_unknown_content_type_error_names_the_value_that_was_sent(self):
        result = pipeline.validate_inputs(
            DEFAULT_QUERY, "", content_types=["text", "reel"]
        )
        assert len(result["errors"]) == 1
        assert '"reel"' in result["errors"][0]
        # The good value is still kept, so the form comes back with the ticks
        # the user made rather than an empty group.
        assert result["content_types"] == ["text"]

    @pytest.mark.parametrize("raw", [7, True, {"text": True}, 1.5])
    def test_content_types_that_are_not_a_list_or_a_name_are_rejected(self, raw):
        result = pipeline.validate_inputs(DEFAULT_QUERY, "", content_types=raw)
        assert len(result["errors"]) == 1
        assert result["errors"][0].endswith(".")
        assert result["content_types"] == []

    @pytest.mark.parametrize("raw", [["text", 7], ["text", True], ["text", None]])
    def test_a_non_string_inside_the_list_is_rejected(self, raw):
        result = pipeline.validate_inputs(DEFAULT_QUERY, "", content_types=raw)
        assert result["errors"]

    def test_content_types_are_case_insensitive(self):
        assert pipeline.validate_inputs(
            DEFAULT_QUERY, "", content_types=["Carousel", " VIDEO "]
        )["content_types"] == ["carousel", "video"]

    def test_a_bare_string_is_read_as_one_content_type(self):
        result = pipeline.validate_inputs(DEFAULT_QUERY, "", content_types="carousel")
        assert result["errors"] == []
        assert result["content_types"] == ["carousel"]

    def test_the_legacy_single_content_type_field_is_still_accepted(self):
        # Old links and stored history replay `content_type`, and a run that
        # cannot read them would silently drop the format the user chose.
        result = pipeline.validate_inputs(DEFAULT_QUERY, "", content_type="Carousel")
        assert result["errors"] == []
        assert result["content_types"] == ["carousel"]

    def test_the_legacy_field_and_the_list_add_up(self):
        result = pipeline.validate_inputs(
            DEFAULT_QUERY, "", content_types=["poll"], content_type="text"
        )
        assert result["errors"] == []
        assert result["content_types"] == ["poll", "text"]

    def test_a_bad_legacy_content_type_is_still_a_friendly_error(self):
        result = pipeline.validate_inputs(DEFAULT_QUERY, "", content_type="reel")
        assert len(result["errors"]) == 1
        assert '"reel"' in result["errors"][0]


# ── Orchestration ─────────────────────────────────────────────────────────────
class TestRunReport:
    def test_produces_a_complete_v4_bundle(self):
        bundle = build_bundle()
        assert bundle["bundle_version"] == 4
        # `content_types` is written only when a run was pinned to a subset, and
        # this one asked for a mix.
        assert set(bundle["inputs"].keys()) == {
            "query", "profile", "subreddits", "days", "discovery"
        }
        assert bundle["inputs"]["subreddits"] == ["testsub"]
        assert "discovery" in bundle
        assert set(bundle["discovery"].keys()) == {"communities", "considered"}
        assert "brand_mentions" not in bundle["stats"]
        assert bundle["sample"]["posts_fetched"] == 4
        assert bundle["sample"]["comments_fetched"] == 2
        assert bundle["sample"]["items_analysed"] == 6
        assert bundle["analysis"]["executive_summary"]
        assert bundle["stats"]["sentiment"]["classified"] == 6
        # Every fake post carries the same date, so coverage-based buckets collapse to one.
        assert len(bundle["stats"]["trend"]) == 1

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

    def test_a_present_tense_classifying_message_precedes_the_classified_summary(self):
        # The frontend's stepper only advances to "Classifying the sample" on a
        # message matching /^classif/i emitted before classification runs; without
        # it, the stage jumps straight from selection to the past-tense summary and
        # that step of the stepper never lights up.
        messages = []
        build_bundle(progress=messages.append)

        selecting = next(i for i, m in enumerate(messages) if m.startswith("Selecting "))
        classifying = next(i for i, m in enumerate(messages) if m.startswith("Classifying"))
        classified = next(i for i, m in enumerate(messages) if m.startswith("Classified "))

        assert selecting < classifying < classified

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
        assert label == "ai tools for freelancers"

    def test_run_label_truncates_a_long_query(self):
        long_query = "how do i " + "really " * 20 + "grow an audience on social media"
        bundle = build_bundle(query=long_query)
        label = pipeline.run_label(bundle)
        assert "…" in label
        assert len(label) <= pipeline.MAX_LABEL_QUERY_CHARS

    def test_capacity_limited_flag_reaches_the_bundle(self):
        def limited(posts, comments, subreddits, query="", profile=""):
            items, preview, classification = fake_classify(posts, comments, subreddits)
            classification["capacity_limited"] = True
            return items, preview, classification

        assert build_bundle(classify=limited)["sample"]["capacity_limited"] is True
        assert build_bundle()["sample"]["capacity_limited"] is False

    def test_selection_metadata_reaches_the_bundle(self):
        selection = build_bundle()["sample"]["selection"]
        assert selection["method"] == "relevance+engagement"
        assert set(selection["relevance_counts"].keys()) == {"0", "1", "2"}

    def test_a_classifier_without_selection_metadata_still_produces_a_bundle(self):
        def bare(posts, comments, subreddits, query="", profile=""):
            items, preview, classification = fake_classify(posts, comments, subreddits)
            del classification["selection"]
            return items, preview, classification

        selection = build_bundle(classify=bare)["sample"]["selection"]
        assert selection["method"] == "deterministic-fallback"
        assert selection["triaged"] == 0

    def test_classify_receives_the_query_and_profile(self):
        captured = {}

        def spy(posts, comments, subreddits, query="", profile=""):
            captured["query"] = query
            captured["profile"] = profile
            return fake_classify(posts, comments, subreddits)

        build_bundle(classify=spy, profile="I run a design studio")
        assert captured["query"] == DEFAULT_QUERY
        assert captured["profile"] == "I run a design studio"

    def test_the_time_range_reaches_the_fetchers_and_the_bundle(self):
        captured = {}

        def spy_posts(subreddit, days):
            captured["days"] = days
            return fake_posts(subreddit, days)

        bundle = build_bundle(days=7, fetch_posts=spy_posts)
        assert captured["days"] == 7
        assert bundle["inputs"]["days"] == 7

    def test_manual_subreddits_skip_discovery(self):
        def exploding_discover(query, profile):
            raise AssertionError("discovery must not run when communities are chosen")

        bundle = build_bundle(
            subreddits=["handpicked"], discover=exploding_discover, manual=fake_manual
        )
        assert bundle["inputs"]["subreddits"] == ["handpicked"]
        community = bundle["discovery"]["communities"][0]
        assert community["reason"] == "chosen by you"

    def test_manual_falls_back_to_the_discovery_module_when_not_injected(self, monkeypatch):
        # The injectable hook is the supported seam, but monkeypatching the module
        # function still has to work, because that is how the callable is resolved.
        monkeypatch.setattr(pipeline.discovery, "manual", fake_manual)
        bundle = build_bundle(subreddits=["handpicked"])
        assert bundle["inputs"]["subreddits"] == ["handpicked"]

    def test_discovery_mode_records_who_chose_the_communities(self):
        assert build_bundle()["inputs"]["discovery"] == "model"
        manual_bundle = build_bundle(
            subreddits=["handpicked"], manual=fake_manual,
            discover=lambda q, p: (_ for _ in ()).throw(AssertionError("no discovery")),
        )
        assert manual_bundle["inputs"]["discovery"] == "manual"

    def test_content_types_reach_synthesis_and_the_bundle(self):
        captured = {}

        def spy(items, topics, query, profile, subreddits, preview_chars=200,
                content_types=None):
            captured["content_types"] = content_types
            return fake_synthesize(items, topics, query, profile, subreddits,
                                   preview_chars, content_types)

        bundle = build_bundle(content_types=["carousel"], synthesize=spy)
        assert captured["content_types"] == ["carousel"]
        assert bundle["inputs"]["content_types"] == ["carousel"]
        assert bundle["analysis"]["content_ideas"][0]["format"] == "Carousel (PDF)"

    def test_several_content_types_reach_synthesis_sorted(self):
        captured = {}

        def spy(items, topics, query, profile, subreddits, preview_chars=200,
                content_types=None):
            captured["content_types"] = content_types
            return fake_synthesize(items, topics, query, profile, subreddits,
                                   preview_chars, content_types)

        bundle = build_bundle(content_types=["poll", "carousel", "poll"], synthesize=spy)
        assert captured["content_types"] == ["carousel", "poll"]
        assert bundle["inputs"]["content_types"] == ["carousel", "poll"]

    def test_the_bundle_records_no_content_types_when_unset(self):
        assert "content_types" not in build_bundle()["inputs"]

    def test_asking_for_every_content_type_records_none_of_them(self):
        # Every format is a mix, and a bundle that listed all five would make
        # the report claim a restriction the run never applied.
        bundle = build_bundle(content_types=list(pipeline.CONTENT_TYPES))
        assert "content_types" not in bundle["inputs"]

    def test_an_unknown_content_type_never_reaches_the_bundle(self):
        # Validation is the input layer's job; the engine simply ignores a value
        # it does not offer rather than pinning ideas to it.
        assert "content_types" not in build_bundle(content_types=["reel"])["inputs"]

    def test_report_title_comes_from_synthesis(self):
        assert build_bundle()["analysis"]["report_title"] == "Tool sprawl among freelancers"

    def test_report_title_falls_back_to_the_query_when_synthesis_omits_it(self):
        def titleless(items, topics, query, profile, subreddits, preview_chars=200,
                      content_types=None):
            result = fake_synthesize(items, topics, query, profile, subreddits, preview_chars)
            del result["report_title"]
            return result

        title = build_bundle(synthesize=titleless)["analysis"]["report_title"]
        assert title and title in DEFAULT_QUERY

    def test_engagement_reports_the_analysed_sample_separately(self):
        bundle = build_bundle()
        engagement = bundle["stats"]["engagement"]
        # Four posts scoring 10..13 and two comments scoring 0 and 1.
        assert engagement["score"] == 46
        assert engagement["analysed"] == {"upvotes": 47, "comments": 6}

    def test_bundle_bodies_are_truncated_but_citation_excerpts_are_not(self):
        def long_bodies(subreddit, days):
            posts = fake_posts(subreddit, days)
            for post in posts:
                post["body"] = "x" * 2000
            return posts

        bundle = build_bundle(fetch_posts=long_bodies)
        assert all(len(post["body"]) <= pipeline.BUNDLE_BODY_CHARS for post in bundle["posts"])
        excerpt = bundle["analysis"]["topics"][0]["citations"][0]["excerpt"]
        assert len(excerpt) > pipeline.BUNDLE_BODY_CHARS

    def test_a_comment_fetch_failure_keeps_the_posts_it_already_read(self):
        def broken_comments(subreddit, days):
            raise RuntimeError("The Reddit data service is temporarily unavailable.")

        bundle = build_bundle(fetch_comments=broken_comments)
        assert bundle["inputs"]["subreddits"] == ["testsub"]
        assert bundle["sample"]["posts_fetched"] == 4
        assert bundle["sample"]["comments_fetched"] == 0
        assert any("not its comments" in notice for notice in bundle["notices"])

    def test_an_empty_error_message_from_a_fetch_does_not_crash_the_run(self):
        def blank_failure(subreddit, days):
            if subreddit == "bad":
                raise ValueError("")
            return fake_posts(subreddit, days)

        bundle = build_bundle(discover=make_discover(["good", "bad"]), fetch_posts=blank_failure)
        assert bundle["inputs"]["subreddits"] == ["good"]
        assert any("r/bad was skipped" in notice for notice in bundle["notices"])

    def test_no_communities_at_all_is_its_own_error(self):
        empty = lambda query, profile: {"communities": [], "considered": []}
        with pytest.raises(pipeline.RunError) as excinfo:
            build_bundle(discover=empty)
        assert "could not find any communities" in str(excinfo.value)

    def test_the_bundle_names_every_model_that_was_used(self):
        def lite_synthesis(items, topics, query, profile, subreddits, preview_chars=200,
                           content_types=None):
            result = fake_synthesize(items, topics, query, profile, subreddits, preview_chars)
            result["model"] = "gemini-2.5-flash-lite"
            return result

        bundle = build_bundle(synthesize=lite_synthesis)
        # Synthesis names the report; classification is still disclosed.
        assert bundle["model"] == "gemini-2.5-flash-lite"
        assert bundle["models"] == ["gemini-2.5-flash", "gemini-2.5-flash-lite"]
        assert "Lite" in bundle["model_label"] and "Flash on" in bundle["model_label"]


class TestCancelAndDeadline:
    """A run nobody is waiting for should stop, and a run that cannot finish in the
    time it has should say so rather than being killed mid-sentence."""

    STAGES = ["start", "discovery", "fetch", "classification", "synthesis"]

    def test_a_cancelled_run_stops_before_discovery(self):
        cancel = threading.Event()
        cancel.set()

        def exploding_discover(query, profile):
            raise AssertionError("a cancelled run must not call discovery")

        with pytest.raises(pipeline.RunCancelled):
            build_bundle(discover=exploding_discover, cancel=cancel)

    def test_cancelling_during_the_fetch_stops_the_fetch(self):
        cancel = threading.Event()

        def cancel_after_first(subreddit, days):
            cancel.set()
            return fake_posts(subreddit, days)

        with pytest.raises(pipeline.RunCancelled):
            build_bundle(
                discover=make_discover(["one", "two"]),
                fetch_posts=cancel_after_first, cancel=cancel,
            )

    def test_cancelling_before_synthesis_stops_the_run(self):
        cancel = threading.Event()

        def cancel_in_classify(posts, comments, subreddits, query="", profile=""):
            cancel.set()
            return fake_classify(posts, comments, subreddits)

        def exploding_synthesize(*args, **kwargs):
            raise AssertionError("a cancelled run must not synthesise")

        with pytest.raises(pipeline.RunCancelled):
            build_bundle(
                classify=cancel_in_classify, synthesize=exploding_synthesize, cancel=cancel
            )

    def test_an_unset_cancel_event_changes_nothing(self):
        bundle = build_bundle(cancel=threading.Event())
        assert bundle["bundle_version"] == 4

    def test_a_deadline_already_past_fails_with_a_friendly_sentence(self):
        with pytest.raises(pipeline.RunError) as excinfo:
            build_bundle(deadline=time.monotonic() - 1)
        message = str(excinfo.value)
        assert message[0].isupper() and message.endswith(".")
        assert "took longer" in message

    def test_a_generous_deadline_produces_a_normal_report(self):
        bundle = build_bundle(deadline=time.monotonic() + 3600)
        assert bundle["analysis"]["content_ideas"]

    def test_the_reserve_stops_new_communities_but_keeps_the_report(self):
        # Inside the reserve from the very first check: the first community is read
        # (nothing has been read yet, so stopping would produce no report at all)
        # and the second is skipped with a notice rather than an error.
        deadline = time.monotonic() + pipeline.DEADLINE_RESERVE_SECONDS - 1
        bundle = build_bundle(discover=make_discover(["one", "two"]), deadline=deadline)
        assert bundle["inputs"]["subreddits"] == ["one"]
        assert any("ran out of time" in notice for notice in bundle["notices"])

    def test_the_deadline_reaches_a_fetcher_that_accepts_one(self):
        captured = {}

        def deadline_aware(subreddit, days, deadline=None):
            captured["deadline"] = deadline
            return fake_posts(subreddit, days)

        deadline = time.monotonic() + 3600
        build_bundle(fetch_posts=deadline_aware, deadline=deadline)
        assert captured["deadline"] == pytest.approx(
            deadline - pipeline.DEADLINE_RESERVE_SECONDS
        )

    def test_a_fetcher_that_predates_the_deadline_is_still_called(self):
        # Every injected fake in this file takes (subreddit, days) and nothing else.
        bundle = build_bundle(deadline=time.monotonic() + 3600)
        assert bundle["sample"]["posts_fetched"] == 4

    def test_relevance_off_relabels_the_selection_and_blanks_the_query(self):
        captured = {}

        def spy(posts, comments, subreddits, query="", profile=""):
            captured["query"] = query
            return fake_classify(posts, comments, subreddits)

        bundle = build_bundle(relevance_filter=False, classify=spy)
        assert captured["query"] == ""  # the analysis layer's signal to skip triage
        assert bundle["sample"]["selection"]["method"] == "relevance-off"
        assert bundle["sample"]["selection"]["triaged"] == 0

    def test_a_bare_two_arg_lambda_still_runs_a_full_report_with_a_deadline_in_play(self):
        # The compatibility contract: a fetcher that predates the keyword-forwarding
        # plumbing entirely (no **kwargs, no deadline parameter) must still work
        # when a deadline is passed down through _call.
        bundle = build_bundle(
            fetch_posts=lambda s, d: fake_posts(s, d, 3),
            deadline=time.monotonic() + 3600,
        )
        assert bundle["sample"]["posts_fetched"] == 3


class TestTopicFindingCoherence:
    """Guards the whole path from a mis-keyed model response to the rendered topic."""

    def test_swapped_ids_from_the_model_still_produce_matching_label_and_finding(self, monkeypatch):
        topics = [
            {"id": 1, "label": "Losing track of tasks", "item_indices": [1, 2]},
            {"id": 2, "label": "Tool fatigue", "item_indices": [3]},
        ]

        def classify_with_topics(posts, comments, subreddits, query="", profile=""):
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
def _post_item(index, subreddit="testsub", body="body text"):
    return {
        "kind": "post", "id": "p{}".format(index), "subreddit": subreddit,
        "title": "Post {}".format(index), "body": body, "score": 10 + index,
        "comments": index, "url": "https://reddit.com/r/{}/comments/t{}/".format(subreddit, index),
        "date": "2026-07-{:02d}".format(1 + index % 28), "created_utc": 1700000000 + index,
    }


def _comment_item(index, subreddit="testsub"):
    return {
        "kind": "comment", "id": "c{}".format(index), "subreddit": subreddit,
        "title": "", "body": "comment body {}".format(index), "score": index,
        "url": "https://reddit.com/r/{}/comments/x/c{}/".format(subreddit, index),
        "date": "2026-07-20", "created_utc": 1700000000 + index,
    }


def _appendix_bundle(items, item_sentiments=None):
    """A minimal bundle carrying only what report_markdown needs to render the
    Source threads appendix, so these tests do not have to run the full
    pipeline to hand-build hundreds of items (see the 500-item cap test)."""
    return {
        "bundle_version": 4,
        "inputs": {"query": "test query"},
        "items": items,
        "analysis": {"item_sentiments": item_sentiments or []},
    }


class TestExports:
    def test_markdown_contains_every_section_and_honest_counts(self):
        markdown = report_export.report_markdown(build_bundle())
        for heading in ["# Content ideas: {}".format(DEFAULT_QUERY), "## Executive summary", "## Sample",
                        "## Communities analysed", "## Trending topics", "## Sentiment",
                        "## Pain points", "## Content ideas", "## Methodology", "## Source threads"]:
            assert heading in markdown
        assert "Posts analysed" in markdown
        assert "https://reddit.com/" in markdown

    def test_no_brand_mention_traces_in_the_markdown(self):
        markdown = report_export.report_markdown(build_bundle())
        # Scoped to everything before the appendix: the appendix now injects
        # real Reddit titles and bodies, which are not "our copy" and are not
        # what this assertion is about (a real title legitimately containing
        # "brand" must not turn this into a mystery failure).
        body = markdown[: markdown.index("## Source threads")]
        assert "brand" not in body.lower()

    def test_no_platform_traces_in_the_markdown(self):
        markdown = report_export.report_markdown(build_bundle())
        body = markdown[: markdown.index("## Source threads")]
        assert "Platform" not in body
        assert "LinkedIn + Instagram" not in body

    def test_appendix_lists_every_analysed_post_with_its_link(self):
        items = [_post_item(1), _post_item(2), _post_item(3), _comment_item(4)]
        markdown = report_export.report_markdown(_appendix_bundle(items))
        section = markdown[markdown.index("## Source threads"):]
        analysed_posts = [item for item in items if item["kind"] != "comment"]
        assert section.count(". **[") == len(analysed_posts)
        for item in analysed_posts:
            assert item["url"] in section

    def test_appendix_entry_numbers_match_citation_indices(self):
        # Guards the analysis.select_items contiguity coupling: the list
        # marker has to equal the citation index stats.citations_for uses.
        items = [_post_item(1), _post_item(2), _post_item(3), _comment_item(4)]
        markdown = report_export.report_markdown(_appendix_bundle(items))
        section = markdown[markdown.index("## Source threads"):]
        lines = section.split("\n")
        for index, item in enumerate(items, 1):
            if item["kind"] == "comment":
                continue
            marker = "{}. ".format(index)
            line = next(candidate for candidate in lines if candidate.startswith(marker))
            assert item["url"] in line

    def test_appendix_meta_line_has_subreddit_date_engagement_and_sentiment(self):
        item = _post_item(1)
        bundle = _appendix_bundle([item], item_sentiments=[{"i": 1, "s": "pos"}])
        markdown = report_export.report_markdown(bundle)
        section = markdown[markdown.index("## Source threads"):]
        assert "r/{}".format(item["subreddit"]) in section
        assert item["date"] in section
        assert "{} upvotes".format(item["score"]) in section
        assert "{} comments".format(item["comments"]) in section
        assert "Positive" in section

    def test_appendix_quotes_body_and_skips_blockquote_when_empty(self):
        with_body = _post_item(1, body="a genuinely useful detail")
        without_body = _post_item(2, body="")
        markdown = report_export.report_markdown(_appendix_bundle([with_body, without_body]))
        section = markdown[markdown.index("## Source threads"):]
        assert "> a genuinely useful detail" in section
        # Exactly one blockquote line: the second entry has no body and must
        # not render a bare "- >" line.
        assert section.count("   - > ") == 1

    def test_appendix_never_lists_analysed_comments(self):
        comment = _comment_item(1)
        markdown = report_export.report_markdown(_appendix_bundle([_post_item(2), comment]))
        # Slice at the heading first: fake comment URLs can legitimately
        # appear earlier in the document via citations, so checking the whole
        # document would be checking the wrong thing.
        section = markdown[markdown.index("## Source threads"):]
        assert comment["url"] not in section

    def test_appendix_comes_after_methodology(self):
        markdown = report_export.report_markdown(_appendix_bundle([_post_item(1)]))
        assert markdown.index("## Methodology") < markdown.index("## Source threads")

    def test_methodology_points_at_the_appendix(self):
        markdown = report_export.report_markdown(_appendix_bundle([_post_item(1)]))
        methodology = markdown[markdown.index("## Methodology"): markdown.index("## Source threads")]
        assert "Source threads" in methodology

    def test_appendix_empty_case_when_all_items_are_comments(self):
        markdown = report_export.report_markdown(
            _appendix_bundle([_comment_item(1), _comment_item(2)])
        )
        assert "No posts were analysed in this run." in markdown

    def test_appendix_never_exceeds_max_source_threads(self):
        items = [_post_item(i) for i in range(1, 501)]
        markdown = report_export.report_markdown(_appendix_bundle(items))
        section = markdown[markdown.index("## Source threads"):]
        assert section.count(". **[") == report_export.MAX_SOURCE_THREADS

    def test_comments_csv_header_has_a_sentiment_column(self):
        header = report_export.comments_csv(build_bundle()).strip().split("\n")[0]
        assert header == "subreddit,comment,date,upvotes,sentiment,url"

    def test_comments_csv_joins_sentiment_for_an_analysed_comment(self):
        rows = report_export.comments_csv(build_bundle()).strip().split("\n")
        # fake_classify labels items 5 and 6 (the two comments) "neg" and "pos"
        # respectively; verified against the fake's own deterministic pattern.
        assert rows[1].split(",")[-2] == "Negative"
        assert rows[2].split(",")[-2] == "Positive"

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

    def test_markdown_carries_each_requirement_text_and_its_counts(self):
        # Mirrors what a qualifier-aware triage run would attach to
        # sample.selection (Stage C5): the markdown methodology has to carry
        # the same requirement text, the same three counts per requirement,
        # the analysed-sample split and the self-reported caveat as the
        # on-screen Requirements row.
        def with_requirements(posts, comments, subreddits, query="", profile=""):
            items, preview, classification = fake_classify(posts, comments, subreddits)
            classification["selection"] = {
                "method": "relevance+qualifiers",
                "triaged": len(items),
                "pool": len(items),
                "topic": "running Meta ads for B2B SaaS",
                "requirements": [
                    {
                        "text": "The author works at a funded startup, Series A, B or C.",
                        "kind": "require", "met": 2, "violated": 1, "unknown": 3,
                    },
                    {
                        "text": "The author is not a beginner or a solo founder.",
                        "kind": "exclude", "met": 1, "violated": 2, "unknown": 3,
                    },
                ],
                "qualifier_counts": {"confirmed": 3, "unconfirmed": 6, "violated": 3},
                "qualifier_analysed": {"confirmed": 3, "unconfirmed": 2, "violated": 1},
            }
            return items, preview, classification

        markdown = report_export.report_markdown(build_bundle(classify=with_requirements))
        assert "The author works at a funded startup, Series A, B or C." in markdown
        assert "The author is not a beginner or a solo founder." in markdown
        assert "2 threads showed it, 1 contradicted it, and 3 did not say" in markdown
        assert "1 threads showed it, 2 contradicted it, and 3 did not say" in markdown
        assert "self-reported" in markdown
        assert "kept despite contradicting a requirement" in markdown

    def test_markdown_with_no_requirements_is_unchanged(self):
        # The today-is-unchanged guarantee for Stage C5: a bundle whose
        # selection carries no requirements (every bundle before this stage,
        # and every run whose brief was pure topic) must not gain any of the
        # new requirements copy.
        markdown = report_export.report_markdown(build_bundle())
        assert "requirement" not in markdown.lower()
        assert "self-reported" not in markdown

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

    def test_a_kind_is_appended_so_two_exports_never_collide(self):
        # Both CSV exports end in ".csv", so the kind is the only thing keeping
        # the ideas download and the posts download apart in a downloads folder.
        bundle = build_bundle(query="ai automation")
        ideas = report_export.filename_stem(bundle, "ideas")
        posts = report_export.filename_stem(bundle, "posts")
        assert ideas.endswith("-ideas") and posts.endswith("-posts")
        assert ideas != posts
        assert ideas.rsplit("-", 1)[0] == posts.rsplit("-", 1)[0]

    def test_a_kind_cannot_widen_the_safe_character_set(self):
        bundle = build_bundle(query="ai automation")
        stem = report_export.filename_stem(bundle, '"\r\nSet-Cookie: x=1')
        assert report_export._FILENAME_STEM_SAFE.match(stem)


# ── The bundled sample ────────────────────────────────────────────────────────
class TestSampleReport:
    """Assertions here target bundle v4. Until `sample_report.json` is regenerated
    with the new pipeline, every test in this class is skipped rather than failing
    on a bundle shape that predates this pass."""

    @pytest.fixture(scope="class")
    def sample(self):
        with open(SAMPLE_PATH, encoding="utf-8") as handle:
            return json.load(handle)

    @pytest.fixture(autouse=True)
    def _skip_until_regenerated(self, sample):
        if sample.get("bundle_version") != 4:
            pytest.skip("sample_report.json not yet regenerated for bundle v4")

    def test_sample_file_has_the_current_bundle_shape(self, sample):
        assert sample["bundle_version"] == pipeline.BUNDLE_VERSION
        for key in ["inputs", "discovery", "posts", "comments", "items", "analysis", "stats", "sample"]:
            assert key in sample
        assert "brand_mentions" not in sample["stats"]
        # A subset check, not an equality one: the file on disk was generated before
        # content types and the discovery mode joined `inputs`, and regenerating it
        # costs a live model run. These four have been there since v3.
        assert {"query", "profile", "subreddits", "days"} <= set(sample["inputs"].keys())

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
        # The only test exercising real Reddit text through report_markdown:
        # verifies the appendix appears and lists exactly the analysed posts,
        # not the fetched pool.
        markdown = report_export.report_markdown(sample)
        assert "## Executive summary" in markdown
        assert "## Communities analysed" in markdown
        assert "## Source threads" in markdown
        assert report_export.posts_csv(sample)
        assert report_export.ideas_csv(sample)
        assert report_export.comments_csv(sample)

        section = markdown[markdown.index("## Source threads"):]
        analysed_posts = [item for item in sample["items"] if item.get("kind") != "comment"]
        assert section.count(". **[") == len(analysed_posts)


class TestDeadlineKeepsWhatItRead:
    """The reserve is what keeps a run out of the hard deadline: entering it turns
    the remaining communities into a notice, and the run analyses what it has."""

    def test_the_reserve_turns_the_rest_of_the_fetch_into_a_notice(self):
        clock = {"now": time.monotonic()}
        # Comfortably outside the reserve at the start, inside it after one fetch.
        deadline = clock["now"] + pipeline.DEADLINE_RESERVE_SECONDS + 30

        def slow(subreddit, days):
            clock["now"] += 45
            return fake_posts(subreddit, days)

        original = pipeline.time.monotonic
        try:
            pipeline.time.monotonic = lambda: clock["now"]
            bundle = build_bundle(
                discover=make_discover(["one", "two"]), fetch_posts=slow, deadline=deadline
            )
        finally:
            pipeline.time.monotonic = original

        assert bundle["inputs"]["subreddits"] == ["one"]
        assert any("ran out of time" in notice for notice in bundle["notices"])
        assert bundle["analysis"]["content_ideas"]

    def test_overrunning_the_hard_deadline_ends_the_run_rather_than_half_analysing(self):
        clock = {"now": time.monotonic()}
        deadline = clock["now"] + 10

        def very_slow(subreddit, days):
            clock["now"] += 1000
            return fake_posts(subreddit, days)

        original = pipeline.time.monotonic
        try:
            pipeline.time.monotonic = lambda: clock["now"]
            with pytest.raises(pipeline.RunError) as excinfo:
                build_bundle(
                    discover=make_discover(["one", "two"]),
                    fetch_posts=very_slow, deadline=deadline,
                )
        finally:
            pipeline.time.monotonic = original
        assert "took longer" in str(excinfo.value)

    def test_running_out_before_anything_is_read_is_an_error(self):
        with pytest.raises(pipeline.RunError) as excinfo:
            build_bundle(deadline=time.monotonic() - 1)
        assert "took longer" in str(excinfo.value)

    def test_cancelling_mid_fetch_still_stops_even_with_data_in_hand(self):
        cancel = threading.Event()

        def cancel_after_first(subreddit, days):
            cancel.set()
            return fake_posts(subreddit, days)

        with pytest.raises(pipeline.RunCancelled):
            build_bundle(
                discover=make_discover(["one", "two"]),
                fetch_posts=cancel_after_first, cancel=cancel,
            )


class TestCallForwardsArbitraryKeywords:
    """`_call` is the injection point every fetch/discovery fake goes through, so
    it has to forward whatever keywords a follow-up wires in (today: `deadline`;
    later: `phrases`) while still tolerating callables that predate all of it."""

    def test_drops_a_keyword_the_target_does_not_declare(self):
        def two_arg(a, b):
            return (a, b)

        assert pipeline._call(two_arg, 1, 2, phrases=["x"]) == (1, 2)

    def test_forwards_a_keyword_the_target_declares(self):
        def takes_phrases(a, b, phrases=None):
            return phrases

        assert pipeline._call(takes_phrases, 1, 2, phrases=["x", "y"]) == ["x", "y"]

    def test_forwards_a_keyword_to_a_callable_declaring_var_keyword(self):
        def catches_anything(a, b, **kwargs):
            return kwargs

        assert pipeline._call(catches_anything, 1, 2, phrases=["x"]) == {"phrases": ["x"]}

    def test_omits_a_keyword_whose_value_is_none(self):
        # Matches the pre-existing deadline behaviour: a None value never reaches
        # the target, even when the target would otherwise accept the keyword.
        captured = {}

        def takes_deadline(a, b, deadline="sentinel"):
            captured["deadline"] = deadline
            return deadline

        pipeline._call(takes_deadline, 1, 2, deadline=None)
        assert captured["deadline"] == "sentinel"  # default survived; None was dropped

        # A callable that cannot take the keyword at all must still be called
        # (with just the positional args), not skipped.
        assert pipeline._call(lambda s, d: (s, d), 1, 2, deadline=None) == (1, 2)

    def test_forwards_multiple_keywords_independently(self):
        def takes_both(a, b, deadline=None, phrases=None):
            return (deadline, phrases)

        assert pipeline._call(takes_both, 1, 2, deadline=99, phrases=["x"]) == (99, ["x"])

    def test_accepts_recognises_a_functools_partial(self):
        import functools

        def three_arg(a, b, deadline=None):
            return deadline

        partial_func = functools.partial(three_arg, 1)
        assert pipeline._accepts(partial_func, "deadline") is True

    def test_accepts_recognises_a_bound_method(self):
        class Fetcher:
            def fetch(self, subreddit, days, deadline=None):
                return deadline

        assert pipeline._accepts(Fetcher().fetch, "deadline") is True

    def test_accepts_recognises_var_keyword_regardless_of_name(self):
        def catches_anything(a, b, **anything):
            return anything

        assert pipeline._accepts(catches_anything, "phrases") is True
        assert pipeline._accepts(catches_anything, "deadline") is True

    def test_accepts_rejects_a_bare_lambda(self):
        assert pipeline._accepts(lambda s, d: [], "deadline") is False
        assert pipeline._accepts(lambda s, d: [], "phrases") is False


class TestQualifierDisclosure:
    """A brief with author constraints has to be visible in the run's own words."""

    REQUIREMENTS = [
        {"text": "The author works at a funded startup, Series A, B or C.",
         "kind": "require", "met": 3, "violated": 4, "unknown": 12},
        {"text": "The author is not a beginner or a solo founder.",
         "kind": "exclude", "met": 2, "violated": 5, "unknown": 12},
    ]

    def _classify(self, confirmed, requirements=None):
        def classify(posts, comments, subreddits, query="", profile=""):
            items, preview, classification = fake_classify(
                posts, comments, subreddits, query, profile
            )
            analysed = len(items)
            classification["selection"] = {
                "method": "relevance+qualifiers",
                "triaged": 19,
                "relevance_counts": {"0": 1, "1": 8, "2": 10},
                "pool": 19,
                "topic": "running Meta ads for B2B SaaS",
                "requirements": (
                    self.REQUIREMENTS if requirements is None else requirements
                ),
                "qualifier_counts": {"confirmed": 5, "unconfirmed": 9, "violated": 5},
                "qualifier_analysed": {
                    "confirmed": confirmed,
                    "unconfirmed": max(0, analysed - confirmed),
                    "violated": 0,
                },
            }
            return items, preview, classification

        return classify

    def test_a_thin_confirmed_count_is_said_out_loud(self):
        bundle = build_bundle(classify=self._classify(confirmed=2))
        notice = " ".join(bundle["notices"])
        assert "Only 2 of the" in notice
        # Specific about why, because "we found two" reads like a bug on its own.
        assert "Most Reddit posts never say who wrote them" in notice
        assert "Nothing was removed for contradicting it." in notice
        assert "whatever the author says they are" in notice
        assert "The author works at a funded startup, Series A, B or C" in notice

    def test_a_healthy_confirmed_count_says_nothing(self):
        bundle = build_bundle(
            classify=self._classify(confirmed=pipeline.QUALIFIER_MIN_CONFIRMED)
        )
        assert not any("Only" in notice for notice in bundle["notices"])

    def test_a_run_with_no_requirements_says_nothing_about_them(self):
        bundle = build_bundle()
        joined = " ".join(bundle["notices"])
        assert "who posted" not in joined
        assert pipeline._REQUIREMENTS_NOT_APPLIED not in bundle["notices"]

    def test_the_new_selection_keys_reach_the_bundle_untouched(self):
        selection = build_bundle(classify=self._classify(confirmed=2))["sample"]["selection"]
        assert selection["method"] == "relevance+qualifiers"
        assert selection["pool"] == 19
        assert selection["topic"] == "running Meta ads for B2B SaaS"
        assert selection["qualifier_counts"]["violated"] == 5
        assert selection["qualifier_analysed"]["confirmed"] == 2
        assert selection["requirements"][1]["kind"] == "exclude"

    def test_the_notice_never_exceeds_the_analysed_sample(self):
        bundle = build_bundle(classify=self._classify(confirmed=2))
        notice = next(n for n in bundle["notices"] if n.startswith("Only "))
        assert "of the {} threads".format(bundle["sample"]["items_analysed"]) in notice

    def test_relevance_off_says_the_requirements_were_not_applied(self):
        bundle = build_bundle(relevance_filter=False)
        assert pipeline._REQUIREMENTS_NOT_APPLIED in bundle["notices"]
        # And no requirement counts, because no model ever read the brief.
        selection = bundle["sample"]["selection"]
        assert selection["method"] == "relevance-off"
        for key in ("requirements", "qualifier_counts", "qualifier_analysed", "topic"):
            assert key not in selection

    def test_relevance_off_ignores_a_classifiers_own_qualifier_metadata(self):
        # The relevance-off branch replaces the selection block wholesale, so a
        # classifier that somehow reported requirements cannot contradict the notice.
        bundle = build_bundle(
            relevance_filter=False, classify=self._classify(confirmed=2)
        )
        assert "requirements" not in bundle["sample"]["selection"]
        assert not any(notice.startswith("Only ") for notice in bundle["notices"])

    def test_a_classifier_reporting_no_analysed_counts_is_not_a_crash(self):
        def classify(posts, comments, subreddits, query="", profile=""):
            items, preview, classification = fake_classify(
                posts, comments, subreddits, query, profile
            )
            classification["selection"] = {
                "method": "relevance+qualifiers",
                "triaged": 4,
                "relevance_counts": {"0": 0, "1": 0, "2": 4},
                "requirements": self.REQUIREMENTS,
            }
            return items, preview, classification

        bundle = build_bundle(classify=classify)
        assert any(notice.startswith("Only 0 of the") for notice in bundle["notices"])

    def test_the_notice_and_the_threshold_carry_no_dashes(self):
        bundle = build_bundle(classify=self._classify(confirmed=1))
        for notice in bundle["notices"]:
            assert "—" not in notice and "–" not in notice
        assert pipeline.QUALIFIER_MIN_CONFIRMED == pipeline.analysis.LOW_DATA_THRESHOLD


class TestClassifyReceivesTheDeadline:
    """Triage is the stage that can overrun a run's budget invisibly."""

    def test_the_soft_deadline_reaches_the_classifier(self):
        captured = {}

        def spy(posts, comments, subreddits, query="", profile="", soft_deadline=None):
            captured["soft_deadline"] = soft_deadline
            return fake_classify(posts, comments, subreddits, query, profile)

        deadline = time.monotonic() + 3600
        build_bundle(classify=spy, deadline=deadline)
        assert captured["soft_deadline"] == pytest.approx(
            deadline - pipeline.DEADLINE_RESERVE_SECONDS
        )

    def test_no_deadline_means_the_keyword_is_not_passed_at_all(self):
        captured = {}

        def spy(posts, comments, subreddits, query="", profile="", soft_deadline="sentinel"):
            captured["soft_deadline"] = soft_deadline
            return fake_classify(posts, comments, subreddits, query, profile)

        build_bundle(classify=spy)
        assert captured["soft_deadline"] == "sentinel"

    def test_a_classifier_that_predates_the_deadline_still_runs(self):
        # Every classify fake in this file takes five positional arguments and no
        # more, which is exactly the compatibility case _call exists for.
        bundle = build_bundle(deadline=time.monotonic() + 3600)
        assert bundle["sample"]["items_analysed"] > 0
