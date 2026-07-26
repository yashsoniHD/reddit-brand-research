"""Sanitisation: what happens when the model returns something other than the contract.

Every case here is a shape a real LLM has produced at some point — nulls, strings
where ints belong, indices that point nowhere, and markdown lifted out of a Reddit
post body. None of it may reach the UI.
"""

import pytest

import analysis
import discovery


class TestStripMarkdown:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("plain text", "plain text"),
            ("![alt](https://evil.example/x.png)", "alt"),
            ("[click here](https://evil.example)", "click here"),
            ("see ![](https://evil.example/pixel.gif) here", "see here"),
            ("[a](u1) and [b](u2)", "a and b"),
            ("line\nbreak", "line break"),
            ("  padded  ", "padded"),
        ],
    )
    def test_flattens_links_and_images(self, raw, expected):
        assert analysis.strip_markdown(raw) == expected

    @pytest.mark.parametrize("raw,expected", [(None, ""), (12, "12"), ({}, "{}")])
    def test_coerces_non_strings(self, raw, expected):
        assert analysis.strip_markdown(raw) == expected

    def test_no_url_survives_a_markdown_link(self):
        assert "http" not in analysis.strip_markdown("[x](http://evil.example/a)")

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("a — b", "a, b"),
            ("a – b", "a, b"),
            ("state—of", "state-of"),
            ("state–of", "state-of"),
            ("no dash here", "no dash here"),
            ("a — b — c", "a, b, c"),
        ],
    )
    def test_em_and_en_dashes_are_normalised(self, raw, expected):
        assert analysis.strip_markdown(raw) == expected

    def test_no_em_or_en_dash_survives_any_input(self):
        result = analysis.strip_markdown("state—of the art — a breakthrough–ish idea")
        assert "—" not in result and "–" not in result


class TestSanitizeClassification:
    def test_per_item_assignments_build_topic_indices(self):
        raw = {
            "topics": [{"id": 1, "label": "Losing track of tasks"}, {"id": 2, "label": "Tool fatigue"}],
            "items": [
                {"i": 1, "t": 1, "s": "neg"},
                {"i": 2, "t": 1, "s": "neu"},
                {"i": 3, "t": 2, "s": "pos"},
                {"i": 4, "t": 0, "s": "neu"},
            ],
        }
        result = analysis.sanitize_classification(raw, n_items=4)
        assert [topic["item_indices"] for topic in result["topics"]] == [[1, 2], [3]]
        assert len(result["item_sentiments"]) == 4

    def test_legacy_topic_index_lists_still_work(self):
        raw = {
            "topics": [{"id": 1, "label": "Old shape", "item_indices": [1, 2]}],
            "item_sentiments": [{"i": 1, "s": "pos"}],
        }
        result = analysis.sanitize_classification(raw, n_items=3)
        assert result["topics"][0]["item_indices"] == [1, 2]
        assert result["item_sentiments"] == [{"i": 1, "s": "pos"}]

    @pytest.mark.parametrize("raw", [None, {}, [], "text", 5, {"topics": None, "items": None}])
    def test_missing_and_wrong_shaped_output_gives_empty_defaults(self, raw):
        result = analysis.sanitize_classification(raw, n_items=10)
        assert result == {"topics": [], "item_sentiments": []}

    def test_drops_topics_with_no_resolvable_evidence(self):
        raw = {
            "topics": [{"id": 1, "label": "Real"}, {"id": 2, "label": "Empty"}],
            "items": [{"i": 1, "t": 1, "s": "pos"}],
        }
        result = analysis.sanitize_classification(raw, n_items=2)
        assert [topic["label"] for topic in result["topics"]] == ["Real"]

    def test_drops_unlabelled_topics(self):
        raw = {"topics": [{"id": 1, "label": None}, {"id": 2}], "items": [{"i": 1, "t": 1, "s": "pos"}]}
        assert analysis.sanitize_classification(raw, 2)["topics"] == []

    def test_out_of_range_and_wrong_typed_indices_are_dropped(self):
        raw = {
            "topics": [{"id": 1, "label": "T"}],
            "items": [
                {"i": 1, "t": 1, "s": "pos"},
                {"i": 0, "t": 1, "s": "pos"},
                {"i": 99, "t": 1, "s": "pos"},
                {"i": "2", "t": 1, "s": "pos"},
                {"i": True, "t": 1, "s": "pos"},
                {"i": None, "t": 1, "s": "pos"},
                "junk",
            ],
        }
        result = analysis.sanitize_classification(raw, n_items=3)
        assert result["topics"][0]["item_indices"] == [1]
        assert result["item_sentiments"] == [{"i": 1, "s": "pos"}]

    def test_duplicate_item_entries_are_ignored_after_the_first(self):
        raw = {
            "topics": [{"id": 1, "label": "T"}],
            "items": [{"i": 1, "t": 1, "s": "pos"}, {"i": 1, "t": 1, "s": "neg"}],
        }
        result = analysis.sanitize_classification(raw, 2)
        assert result["item_sentiments"] == [{"i": 1, "s": "pos"}]
        assert result["topics"][0]["item_indices"] == [1]

    def test_string_topic_ids_are_tolerated(self):
        raw = {"topics": [{"id": 1, "label": "T"}], "items": [{"i": 1, "t": "1", "s": "neu"}]}
        assert analysis.sanitize_classification(raw, 2)["topics"][0]["item_indices"] == [1]

    def test_unknown_sentiment_labels_are_dropped_but_the_item_still_counts_for_topics(self):
        raw = {"topics": [{"id": 1, "label": "T"}], "items": [{"i": 1, "t": 1, "s": "mixed"}]}
        result = analysis.sanitize_classification(raw, 2)
        assert result["item_sentiments"] == []
        assert result["topics"][0]["item_indices"] == [1]

    def test_markdown_in_labels_is_stripped(self):
        raw = {
            "topics": [{"id": 1, "label": "See ![x](http://evil.example/p.png) this"}],
            "items": [{"i": 1, "t": 1, "s": "neu"}],
        }
        label = analysis.sanitize_classification(raw, 2)["topics"][0]["label"]
        assert label == "See x this"

    def test_topic_count_is_capped(self):
        raw = {
            "topics": [{"id": i, "label": "Topic {}".format(i)} for i in range(1, 12)],
            "items": [{"i": i, "t": i, "s": "neu"} for i in range(1, 12)],
        }
        assert len(analysis.sanitize_classification(raw, 11)["topics"]) == analysis.MAX_TOPICS

    def test_missing_ids_fall_back_to_position(self):
        raw = {"topics": [{"label": "First"}], "items": [{"i": 1, "t": 1, "s": "neu"}]}
        result = analysis.sanitize_classification(raw, 2)
        assert result["topics"][0]["id"] == 1


class TestTopicDescriptionMatching:
    """A finding attached to the wrong topic is the worst failure this app can ship:
    the label and the prose contradict each other in the most quoted section."""

    TOPICS = [
        {"id": 1, "label": "Struggling with procrastination"},
        {"id": 2, "label": "Seeking productivity tools"},
        {"id": 3, "label": "Managing digital distractions"},
    ]

    def test_swapped_ids_are_corrected_by_the_echoed_label(self):
        # Exactly what the model produced in a real run: right labels, swapped ids.
        raw = {
            "topic_descriptions": [
                {"id": 2, "label": "Struggling with procrastination",
                 "finding": "People put tasks off for weeks."},
                {"id": 1, "label": "Seeking productivity tools",
                 "finding": "People want app recommendations."},
            ]
        }
        result = analysis.sanitize_synthesis(raw, 10, self.TOPICS)
        assert result["topic_descriptions"][1] == "People put tasks off for weeks."
        assert result["topic_descriptions"][2] == "People want app recommendations."

    def test_matching_is_case_and_whitespace_insensitive(self):
        raw = {"topic_descriptions": [
            {"id": 9, "label": "  SEEKING PRODUCTIVITY TOOLS  ", "finding": "Tools."}
        ]}
        result = analysis.sanitize_synthesis(raw, 10, self.TOPICS)
        assert result["topic_descriptions"] == {2: "Tools."}

    def test_correct_ids_and_labels_agree_and_still_work(self):
        raw = {"topic_descriptions": [
            {"id": 1, "label": "Struggling with procrastination", "finding": "A."},
            {"id": 2, "label": "Seeking productivity tools", "finding": "B."},
        ]}
        result = analysis.sanitize_synthesis(raw, 10, self.TOPICS)
        assert result["topic_descriptions"] == {1: "A.", 2: "B."}

    def test_unknown_label_falls_back_to_a_known_claimed_id(self):
        raw = {"topic_descriptions": [
            {"id": 3, "label": "Something the model invented", "finding": "C."}
        ]}
        assert analysis.sanitize_synthesis(raw, 10, self.TOPICS)["topic_descriptions"] == {3: "C."}

    def test_missing_label_falls_back_to_the_claimed_id(self):
        raw = {"topic_descriptions": [{"id": 2, "finding": "D."}]}
        assert analysis.sanitize_synthesis(raw, 10, self.TOPICS)["topic_descriptions"] == {2: "D."}

    def test_unknown_label_and_unknown_id_is_dropped(self):
        raw = {"topic_descriptions": [
            {"id": 99, "label": "Invented topic", "finding": "E."},
            {"label": "Also invented", "finding": "F."},
            {"id": "2", "label": "Invented again", "finding": "G."},
        ]}
        assert analysis.sanitize_synthesis(raw, 10, self.TOPICS)["topic_descriptions"] == {}

    def test_first_finding_for_a_topic_wins_over_later_duplicates(self):
        raw = {"topic_descriptions": [
            {"id": 1, "label": "Seeking productivity tools", "finding": "First."},
            {"id": 2, "label": "Seeking productivity tools", "finding": "Second."},
        ]}
        assert analysis.sanitize_synthesis(raw, 10, self.TOPICS)["topic_descriptions"] == {2: "First."}

    def test_markdown_in_a_label_still_matches_after_stripping(self):
        raw = {"topic_descriptions": [
            {"id": 1, "label": "[Seeking productivity tools](http://evil.example)",
             "finding": "H."}
        ]}
        assert analysis.sanitize_synthesis(raw, 10, self.TOPICS)["topic_descriptions"] == {2: "H."}

    def test_no_topics_supplied_keeps_whatever_ids_the_model_gave(self):
        raw = {"topic_descriptions": [{"id": 7, "label": "Anything", "finding": "I."}]}
        assert analysis.sanitize_synthesis(raw, 10)["topic_descriptions"] == {7: "I."}

    def test_empty_findings_are_dropped(self):
        raw = {"topic_descriptions": [
            {"id": 1, "label": "Struggling with procrastination", "finding": ""},
            {"id": 2, "label": "Seeking productivity tools", "finding": None},
        ]}
        assert analysis.sanitize_synthesis(raw, 10, self.TOPICS)["topic_descriptions"] == {}

    def test_malformed_topic_list_does_not_crash_matching(self):
        raw = {"topic_descriptions": [{"id": 1, "label": "x", "finding": "y"}]}
        for topics in ([None, "junk"], [{"id": None, "label": "x"}], [{"label": "no id"}]):
            assert isinstance(analysis.sanitize_synthesis(raw, 10, topics), dict)


class TestSanitizeSynthesis:
    def _raw(self):
        return {
            "executive_summary": "A [real](http://evil.example) takeaway.",
            "topic_descriptions": [
                {"id": 1, "label": "Losing context", "finding": "People keep losing context."},
                {"id": 99, "label": "Unknown", "finding": "Unknown topic."},
                {"id": 2, "label": "Tool fatigue", "finding": None},
                "junk",
            ],
            "pain_points": [
                {"text": "Too many tools", "item_indices": [1, 99, "2"]},
                {"text": None, "item_indices": [1]},
                "a bare string pain point",
            ],
            "questions": [{"text": "How do I start?", "item_indices": None}],
            "content_ideas": [
                {"title": "Guide", "format": None, "angle": "x", "rationale": "y",
                 "addresses": "Too many tools", "item_indices": [2]},
                {"title": None},
                {"title": "![img](http://evil.example/a.png)Clean"},
            ],
        }

    def test_coerces_everything_into_a_renderable_shape(self):
        topics = [{"id": 1, "label": "Losing context"}, {"id": 2, "label": "Tool fatigue"}]
        result = analysis.sanitize_synthesis(self._raw(), n_items=3, topics=topics)
        assert result["executive_summary"] == "A real takeaway."
        assert result["topic_descriptions"] == {1: "People keep losing context."}
        assert [claim["text"] for claim in result["pain_points"]] == [
            "Too many tools", "a bare string pain point"
        ]
        assert result["pain_points"][0]["item_indices"] == [1]
        assert result["questions"][0]["item_indices"] == []
        assert [idea["title"] for idea in result["content_ideas"]] == ["Guide", "imgClean"]
        assert result["content_ideas"][0]["format"] == "Content"

    @pytest.mark.parametrize("raw", [None, {}, [], "text", 7])
    def test_missing_output_gives_safe_defaults(self, raw):
        result = analysis.sanitize_synthesis(raw, n_items=5)
        assert result["executive_summary"] == ""
        assert result["topic_descriptions"] == {}
        assert result["pain_points"] == [] and result["questions"] == []
        assert result["content_ideas"] == []

    def test_unknown_topic_descriptions_are_dropped_only_when_topics_are_known(self):
        raw = {"topic_descriptions": [{"id": 7, "finding": "Something"}]}
        known = [{"id": 1, "label": "Only topic"}]
        assert analysis.sanitize_synthesis(raw, 5, known)["topic_descriptions"] == {}
        assert analysis.sanitize_synthesis(raw, 5)["topic_descriptions"] == {7: "Something"}

    def test_claims_are_capped(self):
        raw = {
            "pain_points": [{"text": "p{}".format(i)} for i in range(20)],
            "questions": [{"text": "q{}".format(i)} for i in range(20)],
            "content_ideas": [{"title": "i{}".format(i)} for i in range(20)],
        }
        result = analysis.sanitize_synthesis(raw, 5)
        assert len(result["pain_points"]) == analysis.MAX_PAIN_POINTS
        assert len(result["questions"]) == analysis.MAX_QUESTIONS
        assert len(result["content_ideas"]) == analysis.MAX_CONTENT_IDEAS

    def test_no_markdown_link_survives_any_field(self):
        raw = {
            "executive_summary": "[a](http://evil.example)",
            "pain_points": [{"text": "[b](http://evil.example)"}],
            "content_ideas": [{"title": "[c](http://evil.example)", "angle": "[d](http://evil.example)"}],
        }
        result = analysis.sanitize_synthesis(raw, 5)
        blob = repr(result)
        assert "http" not in blob and "](" not in blob


class TestIdeaHookSanitisation:
    def test_hook_markdown_is_stripped(self):
        raw = {"content_ideas": [
            {"title": "Guide", "hook": "See ![x](http://evil.example/p.png) this hook"}
        ]}
        result = analysis.sanitize_synthesis(raw, n_items=3, platform="linkedin")
        assert result["content_ideas"][0]["hook"] == "See x this hook"

    def test_hook_is_truncated_to_max_chars(self):
        raw = {"content_ideas": [{"title": "Guide", "hook": "x" * 200}]}
        result = analysis.sanitize_synthesis(raw, n_items=3, platform="linkedin")
        assert len(result["content_ideas"][0]["hook"]) == analysis.MAX_HOOK_CHARS

    def test_missing_hook_becomes_an_empty_string_not_a_dropped_idea(self):
        raw = {"content_ideas": [{"title": "Guide"}]}
        result = analysis.sanitize_synthesis(raw, n_items=3, platform="linkedin")
        assert result["content_ideas"][0]["hook"] == ""
        assert result["content_ideas"][0]["title"] == "Guide"

    def test_no_markdown_link_survives_a_hook(self):
        raw = {"content_ideas": [{"title": "Guide", "hook": "[a](http://evil.example)"}]}
        result = analysis.sanitize_synthesis(raw, n_items=3, platform="linkedin")
        assert "http" not in result["content_ideas"][0]["hook"]


class TestIdeaPlatformCoercion:
    def test_fixed_platform_overrides_whatever_the_model_said(self):
        raw = {"content_ideas": [
            {"title": "A", "platform": "instagram"},
            {"title": "B", "platform": "made up platform"},
            {"title": "C"},
        ]}
        result = analysis.sanitize_synthesis(raw, n_items=3, platform="linkedin")
        assert [idea["platform"] for idea in result["content_ideas"]] == ["linkedin"] * 3

    def test_fixed_instagram_run_overrides_a_linkedin_value_from_the_model(self):
        raw = {"content_ideas": [{"title": "A", "platform": "linkedin"}]}
        result = analysis.sanitize_synthesis(raw, n_items=3, platform="instagram")
        assert result["content_ideas"][0]["platform"] == "instagram"

    def test_both_respects_valid_model_supplied_values(self):
        raw = {"content_ideas": [
            {"title": "A", "platform": "instagram"},
            {"title": "B", "platform": "linkedin"},
        ]}
        result = analysis.sanitize_synthesis(raw, n_items=3, platform="both")
        assert [idea["platform"] for idea in result["content_ideas"]] == ["instagram", "linkedin"]

    def test_both_defaults_invalid_or_missing_values_to_linkedin(self):
        raw = {"content_ideas": [
            {"title": "A", "platform": "tiktok"},
            {"title": "B", "platform": None},
            {"title": "C"},
        ]}
        result = analysis.sanitize_synthesis(raw, n_items=3, platform="both")
        assert [idea["platform"] for idea in result["content_ideas"]] == ["linkedin"] * 3

    def test_platform_matching_ignores_case_and_punctuation(self):
        raw = {"content_ideas": [{"title": "A", "platform": "  Instagram! "}]}
        result = analysis.sanitize_synthesis(raw, n_items=3, platform="both")
        assert result["content_ideas"][0]["platform"] == "instagram"

    def test_unspecified_run_platform_falls_back_to_the_models_answer_or_linkedin(self):
        # platform="" (the default) behaves like an unfixed run for coercion purposes.
        raw = {"content_ideas": [{"title": "A", "platform": "instagram"}, {"title": "B"}]}
        result = analysis.sanitize_synthesis(raw, n_items=3)
        assert [idea["platform"] for idea in result["content_ideas"]] == ["instagram", "linkedin"]


class TestSynthesisPrompt:
    """`_synthesis_prompt` is what actually reaches the model; sanitisation cannot
    fix a prompt that never asked for the right platform or the right query."""

    def test_prompt_contains_the_query(self):
        prompt = analysis._synthesis_prompt([], [], "skincare routines for oily skin", "", "linkedin", ["x"])
        assert "skincare routines for oily skin" in prompt

    def test_prompt_contains_the_platform_label(self):
        prompt = analysis._synthesis_prompt([], [], "q", "", "linkedin", ["x"])
        assert "LinkedIn" in prompt

    def test_fixed_platform_lists_only_that_platforms_formats(self):
        prompt = analysis._synthesis_prompt([], [], "q", "", "linkedin", ["x"])
        assert "Carousel (PDF)" in prompt
        assert "Reel script" not in prompt

    def test_both_platforms_lists_both_format_menus(self):
        prompt = analysis._synthesis_prompt([], [], "q", "", "both", ["x"])
        assert "Carousel (PDF)" in prompt
        assert "Reel script" in prompt

    def test_profile_line_is_present_only_when_profile_is_non_empty(self):
        without_profile = analysis._synthesis_prompt([], [], "q", "", "linkedin", ["x"])
        with_profile = analysis._synthesis_prompt(
            [], [], "q", "I run a skincare brand", "linkedin", ["x"]
        )
        assert "They describe themselves as" not in without_profile
        assert "They describe themselves as: I run a skincare brand" in with_profile

    def test_fixed_platform_rule_names_that_platform_for_every_idea(self):
        prompt = analysis._synthesis_prompt([], [], "q", "", "instagram", ["x"])
        assert '"instagram" for every idea' in prompt

    def test_both_platform_rule_asks_the_model_to_choose(self):
        prompt = analysis._synthesis_prompt([], [], "q", "", "both", ["x"])
        assert '"platform" is "linkedin" or "instagram". Choose whichever feed' in prompt

    def test_no_prompt_contains_an_em_or_en_dash(self):
        # The prompts forbid the model from using dashes; they should not model the
        # habit themselves, in their own instructional text or example strings.
        classification = analysis._classification_prompt([], ["x"])
        synthesis = analysis._synthesis_prompt([], [], "q", "", "linkedin", ["x"])
        discovery_prompt = discovery._discovery_prompt("q", "")
        for prompt in (classification, synthesis, discovery_prompt):
            assert "—" not in prompt and "–" not in prompt

    def test_classification_and_synthesis_prompts_state_the_no_dash_rule(self):
        classification = analysis._classification_prompt([], ["x"])
        synthesis = analysis._synthesis_prompt([], [], "q", "", "linkedin", ["x"])
        for prompt in (classification, synthesis):
            assert "em dash" in prompt and "en dash" in prompt

    def test_discovery_prompt_states_the_no_dash_rule(self):
        prompt = discovery._discovery_prompt("q", "")
        assert "em dash" in prompt and "en dash" in prompt


class TestSynthesizePublicCall:
    def test_forwards_query_and_platform_into_the_prompt_and_sanitises_the_result(self, monkeypatch):
        captured = {}

        def fake_chat_json(prompt, max_tokens, label):
            captured["prompt"] = prompt
            captured["label"] = label
            return (
                {"content_ideas": [{"title": "A", "hook": "Stop scrolling", "platform": "instagram"}]},
                "test-model",
            )

        monkeypatch.setattr(analysis, "_chat_json", fake_chat_json)
        result = analysis.synthesize([], [], "skincare routines", "", "linkedin", ["skincareaddiction"])

        assert result["model"] == "test-model"
        assert captured["label"] == "synthesis"
        assert "skincare routines" in captured["prompt"]
        # The run is fixed to LinkedIn, so the model's stray "instagram" is overridden.
        assert result["content_ideas"][0]["platform"] == "linkedin"
        assert result["content_ideas"][0]["hook"] == "Stop scrolling"


class _FakeCandidate:
    def __init__(self, finish="STOP"):
        self.finish_reason = finish


class _FakeResponse:
    """Shape of a real Vertex response: `.text` and `.candidates[0].finish_reason`."""

    def __init__(self, text, finish="STOP"):
        self.text = text
        self.candidates = [_FakeCandidate(finish)]


class _FakeModels:
    """Queues one body per call, repeating the last one past the end of the list."""

    def __init__(self, bodies):
        self.bodies = list(bodies)
        self.calls = 0

    def generate_content(self, model, contents, config):
        self.calls += 1
        body = self.bodies[min(self.calls - 1, len(self.bodies) - 1)]
        return _FakeResponse(body)


class _FakeClient:
    def __init__(self, bodies):
        self.models = _FakeModels(bodies)


class TestChatJsonRetriesMalformedPayloads:
    """Regression coverage for the retryable-JSON-parse path in `_chat_json`.

    Gemini occasionally returns a payload that is not quite valid JSON (a trailing
    comma, an unquoted key) or a body that parses but is not the object shape asked
    for (a top-level list, an empty string). Before this, any of those was fatal on
    the first attempt, throwing away an otherwise-good multi-second call. Now a
    trailing comma is repaired in place (no retry spent), and anything else
    unparseable is retried like a transient failure, up to MAX_ATTEMPTS.
    """

    @pytest.fixture(autouse=True)
    def _no_sleep(self, monkeypatch):
        # These tests deliberately exhaust MAX_ATTEMPTS in one case; without this the
        # real RETRY_DELAYS_SECONDS backoff would slow the suite for no reason.
        monkeypatch.setattr(analysis.time, "sleep", lambda seconds: None)

    def test_trailing_comma_is_repaired_without_spending_a_retry(self, monkeypatch):
        client = _FakeClient(['{"a": 1,}', '{"a": 1}'])
        monkeypatch.setattr(analysis, "_client", client)

        parsed, model = analysis.chat_json("prompt", 100, "demo")

        assert parsed == {"a": 1}
        assert model == analysis.PRIMARY_MODEL
        # The second, valid body must never have been needed.
        assert client.models.calls == 1

    def test_unquoted_key_then_valid_body_is_healed_by_one_retry(self, monkeypatch):
        client = _FakeClient(['{\n  ok: true\n}', '{"ok": true}'])
        monkeypatch.setattr(analysis, "_client", client)

        parsed, model = analysis.chat_json("prompt", 100, "demo")

        assert parsed == {"ok": True}
        assert client.models.calls == 2

    def test_garbage_every_attempt_raises_the_incomplete_message_after_max_attempts(self, monkeypatch):
        client = _FakeClient(["not json at all"])
        monkeypatch.setattr(analysis, "_client", client)

        with pytest.raises(ValueError) as excinfo:
            analysis.chat_json("prompt", 100, "demo")

        assert str(excinfo.value) == analysis._INCOMPLETE
        assert "came back incomplete" in str(excinfo.value)
        assert client.models.calls == analysis.MAX_ATTEMPTS

    def test_top_level_list_is_retried_as_wrong_shape_not_accepted(self, monkeypatch):
        client = _FakeClient(["[1, 2, 3]", '{"ok": 1}'])
        monkeypatch.setattr(analysis, "_client", client)

        parsed, model = analysis.chat_json("prompt", 100, "demo")

        assert parsed == {"ok": 1}
        assert client.models.calls == 2

    def test_empty_body_then_valid_body_is_retried_and_succeeds(self, monkeypatch):
        client = _FakeClient(["", '{"ok": 1}'])
        monkeypatch.setattr(analysis, "_client", client)

        parsed, model = analysis.chat_json("prompt", 100, "demo")

        assert parsed == {"ok": 1}
        assert client.models.calls == 2

    @pytest.mark.parametrize(
        "text",
        [
            '{"a": [1, 2]}',
            '{"s": "a, ] literal"}',
            '{"n": {"m": [1]}}',
            '{"s": "trailing comma, in a string,}"}',
        ],
    )
    def test_repair_regex_never_alters_json_that_strict_parsing_already_accepts(self, text):
        # No fake client needed: this exercises _parse_json_object directly, proving
        # the repair pass is only ever consulted after strict json.loads has failed.
        assert analysis._parse_json_object(text) == analysis.json.loads(text)


class TestErrorMapping:
    def test_quota_is_recognised_and_reported_with_its_reset_time(self):
        exc = Exception(
            "429 RESOURCE_EXHAUSTED. Quota exceeded for aiplatform.googleapis.com. "
            "Please try again in 22m21.792s."
        )
        message = analysis.quota_message(exc)
        assert message.startswith("The analysis service has reached its rate limit")
        assert "22m" in message
        assert "429" not in message and "http" not in message
        # Capacity is a service fact to a buyer, not an API-key problem to debug.
        for jargon in ["API", "api key", "aiplatform", "resource_exhausted", "quota exceeded"]:
            assert jargon.lower() not in message.lower()

    def test_non_quota_errors_are_not_treated_as_quota(self):
        assert analysis.quota_message(Exception("some other transient error")) is None

    def test_quota_without_a_hint_still_reads_as_a_sentence(self):
        message = analysis.quota_message(Exception("rate limit exceeded"))
        assert message.endswith(".")

    def test_payload_too_large_is_detected_from_code_and_text(self):
        class Code413(Exception):
            code = 413

        assert analysis._is_payload_too_large(Code413("nope"))
        assert analysis._is_payload_too_large(Exception("Request too large for model"))
        assert not analysis._is_payload_too_large(Exception("something else"))

    def test_retry_wait_is_capped_even_when_the_provider_asks_for_longer(self):
        class WithHeaders(Exception):
            response = type("R", (), {"headers": {"retry-after": "600"}})()

        wait = analysis._retry_wait_seconds(WithHeaders("busy"), 0)
        assert wait == analysis.MAX_RATE_LIMIT_WAIT_SECONDS

    def test_retry_wait_falls_back_to_the_default_ladder(self):
        assert analysis._retry_wait_seconds(Exception("no headers"), 0) == analysis.RETRY_DELAYS_SECONDS[0]


class TestSelectAndFormatItems:
    def _posts(self, count):
        return [
            {"kind": "post", "title": "post {}".format(i), "body": "b" * 500,
             "score": i, "created_utc": 1700000000 + i}
            for i in range(count)
        ]

    def _comments(self, count):
        return [
            {"kind": "comment", "title": "", "body": "c" * 500, "score": i}
            for i in range(count)
        ]

    def test_posts_come_first_in_fetch_order_comments_by_score(self):
        items = analysis.select_items(self._posts(3), self._comments(3))
        assert [item["kind"] for item in items] == ["post"] * 3 + ["comment"] * 3
        assert [item["score"] for item in items[3:]] == [2, 1, 0]

    def test_caps_are_respected(self):
        items = analysis.select_items(self._posts(400), self._comments(400))
        assert len(items) == analysis.MAX_POSTS_ANALYSED + analysis.MAX_COMMENTS_ANALYSED

    def test_custom_caps_shrink_the_sample(self):
        assert len(analysis.select_items(self._posts(50), self._comments(50), 5, 3)) == 8

    def test_previews_are_truncated(self):
        listing = analysis.format_items(self._posts(1), preview_chars=20)
        assert "b" * 20 in listing and "b" * 21 not in listing

    def test_numbering_is_preserved_when_only_a_subset_is_listed(self):
        items = self._posts(5)
        listing = analysis.format_items(items, 50, indices=[2, 4])
        assert listing.startswith("2. ")
        assert "4. " in listing and "1. " not in listing and "3. " not in listing

    def test_empty_sample_is_rejected_loudly(self):
        with pytest.raises(ValueError):
            analysis.classify_items([], ["x"])


class TestTokenBudget:
    def test_estimate_scales_with_length(self):
        assert analysis.estimate_tokens("a" * 400) == 101
        assert analysis.estimate_tokens("") == 1
        assert analysis.estimate_tokens(None) == 1

    def test_fit_sample_uses_the_full_sample_when_the_budget_is_generous(self):
        posts = [
            {"kind": "post", "title": "t", "body": "b" * 200, "score": 1, "created_utc": 1}
            for _ in range(200)
        ]
        items, preview = analysis.fit_sample(posts, [], ["x"], budget=1000000)
        assert len(items) == analysis.MAX_POSTS_ANALYSED
        assert preview == analysis.BODY_PREVIEW_CHARS

    def test_fit_sample_shrinks_to_a_tight_budget(self):
        posts = [
            {"kind": "post", "title": "t" * 100, "body": "b" * 500, "score": 1, "created_utc": 1}
            for _ in range(300)
        ]
        items, preview = analysis.fit_sample(posts, [], ["x"], budget=4000)
        assert 0 < len(items) < analysis.MAX_POSTS_ANALYSED
        assert preview <= analysis.BODY_PREVIEW_CHARS

    def test_fit_sample_never_returns_nothing_when_there_is_data(self):
        posts = [
            {"kind": "post", "title": "t" * 300, "body": "b" * 5000, "score": 1, "created_utc": 1}
            for _ in range(300)
        ]
        items, _ = analysis.fit_sample(posts, [], ["x"], budget=1)
        assert items

    def test_learned_limit_is_used_for_the_budget(self, monkeypatch):
        monkeypatch.setattr(analysis, "_token_limit", 12000)
        assert analysis.token_budget() == int(12000 * analysis.BUDGET_SAFETY)

    def test_capacity_limited_is_false_when_the_full_sample_was_used(self):
        posts = [{"kind": "post", "score": 1} for _ in range(10)]
        comments = [{"kind": "comment", "score": 1} for _ in range(5)]
        items = posts + comments
        assert not analysis.is_capacity_limited(
            items, analysis.BODY_PREVIEW_CHARS, posts, comments
        )

    def test_capacity_limited_is_true_when_the_budget_shrank_the_sample(self):
        posts = [{"kind": "post", "score": 1} for _ in range(200)]
        comments = [{"kind": "comment", "score": 1} for _ in range(200)]
        items = posts[:40] + comments[:40]
        assert analysis.is_capacity_limited(items, 110, posts, comments)

    def test_capacity_limited_is_true_when_only_the_preview_was_trimmed(self):
        posts = [{"kind": "post", "score": 1} for _ in range(5)]
        assert analysis.is_capacity_limited(posts, 100, posts, [])

    def test_output_reservation_never_exceeds_the_ceiling(self):
        assert analysis._classification_output_tokens(100000) == analysis.CLASSIFY_MAX_TOKENS
        assert analysis._classification_output_tokens(0) > 0
