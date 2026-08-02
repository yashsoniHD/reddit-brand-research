"""Sanitisation: what happens when the model returns something other than the contract.

Every case here is a shape a real LLM has produced at some point — nulls, strings
where ints belong, indices that point nowhere, and markdown lifted out of a Reddit
post body. None of it may reach the UI.
"""

import threading
import time

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

    def test_a_topic_claiming_the_reserved_id_zero_is_dropped(self):
        # 0 is the prompt's "fits no topic" sentinel. A topic that claims it would
        # collect every unassigned item in the sample.
        raw = {
            "topics": [{"id": 0, "label": "Everything else"}, {"id": 1, "label": "Real"}],
            "items": [
                {"i": 1, "t": 0, "s": "neu"},
                {"i": 2, "t": 0, "s": "neu"},
                {"i": 3, "t": 1, "s": "pos"},
            ],
        }
        result = analysis.sanitize_classification(raw, 3)
        assert [topic["label"] for topic in result["topics"]] == ["Real"]
        assert result["topics"][0]["item_indices"] == [3]

    def test_items_marked_t_zero_stay_unassigned(self):
        raw = {
            "topics": [{"id": 1, "label": "Real"}],
            "items": [{"i": 1, "t": 0, "s": "neu"}, {"i": 2, "t": 1, "s": "neu"}],
        }
        result = analysis.sanitize_classification(raw, 2)
        assert result["topics"][0]["item_indices"] == [2]
        # The unassigned item is still labelled for sentiment; it just has no topic.
        assert len(result["item_sentiments"]) == 2

    def test_a_topic_with_id_zero_does_not_displace_a_real_topic_one(self):
        raw = {
            "topics": [{"id": 0, "label": "Sentinel"}, {"id": 1, "label": "Real"}],
            "items": [{"i": 1, "t": 1, "s": "neu"}],
        }
        result = analysis.sanitize_classification(raw, 2)
        assert [(t["id"], t["label"]) for t in result["topics"]] == [(1, "Real")]


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
                {"text": "Too many tools to keep track of every week",
                 "item_indices": [1, 99, "2"]},
                {"text": None, "item_indices": [1]},
                "a bare string pain point with enough words in it",
            ],
            "questions": [
                {"text": "How do I start consolidating all of this?", "item_indices": None}
            ],
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
            "Too many tools to keep track of every week",
            "a bare string pain point with enough words in it",
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
            "pain_points": [{"text": "A distinct pain point number {}".format(i)} for i in range(20)],
            "questions": [{"text": "A distinct question number {}?".format(i)} for i in range(20)],
            "content_ideas": [{"title": "i{}".format(i)} for i in range(30)],
        }
        result = analysis.sanitize_synthesis(raw, 5)
        assert len(result["pain_points"]) == analysis.MAX_PAIN_POINTS
        assert len(result["questions"]) == analysis.MAX_QUESTIONS
        assert len(result["content_ideas"]) == analysis.SANITIZE_MAX_CONTENT_IDEAS

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
        result = analysis.sanitize_synthesis(raw, n_items=3)
        assert result["content_ideas"][0]["hook"] == "See x this hook"

    def test_hook_is_truncated_to_max_chars(self):
        raw = {"content_ideas": [{"title": "Guide", "hook": "x" * 200}]}
        result = analysis.sanitize_synthesis(raw, n_items=3)
        assert len(result["content_ideas"][0]["hook"]) == analysis.MAX_HOOK_CHARS

    def test_missing_hook_becomes_an_empty_string_not_a_dropped_idea(self):
        raw = {"content_ideas": [{"title": "Guide"}]}
        result = analysis.sanitize_synthesis(raw, n_items=3)
        assert result["content_ideas"][0]["hook"] == ""
        assert result["content_ideas"][0]["title"] == "Guide"

    def test_no_markdown_link_survives_a_hook(self):
        raw = {"content_ideas": [{"title": "Guide", "hook": "[a](http://evil.example)"}]}
        result = analysis.sanitize_synthesis(raw, n_items=3)
        assert "http" not in result["content_ideas"][0]["hook"]

    def test_ideas_never_carry_a_platform_field(self):
        # Platform is gone from the product surface; sanitisation must not resurrect
        # it even if a stale prompt or a hand-built payload includes one.
        raw = {"content_ideas": [{"title": "Guide", "hook": "x", "platform": "instagram"}]}
        result = analysis.sanitize_synthesis(raw, n_items=3)
        assert "platform" not in result["content_ideas"][0]
        assert set(result["content_ideas"][0].keys()) == {
            "title", "hook", "format", "angle", "rationale", "addresses", "item_indices"
        }


class TestHookDeduplication:
    """Two ideas that open on the same words are one idea printed twice, which
    is exactly what a live run produced: ideas 02 and 14 shared a quote stem."""

    def test_a_second_idea_with_the_same_opening_is_dropped(self):
        raw = {"content_ideas": [
            {"title": "A", "hook": "Client feedback arrives in five different places every week."},
            {"title": "B", "hook": "Client feedback arrives in five different places, and I miss half."},
            {"title": "C", "hook": "Nobody told me the deadline moved until Friday afternoon."},
        ]}
        result = analysis.sanitize_synthesis(raw, n_items=3)
        assert [idea["title"] for idea in result["content_ideas"]] == ["A", "C"]

    def test_casing_and_punctuation_cannot_hide_a_repeat(self):
        raw = {"content_ideas": [
            {"title": "A", "hook": "Unlimited revisions is not standard. It is just unpriced."},
            {"title": "B", "hook": "UNLIMITED revisions, is not standard: it is never priced."},
        ]}
        result = analysis.sanitize_synthesis(raw, n_items=3)
        assert [idea["title"] for idea in result["content_ideas"]] == ["A"]

    def test_hooks_that_only_diverge_after_the_stem_are_kept(self):
        # The comparison is the opening, not the whole line: two hooks that
        # share a common noun but start differently are two ideas.
        raw = {"content_ideas": [
            {"title": "A", "hook": "Three clients, one calendar, and no idea who is waiting."},
            {"title": "B", "hook": "Nobody warned me that one calendar cannot hold three clients."},
        ]}
        result = analysis.sanitize_synthesis(raw, n_items=3)
        assert [idea["title"] for idea in result["content_ideas"]] == ["A", "B"]

    def test_several_ideas_with_no_hook_at_all_are_all_kept(self):
        # An empty hook has no stem, so it must not collide with the next
        # empty hook and quietly delete every idea after the first.
        raw = {"content_ideas": [{"title": "A"}, {"title": "B"}, {"title": "C"}]}
        result = analysis.sanitize_synthesis(raw, n_items=3)
        assert [idea["title"] for idea in result["content_ideas"]] == ["A", "B", "C"]


class TestHookProfileEcho:
    """A hook is either the community's voice or the author's. It is never the
    user's own description of their business, read back as if a Redditor said it."""

    PROFILE = "I run a Notion template shop for freelance designers."

    def test_a_hook_that_repeats_the_profile_is_dropped(self):
        raw = {"content_ideas": [
            {"title": "A", "hook": "I run a Notion template shop for freelance designers."},
            {"title": "B", "hook": "Nobody told me the deadline moved until Friday afternoon."},
        ]}
        result = analysis.sanitize_synthesis(raw, 3, profile=self.PROFILE)
        assert [idea["title"] for idea in result["content_ideas"]] == ["B"]

    def test_a_partial_run_of_the_profile_is_still_an_echo(self):
        raw = {"content_ideas": [
            {"title": "A", "hook": "Every Notion template shop I know undercharges by half."},
        ]}
        result = analysis.sanitize_synthesis(raw, 3, profile=self.PROFILE)
        assert result["content_ideas"] == []

    @pytest.mark.parametrize(
        "hook",
        [
            "Four designers, four different setups, and nobody agrees.",
            "My template broke the moment a second client opened it.",
            "Freelance work does not fail on skill. It fails on handover.",
        ],
    )
    def test_ordinary_hooks_that_share_a_word_with_the_profile_survive(self, hook):
        # The threshold is a run of at least three words and twelve characters,
        # so shared vocabulary alone is never enough to lose an idea.
        raw = {"content_ideas": [{"title": "A", "hook": hook}]}
        result = analysis.sanitize_synthesis(raw, 3, profile=self.PROFILE)
        assert len(result["content_ideas"]) == 1

    def test_a_hook_naming_the_users_own_product_is_dropped(self):
        # Straight from a live run: the model wrote the user's own product into
        # a first-person hook, which reads as a testimonial nobody gave.
        raw = {"content_ideas": [
            {"title": "A", "hook": "My Notion template for juggling clients keeps me sane."},
            {"title": "B", "hook": "Three deadlines moved and nobody thought to tell me."},
        ]}
        result = analysis.sanitize_synthesis(raw, 3, profile=self.PROFILE)
        assert [idea["title"] for idea in result["content_ideas"]] == ["B"]

    def test_brand_tokens_are_the_names_the_user_capitalised_mid_sentence(self):
        assert analysis.brand_tokens(self.PROFILE) == {"notion"}
        # A capital at the start of a sentence says nothing, and neither does a
        # profile written entirely in lower case.
        assert analysis.brand_tokens("Bookkeeping is what I do all day.") == set()
        assert analysis.brand_tokens("i run a notion template shop") == set()
        # Short words are skipped: a three-letter capital collides with too much.
        assert analysis.brand_tokens("I sell an API course for Django teams.") == {"django"}

    def test_no_profile_means_no_echo_check_at_all(self):
        raw = {"content_ideas": [{"title": "A", "hook": "I run a Notion template shop."}]}
        assert len(analysis.sanitize_synthesis(raw, 3)["content_ideas"]) == 1

    def test_the_echo_helper_needs_both_a_word_count_and_a_length(self):
        assert analysis.PROFILE_ECHO_MIN_WORDS == 3
        assert analysis.PROFILE_ECHO_MIN_CHARS == 12
        # Three words but only nine characters: too small to mean anything.
        assert not analysis._echoes("a b c today", "a b c yesterday")
        # Three words and twenty-seven characters: a run this long is a quote
        # of the brief, not a coincidence.
        assert analysis._echoes(
            "We asked twelve freelance graphic designers what they charge.",
            "I coach freelance graphic designers on pricing.",
        )


class TestSynthesisPrompt:
    """`_synthesis_prompt` is what actually reaches the model; sanitisation cannot
    fix a prompt that never asked for the right query."""

    def test_prompt_contains_the_query(self):
        prompt = analysis._synthesis_prompt([], [], "skincare routines for oily skin", "", ["x"])
        assert "skincare routines for oily skin" in prompt

    def test_the_prompt_names_no_channel_when_the_run_named_none(self):
        # The product used to write for LinkedIn implicitly. A channel-neutral run
        # must not name a channel at all, or every idea gets shaped for a place the
        # user never chose.
        prompt = analysis._synthesis_prompt([], [], "q", "", ["x"])
        assert "They publish on" not in prompt
        assert "a creator growing an audience" in prompt
        assert "stop a feed scroll" in prompt
        # Deliberately not a loop over every label. Two of them are substrings of
        # text the prompt legitimately contains: "X" appears in the hook rules'
        # own banned examples ("Struggling with X? Here is how."), and
        # "Newsletter" inside the format label "Newsletter issue". "LinkedIn" is
        # the one that actually regressed here, and it collides with nothing.
        assert "LinkedIn" not in prompt

    def test_the_prompt_names_the_channels_the_run_asked_for(self):
        prompt = analysis._synthesis_prompt(
            [], [], "q", "", ["x"], channels=["instagram", "tiktok"]
        )
        assert "They publish on: Instagram and TikTok." in prompt
        # And the hook rule stops talking about a generic feed.
        assert "stop a scroll on Instagram and TikTok" in prompt
        assert "LinkedIn" not in prompt

    def test_a_channel_narrows_the_format_menu_to_what_it_can_ship(self):
        prompt = analysis._synthesis_prompt([], [], "q", "", ["x"], channels=["tiktok"])
        assert '"Short-form video script" for EVERY idea' in prompt
        for absent in ("Poll", "Carousel", "Long-form article"):
            assert absent not in prompt

    def test_prompt_lists_the_flat_format_menu(self):
        prompt = analysis._synthesis_prompt([], [], "q", "", ["x"])
        for fmt in analysis.IDEA_FORMATS:
            assert fmt in prompt

    def test_prompt_has_no_platform_field_in_its_json_contract(self):
        prompt = analysis._synthesis_prompt([], [], "q", "", ["x"])
        assert '"platform"' not in prompt

    def test_profile_line_is_present_only_when_profile_is_non_empty(self):
        without_profile = analysis._synthesis_prompt([], [], "q", "", ["x"])
        with_profile = analysis._synthesis_prompt(
            [], [], "q", "I run a skincare brand", ["x"]
        )
        assert "They describe themselves as" not in without_profile
        assert "They describe themselves as: I run a skincare brand" in with_profile

    def test_no_prompt_contains_an_em_or_en_dash(self):
        # The prompts forbid the model from using dashes; they should not model the
        # habit themselves, in their own instructional text or example strings.
        classification = analysis._classification_prompt([], ["x"])
        synthesis = analysis._synthesis_prompt([], [], "q", "", ["x"])
        discovery_prompt = discovery._discovery_prompt("q", "")
        draft = analysis._draft_prompt({}, [], "q", "")
        for prompt in (classification, synthesis, discovery_prompt, draft):
            assert "—" not in prompt and "–" not in prompt

    def test_classification_and_synthesis_prompts_state_the_no_dash_rule(self):
        classification = analysis._classification_prompt([], ["x"])
        synthesis = analysis._synthesis_prompt([], [], "q", "", ["x"])
        for prompt in (classification, synthesis):
            assert "em dash" in prompt and "en dash" in prompt

    def test_discovery_prompt_states_the_no_dash_rule(self):
        prompt = discovery._discovery_prompt("q", "")
        assert "em dash" in prompt and "en dash" in prompt


class TestContentType:
    """When the user asks for formats, every idea comes back in one of them."""

    def test_the_prompt_names_the_one_format_and_drops_the_menu(self):
        prompt = analysis._synthesis_prompt(
            [], [], "q", "", ["x"], content_types=["carousel"]
        )
        assert '"Carousel" for EVERY idea' in prompt
        assert "Video script" not in prompt

    def test_no_content_type_keeps_the_full_format_menu(self):
        prompt = analysis._synthesis_prompt([], [], "q", "", ["x"])
        for fmt in analysis.IDEA_FORMATS:
            assert fmt in prompt

    def test_two_content_types_ask_for_both_and_for_an_even_spread(self):
        prompt = analysis._synthesis_prompt(
            [], [], "q", "", ["x"], content_types=["poll", "carousel"]
        )
        assert "Carousel" in prompt and "Poll" in prompt
        assert "roughly evenly" in prompt
        # The formats nobody asked for are not on the menu at all.
        assert "Video script" not in prompt and "Long-form article" not in prompt

    def test_one_content_type_does_not_ask_for_a_spread(self):
        prompt = analysis._synthesis_prompt(
            [], [], "q", "", ["x"], content_types=["poll"]
        )
        assert "roughly evenly" not in prompt

    def test_the_mixed_menu_does_not_ask_for_a_spread(self):
        assert "roughly evenly" not in analysis._synthesis_prompt([], [], "q", "", ["x"])

    @pytest.mark.parametrize(
        "content_type,expected",
        [("text", "Text post"), ("carousel", "Carousel"),
         ("video", "Video script"), ("poll", "Poll"), ("article", "Long-form article"),
         ("short_video", "Short-form video script"), ("thread", "Thread"),
         ("issue", "Newsletter issue")],
    )
    def test_every_idea_is_forced_to_the_chosen_format(self, content_type, expected):
        raw = {"content_ideas": [
            {"title": "A", "format": "Skywriting"},
            {"title": "B", "format": ""},
            {"title": "C"},
        ]}
        result = analysis.sanitize_synthesis(raw, 3, content_types=[content_type])
        assert [idea["format"] for idea in result["content_ideas"]] == [expected] * 3

    def test_an_unknown_content_type_leaves_the_model_format_alone(self):
        raw = {"content_ideas": [{"title": "A", "format": "Poll"}]}
        result = analysis.sanitize_synthesis(raw, 3, content_types=["reel"])
        assert result["content_ideas"][0]["format"] == "Poll"

    def test_an_idea_already_in_a_chosen_format_keeps_it(self):
        raw = {"content_ideas": [
            {"title": "A", "format": "Poll"},
            {"title": "B", "format": "Carousel"},
        ]}
        result = analysis.sanitize_synthesis(raw, 3, content_types=["carousel", "poll"])
        assert [idea["format"] for idea in result["content_ideas"]] == [
            "Poll", "Carousel"
        ]

    def test_the_model_may_name_a_chosen_format_by_its_wire_value(self):
        raw = {"content_ideas": [{"title": "A", "format": "carousel"}]}
        result = analysis.sanitize_synthesis(raw, 3, content_types=["carousel", "poll"])
        assert result["content_ideas"][0]["format"] == "Carousel"

    def test_an_idea_outside_the_set_goes_to_the_least_used_chosen_format(self):
        # Three carousels and a newsletter: the newsletter becomes the poll,
        # because that is the chosen format the set has used least.
        raw = {"content_ideas": [
            {"title": "A", "format": "Carousel"},
            {"title": "B", "format": "Carousel"},
            {"title": "C", "format": "Carousel"},
            {"title": "D", "format": "Skywriting"},
        ]}
        result = analysis.sanitize_synthesis(raw, 3, content_types=["carousel", "poll"])
        assert [idea["format"] for idea in result["content_ideas"]] == [
            "Carousel", "Carousel", "Carousel", "Poll"
        ]

    def test_a_run_of_unusable_formats_is_spread_across_the_chosen_ones(self):
        raw = {"content_ideas": [
            {"title": "i{}".format(i), "format": "Skywriting"} for i in range(4)
        ]}
        result = analysis.sanitize_synthesis(
            raw, 3, content_types=["poll", "text", "video"]
        )
        formats = [idea["format"] for idea in result["content_ideas"]]
        # Menu order breaks the ties, and no format is used twice before every
        # other chosen one has been used once.
        assert formats == ["Text post", "Video script", "Poll", "Text post"]

    def test_the_spread_counts_the_formats_the_model_already_chose(self):
        raw = {"content_ideas": [
            {"title": "A", "format": "Poll"},
            {"title": "B", "format": "Poll"},
            {"title": "C", "format": "Skywriting"},
        ]}
        result = analysis.sanitize_synthesis(raw, 3, content_types=["poll", "text"])
        assert result["content_ideas"][2]["format"] == "Text post"

    def test_the_map_covers_exactly_the_offered_types(self):
        assert set(analysis.CONTENT_TYPE_FORMATS) == {
            "text", "carousel", "video", "poll", "article",
            "short_video", "thread", "issue",
        }
        for label in analysis.CONTENT_TYPE_FORMATS.values():
            assert label in analysis.IDEA_FORMATS

    def test_every_idea_format_the_model_may_emit_is_offerable_as_a_content_type(self):
        # The mixed-mode menu and the content-type selector have to line up: a
        # format the model is told it may use, but which the user cannot ask
        # for, is a gap in the selector.
        assert set(analysis.CONTENT_TYPE_FORMATS.values()) == set(analysis.IDEA_FORMATS)


class TestChannelTaxonomy:
    """The channel maps decide what a creator can be offered, so they are checked
    for internal consistency rather than trusted."""

    def test_every_channel_has_formats_and_draft_rules(self):
        assert set(analysis.CHANNEL_FORMATS) == set(analysis.CHANNELS)
        assert set(analysis.CHANNEL_DRAFT_RULES) == set(analysis.CHANNELS)

    def test_every_channel_format_is_a_real_content_type(self):
        # A channel pointing at a format key that does not exist would silently
        # narrow that channel's menu to nothing.
        for keys in analysis.CHANNEL_FORMATS.values():
            assert keys, "a channel with no formats can ship nothing"
            for key in keys:
                assert key in analysis.CONTENT_TYPE_FORMATS

    def test_every_format_is_shippable_somewhere(self):
        # A format no channel can ship is one a creator can pick and then never
        # be offered again once they name where they publish.
        covered = {
            key for keys in analysis.CHANNEL_FORMATS.values() for key in keys
        }
        assert covered == set(analysis.CONTENT_TYPE_FORMATS)

    def test_every_draft_rule_names_a_format_this_module_can_write(self):
        for label in analysis.IDEA_FORMATS:
            assert label in analysis.DRAFT_FORMAT_RULES
            assert label in analysis.DRAFT_FORMAT_WORDS

    def test_channel_formats_is_the_union_not_the_intersection(self):
        # TikTok and a newsletter share no format at all. Someone posting to both
        # wants each channel's formats, not the empty set they have in common.
        both = analysis.channel_formats(["tiktok", "newsletter"])
        assert "Short-form video script" in both
        assert "Newsletter issue" in both

    def test_channel_formats_is_in_menu_order(self):
        assert analysis.channel_formats(["instagram"]) == [
            "Text post", "Carousel", "Poll", "Short-form video script"
        ]

    def test_no_channels_means_no_restriction(self):
        assert analysis.channel_formats([]) == []
        assert analysis.channel_formats(None) == []

    def test_an_unknown_channel_is_dropped_rather_than_narrowing_anything(self):
        assert analysis.channel_formats(["myspace"]) == []
        assert analysis.channel_formats(["myspace", "tiktok"]) == [
            "Short-form video script"
        ]

    def test_channel_labels_and_join(self):
        assert analysis.channel_labels(["x", "linkedin", "nope"]) == ["LinkedIn", "X"]
        assert analysis.join_labels(["LinkedIn"]) == "LinkedIn"
        assert analysis.join_labels(["LinkedIn", "X"]) == "LinkedIn and X"
        assert (
            analysis.join_labels(["LinkedIn", "X", "TikTok"])
            == "LinkedIn, X and TikTok"
        )
        assert analysis.join_labels([]) == ""


class TestAllowedFormats:
    """Two independent pins can contradict each other, and which one wins is a
    promise to the user either way."""

    def test_neither_pin_means_every_format(self):
        assert analysis.allowed_formats(None, None) == ([], False)

    def test_a_content_type_pin_alone_is_unchanged(self):
        assert analysis.allowed_formats(None, ["poll"]) == (["Poll"], False)

    def test_a_channel_pin_alone_gives_that_channel_its_formats(self):
        assert analysis.allowed_formats(["tiktok"], None) == (
            ["Short-form video script"], False
        )

    def test_compatible_pins_intersect(self):
        assert analysis.allowed_formats(["instagram"], ["carousel", "poll"]) == (
            ["Carousel", "Poll"], False
        )

    def test_a_pin_the_channel_cannot_ship_loses_to_the_channel(self):
        # A shared link can carry ch=tiktok&type=poll. Refusing would break the
        # link; honouring the poll would hand back something unpostable. The
        # channel wins and the caller is told, so the report can say so.
        formats, dropped = analysis.allowed_formats(["tiktok"], ["poll"])
        assert formats == ["Short-form video script"]
        assert dropped is True

    def test_a_partly_impossible_pin_keeps_the_possible_part_silently(self):
        # Carousel is shippable on Instagram and a poll is too, but a thread is
        # not. Something survived, so nothing was really dropped from the user's
        # intent and there is nothing to announce.
        formats, dropped = analysis.allowed_formats(
            ["instagram"], ["carousel", "thread"]
        )
        assert formats == ["Carousel"]
        assert dropped is False

    def test_the_sanitiser_pins_ideas_to_what_the_channel_can_ship(self):
        raw = {"content_ideas": [
            {"title": "A", "format": "Poll"},
            {"title": "B", "format": "Long-form article"},
        ]}
        result = analysis.sanitize_synthesis(raw, 3, channels=["tiktok"])
        assert [idea["format"] for idea in result["content_ideas"]] == [
            "Short-form video script"
        ] * 2


class TestChannelDrafting:
    IDEA = {"title": "t", "hook": "h", "format": "Text post", "angle": "a"}

    def test_a_neutral_draft_names_no_channel_and_keeps_the_old_caps(self):
        prompt = analysis._draft_prompt(self.IDEA, [], "q", "")
        assert "a social post" in prompt
        assert "LinkedIn" not in prompt
        assert "at most 3 hashtags" in prompt

    @pytest.mark.parametrize(
        "channel,artefact",
        [
            ("linkedin", "a LinkedIn post"),
            ("instagram", "an Instagram post"),
            ("tiktok", "a TikTok video"),
            ("x", "a post for X"),
            ("youtube", "a YouTube video"),
            ("newsletter", "a newsletter email"),
        ],
    )
    def test_each_channel_names_the_artefact_it_actually_produces(
        self, channel, artefact
    ):
        prompt = analysis._draft_prompt(self.IDEA, [], "q", "", channel)
        assert "ghostwriting {}".format(artefact) in prompt

    def test_the_hashtag_ceiling_follows_the_channel(self):
        instagram = analysis._draft_prompt(self.IDEA, [], "q", "", "instagram")
        assert "at most 10 hashtags" in instagram
        x = analysis._draft_prompt(self.IDEA, [], "q", "", "x")
        assert "at most 1 hashtags" in x

    def test_a_newsletter_is_told_not_to_use_hashtags_at_all(self):
        prompt = analysis._draft_prompt(self.IDEA, [], "q", "", "newsletter")
        assert "No hashtags at all." in prompt
        assert "at most" not in prompt.split("hashtag")[0][-40:]

    def test_a_zero_hashtag_cap_removes_the_line_without_a_trailing_blank(self):
        # The cap used to be a constant that was always at least one, so this
        # branch is newer than the whitespace normaliser it runs after.
        assert analysis.sanitize_post("Body here.\n\n#a #b #c", 0) == "Body here."

    def test_an_unknown_channel_falls_back_to_the_neutral_rules(self):
        rules = analysis.draft_rules("myspace")
        assert rules["hashtags"] == analysis.DRAFT_MAX_HASHTAGS
        assert rules["poll_options"] == analysis.DRAFT_MAX_POLL_OPTIONS
        assert rules["voice"] == ""

    def test_the_new_formats_each_get_their_own_shape(self):
        for fmt, marker in (
            ("Short-form video script", "vertical short-form script"),
            ("Thread", "under 280 characters"),
            ("Newsletter issue", "Subject:"),
        ):
            prompt = analysis._draft_prompt(
                dict(self.IDEA, format=fmt), [], "q", ""
            )
            assert marker in prompt

    def test_no_channel_voice_line_contains_a_dash(self):
        # Same rule the prompts impose on the model: they must not model the habit.
        for rules in analysis.CHANNEL_DRAFT_RULES.values():
            assert "—" not in rules["voice"] and "–" not in rules["voice"]


class TestIdeaCount:
    def test_the_prompt_asks_for_twelve_to_fifteen(self):
        prompt = analysis._synthesis_prompt([], [], "q", "", ["x"])
        assert analysis.MIN_CONTENT_IDEAS == 12 and analysis.MAX_CONTENT_IDEAS == 15
        assert "12 to 15 posts" in prompt

    def test_the_sanitiser_accepts_a_wider_band_than_the_prompt_asks_for(self):
        assert analysis.SANITIZE_MIN_CONTENT_IDEAS == 8
        assert analysis.SANITIZE_MAX_CONTENT_IDEAS == 20
        raw = {"content_ideas": [{"title": "i{}".format(i)} for i in range(18)]}
        assert len(analysis.sanitize_synthesis(raw, 3)["content_ideas"]) == 18

    def test_the_synthesis_budget_covers_fifteen_ideas(self):
        # Roughly 250 output tokens per pretty-printed idea plus the rest of the
        # payload, all of it well inside the ceiling with room left for reasoning.
        payload = 250 * analysis.MAX_CONTENT_IDEAS + 80 * analysis.MAX_TOPICS + 1000
        assert payload < analysis.SYNTHESIS_MAX_TOKENS / 2


class TestReportTitle:
    def test_the_prompt_asks_for_one(self):
        prompt = analysis._synthesis_prompt([], [], "q", "", ["x"])
        assert '"report_title"' in prompt

    def test_a_model_title_is_stripped_and_trimmed_to_a_word_boundary(self):
        raw = {"report_title": "**How " + "freelancers " * 20 + "price**"}
        title = analysis.sanitize_synthesis(raw, 3)["report_title"]
        assert len(title) <= analysis.MAX_REPORT_TITLE_CHARS
        assert not title.endswith("freelance")
        assert "*" not in title

    def test_a_missing_title_falls_back_to_the_query(self):
        result = analysis.sanitize_synthesis({}, 3, query="scope creep on fixed fee projects")
        assert result["report_title"] == "scope creep on fixed fee projects"

    def test_a_long_query_fallback_stops_at_a_word_boundary(self):
        query = "how freelance designers handle scope creep on fixed fee client projects"
        title = analysis.report_title_from_query(query)
        assert len(title) <= analysis.FALLBACK_TITLE_CHARS
        assert query.startswith(title)
        assert title.split()[-1] in query.split()

    def test_no_query_and_no_title_still_gives_something_renderable(self):
        assert analysis.sanitize_synthesis({}, 3)["report_title"]

    def test_markdown_and_links_never_survive_a_title(self):
        raw = {"report_title": "[Pricing](http://evil.example) at scale"}
        assert analysis.sanitize_synthesis(raw, 3)["report_title"] == "Pricing at scale"


class TestClaimQuality:
    def test_a_question_that_restates_a_pain_point_is_dropped(self):
        raw = {
            "pain_points": [{"text": "Clients keep adding work after the quote"}],
            "questions": [
                {"text": "Clients keep adding work after the quote?"},
                {"text": "How do you write a scope that survives contact?"},
            ],
        }
        result = analysis.sanitize_synthesis(raw, 3)
        assert len(result["questions"]) == 1
        assert result["questions"][0]["text"].startswith("How do you write")

    def test_duplicates_within_one_list_are_dropped_too(self):
        raw = {"pain_points": [
            {"text": "Clients keep adding work after the quote"},
            {"text": "clients keep adding work after the quote."},
        ]}
        assert len(analysis.sanitize_synthesis(raw, 3)["pain_points"]) == 1

    def test_a_claim_too_short_to_say_anything_is_dropped(self):
        raw = {"pain_points": [{"text": "Pricing"}, {"text": "Tools"},
                               {"text": "Nobody answers a scoping email in under a week"}]}
        result = analysis.sanitize_synthesis(raw, 3)
        assert [claim["text"] for claim in result["pain_points"]] == [
            "Nobody answers a scoping email in under a week"
        ]

    def test_the_floor_is_a_named_constant_not_a_magic_number(self):
        assert analysis.MIN_CLAIM_CHARS == 25


class TestHookQualityRules:
    """The hook is the product. These rules are the difference between a report
    someone publishes from and one they scroll past."""

    def test_the_question_then_command_template_is_banned_by_name(self):
        prompt = analysis._synthesis_prompt([], [], "q", "", ["x"])
        assert "[Problem]?" in prompt and "banned" in prompt

    def test_the_prompt_demands_a_number_a_quote_or_a_named_situation(self):
        prompt = analysis._synthesis_prompt([], [], "q", "", ["x"])
        assert "a specific number" in prompt
        assert "quoted from the evidence" in prompt
        assert "concrete named situation" in prompt

    def test_the_prompt_shows_three_good_and_three_bad_examples(self):
        prompt = analysis._synthesis_prompt([], [], "q", "", ["x"])
        good_block = prompt.split("Good hooks")[1].split("Bad hooks")[0]
        bad_block = prompt.split("Bad hooks")[1].split("\n-")[0]
        # Examples are the indented lines; the rule sentence around them is not.
        good = [line for line in good_block.split("\n") if line.startswith("    ")]
        bad = [line for line in bad_block.split("\n") if line.startswith("    ")]
        assert len(good) == 3 and len(bad) == 3
        assert "250 cold emails" in good_block
        assert "Struggling to price your work?" in bad_block

    def test_the_prompt_caps_how_many_ideas_may_name_the_brand(self):
        prompt = analysis._synthesis_prompt([], [], "q", "", ["x"])
        assert "At most 2 of the 15 ideas may name" in prompt

    def test_the_prompt_bans_a_quote_with_a_marketing_tail_bolted_on(self):
        # The gamed pattern from a live run: a real Reddit line, then a second
        # sentence selling the post. Named shape by shape so the model cannot
        # read the rule as being about one phrasing.
        prompt = analysis._synthesis_prompt([], [], "q", "", ["x"])
        assert "A hook is ONE voice" in prompt
        assert "NEVER append a promise, an instruction, a reassurance or an offer" in prompt
        for tail in ("It doesn't have to be.", "Here's how.", "Explore these alternatives."):
            assert tail in prompt

    def test_the_prompt_keeps_the_brand_out_of_the_hook_entirely(self):
        prompt = analysis._synthesis_prompt([], [], "q", "profile text", ["x"])
        assert "may never" in prompt and "appear inside a hook" in prompt
        assert 'that belongs in "angle" and "rationale". Never in "hook".' in prompt

    def test_the_prompt_gives_a_mechanical_test_for_the_question_plus_promise(self):
        # A rule the model can check itself beats a rule it has to interpret,
        # and interpretation is what produced "[Quote]? Here's my clause."
        prompt = analysis._synthesis_prompt([], [], "q", "", ["x"])
        assert "that question mark must be the LAST character" in prompt
        assert "Here's my contract clause." in prompt

    def test_the_prompt_bans_the_promise_fragment_anywhere_in_a_hook(self):
        prompt = analysis._synthesis_prompt([], [], "q", "", ["x"])
        assert "may never end on a fragment that promises the answer" in prompt
        for fragment in ('"My process."', '"Here\'s how."', '"My system."'):
            assert fragment in prompt

    def test_the_prompt_bans_restating_the_query_or_the_profile(self):
        prompt = analysis._synthesis_prompt([], [], "q", "profile text", ["x"])
        assert "may never restate the research question" in prompt
        assert "self-description of the person you are writing for" in prompt

    def test_the_prompt_demands_a_distinct_opening_for_every_hook(self):
        prompt = analysis._synthesis_prompt([], [], "q", "", ["x"])
        assert "Every hook must open on different words" in prompt
        assert "No two hooks in this response may begin" in prompt

    def test_no_good_hook_example_models_the_banned_tail(self):
        # The examples teach harder than the rules do, so none of them may be
        # a quote with a sales sentence appended.
        prompt = analysis._synthesis_prompt([], [], "q", "", ["x"])
        good_block = prompt.split("Good hooks")[1].split("Bad hooks")[0]
        for tail in ("Here is what", "Here is how", "Here's how", "Try these"):
            assert tail not in good_block


class TestStripsUrlsAndEmphasis:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("**bold** claim", "bold claim"),
            ("an *emphasised* claim", "an emphasised claim"),
            ("__underlined__ claim", "underlined claim"),
            ("read https://evil.example/x now", "read now"),
            ("see www.evil.example for more", "see for more"),
            ("go to evilsite.com/deals today", "go to today"),
            ("try Notion.so and Figma.com", "try and"),
        ],
    )
    def test_links_and_emphasis_never_reach_a_report(self, raw, expected):
        assert analysis.strip_markdown(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        ["Node.js is fine", "version 3.5 shipped", "i.e. this survives", "5 * 3 = 15"],
    )
    def test_ordinary_text_that_looks_like_a_domain_survives(self, raw):
        assert analysis.strip_markdown(raw) == raw

    def test_no_hook_can_smuggle_a_url(self):
        raw = {"content_ideas": [{"title": "A", "hook": "Read more at scamsite.com/offer"}]}
        hook = analysis.sanitize_synthesis(raw, 3)["content_ideas"][0]["hook"]
        assert "scamsite" not in hook and "http" not in hook


class TestDraftGrounding:
    """The draft is the one output a user puts their own name on, so it may not
    contain a number the evidence did not give it."""

    IDEA = {"title": "Scope creep", "hook": "Twenty hours quoted, sixty five delivered.",
            "format": "Text post", "angle": "Discovery is the fix.",
            "addresses": "Losing margin on fixed fee work"}

    def test_the_prompt_forbids_inventing_specifics(self):
        prompt = analysis._draft_prompt(self.IDEA, ["We quoted 20 hours."], "q", "")
        for rule in ("Never invent a statistic", "named tool", "personal anecdote",
                     "must come from the excerpts"):
            assert rule in prompt

    def test_the_prompt_offers_an_opinion_piece_instead_of_invented_data(self):
        prompt = analysis._draft_prompt(self.IDEA, [], "q", "")
        assert "opinionated" in prompt
        assert "An invented number is not." in prompt

    @pytest.mark.parametrize(
        "fmt,marker",
        [
            ("Carousel", "Slide 1:"),
            ("Video script", "Beat 1:"),
            ("Long-form article", "structured long-form article"),
            ("Poll", "poll question"),
            ("Text post", "short paragraphs"),
        ],
    )
    def test_each_format_gets_its_own_shape(self, fmt, marker):
        prompt = analysis._draft_prompt(dict(self.IDEA, format=fmt), [], "q", "")
        assert marker in prompt

    def test_a_poll_draft_asks_for_a_question_bounded_options_and_a_vote_prompt(self):
        prompt = analysis._draft_prompt(dict(self.IDEA, format="Poll"), [], "q", "")
        assert "the poll question on its own line" in prompt
        assert "two to four options" in prompt
        assert 'prefixed with "Option:"' in prompt
        assert "vote" in prompt and "comments" in prompt
        assert "80 to 180 words" in prompt

    def test_an_unknown_format_falls_back_to_a_plain_post(self):
        prompt = analysis._draft_prompt(dict(self.IDEA, format="Skywriting"), [], "q", "")
        assert "short paragraphs" in prompt

    def test_a_near_miss_format_name_still_matches(self):
        # The label the engine no longer uses still resolves, because stored
        # bundles and replayed links are full of it.
        assert analysis._draft_format({"format": "Carousel (PDF)"}) == "Carousel"
        assert analysis._draft_format({"format": "reel"}) == "Short-form video script"
        # The long and the short video script must not swallow each other.
        assert analysis._draft_format({"format": "video"}) == "Video script"
        assert (
            analysis._draft_format({"format": "Short-form video script"})
            == "Short-form video script"
        )

    def test_the_word_band_follows_the_format(self):
        article = analysis._draft_prompt(
            dict(self.IDEA, format="Long-form article"), [], "q", ""
        )
        assert "350 to 650 words" in article

    def test_evidence_is_carried_at_full_excerpt_length(self):
        long_excerpt = "x" * 900
        lines = analysis._evidence_lines([long_excerpt])
        assert analysis.DRAFT_EVIDENCE_CHARS == 600
        assert len(lines) <= analysis.DRAFT_EVIDENCE_CHARS + 6


class TestSynthesizePublicCall:
    def test_forwards_query_into_the_prompt_and_sanitises_the_result(self, monkeypatch):
        captured = {}

        def fake_chat_json(prompt, max_tokens, label, thinking_budget=None):
            captured["prompt"] = prompt
            captured["label"] = label
            captured["thinking_budget"] = thinking_budget
            return (
                {"content_ideas": [{"title": "A", "hook": "Stop scrolling"}]},
                "test-model",
            )

        monkeypatch.setattr(analysis, "_chat_json", fake_chat_json)
        result = analysis.synthesize([], [], "skincare routines", "", ["skincareaddiction"])

        assert result["model"] == "test-model"
        assert captured["label"] == "synthesis"
        assert captured["thinking_budget"] == analysis.SYNTHESIS_THINKING_BUDGET
        assert "skincare routines" in captured["prompt"]
        assert result["content_ideas"][0]["hook"] == "Stop scrolling"
        assert "platform" not in result["content_ideas"][0]

    def test_the_profile_reaches_the_sanitiser_so_the_echo_guard_is_live(self, monkeypatch):
        """The guard is worthless if the real call path never hands it a profile."""

        def fake_chat_json(prompt, max_tokens, label, thinking_budget=None):
            return (
                {"content_ideas": [
                    {"title": "Echo", "hook": "I run a Notion template shop for designers."},
                    {"title": "Real", "hook": "Nobody told me the deadline moved until Friday."},
                ]},
                "test-model",
            )

        monkeypatch.setattr(analysis, "_chat_json", fake_chat_json)
        result = analysis.synthesize(
            [], [], "q", "I run a Notion template shop for designers.", ["x"]
        )
        assert [idea["title"] for idea in result["content_ideas"]] == ["Real"]


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
        self.last_prompt = None
        self.last_config = None
        self.configs = []
        # Which model each call went to, so a test can assert a stage's tier.
        self.models_used = []

    def generate_content(self, model, contents, config):
        self.calls += 1
        self.last_prompt = contents
        self.last_config = config
        self.configs.append(config)
        self.models_used.append(model)
        body = self.bodies[min(self.calls - 1, len(self.bodies) - 1)]
        return _FakeResponse(body)


class _FakeClient:
    def __init__(self, bodies):
        self.models = _FakeModels(bodies)


class TestSanitizePost:
    """`sanitize_post` cleans a whole draft without collapsing it to one line: the
    blank lines between paragraphs are what makes a post readable in a feed, so
    `strip_markdown` (which flattens all whitespace) is applied per line instead."""

    def test_markdown_is_stripped_per_line_and_blank_lines_survive(self):
        raw = "**Bold opener.**\n\nA [linked](http://evil.example) second line."
        result = analysis.sanitize_post(raw)
        assert result == "Bold opener.\n\nA linked second line."

    def test_runs_of_blank_lines_collapse_to_one(self):
        raw = "First paragraph.\n\n\n\nSecond paragraph."
        result = analysis.sanitize_post(raw)
        assert "\n\n\n" not in result
        assert result == "First paragraph.\n\nSecond paragraph."

    def test_hashtag_line_is_capped_at_the_maximum(self):
        raw = "A post.\n\n#one #two #three #four #five #six"
        result = analysis.sanitize_post(raw)
        last_line = result.split("\n")[-1]
        assert last_line.count("#") == analysis.DRAFT_MAX_HASHTAGS
        assert last_line == "#one #two #three"

    def test_a_hashtag_used_mid_sentence_is_left_alone(self):
        # Only a line that is PURELY hashtags gets capped; one used as a word within
        # a sentence is ordinary text, not the "wall of tags" the cap exists for.
        raw = "Growth is not a #hashtag strategy, it is a habit."
        result = analysis.sanitize_post(raw)
        assert result == raw

    def test_paired_emphasis_is_stripped_but_a_lone_asterisk_survives(self):
        raw = "It costs $5 * 3 for the *bundle*, not **the whole thing**."
        result = analysis.sanitize_post(raw)
        assert "$5 * 3" in result
        assert "*bundle*" not in result and "bundle" in result
        assert "**" not in result

    def test_crlf_and_lone_cr_line_endings_are_normalised_to_lf(self):
        raw = "Line one.\r\nLine two.\r\n\r\nLine three.\r"
        result = analysis.sanitize_post(raw)
        assert "\r" not in result
        assert result == "Line one.\nLine two.\n\nLine three."

    def test_em_and_en_dashes_are_normalised_like_everywhere_else(self):
        raw = "Scope creep 2019—2024 is not a pricing problem — it is a discovery one."
        result = analysis.sanitize_post(raw)
        assert "—" not in result and "–" not in result
        assert "2019-2024" in result

    def test_none_becomes_an_empty_string(self):
        assert analysis.sanitize_post(None) == ""

    def test_does_not_end_with_a_trailing_blank_line(self):
        raw = "A post.\n\n\n"
        result = analysis.sanitize_post(raw)
        assert result == result.rstrip()
        assert result == "A post."


class TestDraftPost:
    """`draft_post` turns one content idea into a publish-ready post. These tests use
    the same fake client shape as the retry regression tests above: `.text` and
    `.candidates[0].finish_reason`, injected via `monkeypatch.setattr(analysis,
    "_client", fake)` so nothing here reaches Vertex."""

    IDEA = {
        "title": "Why your agency retainer keeps shrinking",
        "hook": "Your retainer is not shrinking because of your pricing.",
        "format": "Text post",
        "angle": "Scope creep is a discovery problem, not a contract problem.",
        "addresses": "Agencies losing margin on fixed-fee work",
    }
    EVIDENCE = [
        "We quoted 20 hours and delivered 65.",
        "Every retainer I have lost started with a free quick favour in month two.",
    ]

    @staticmethod
    def _client_with(payload):
        import json as _json

        return _FakeClient([_json.dumps(payload)])

    def test_returns_exactly_the_post_key(self, monkeypatch):
        client = self._client_with({"post": "Hello there.\n\nSecond line."})
        monkeypatch.setattr(analysis, "_client", client)

        result = analysis.draft_post(self.IDEA, self.EVIDENCE, "q", "p")

        assert set(result.keys()) == {"post"}
        assert result["post"] == "Hello there.\n\nSecond line."

    def test_empty_result_raises_the_user_ready_error(self, monkeypatch):
        client = self._client_with({"post": "   \n\n  "})
        monkeypatch.setattr(analysis, "_client", client)

        with pytest.raises(ValueError) as excinfo:
            analysis.draft_post(self.IDEA, self.EVIDENCE, "q", "")

        assert str(excinfo.value) == "We could not draft that post. Please try again."

    def test_thinking_stays_on_for_drafts(self, monkeypatch):
        client = self._client_with({"post": "Hello there."})
        monkeypatch.setattr(analysis, "_client", client)

        analysis.draft_post(self.IDEA, self.EVIDENCE, "q", "")

        # Thinking is turned off by passing a thinking_config; its absence here is
        # what proves the draft call left the default (thinking on) untouched.
        assert "thinking_config" not in client.models.last_config

    def test_idea_query_profile_and_evidence_all_reach_the_prompt(self, monkeypatch):
        client = self._client_with({"post": "Hello there."})
        monkeypatch.setattr(analysis, "_client", client)

        analysis.draft_post(
            self.IDEA, self.EVIDENCE, "content ideas for marketing agencies", "I run a 6-person agency"
        )

        prompt = client.models.last_prompt
        for needle in (
            self.IDEA["hook"], self.IDEA["angle"], "marketing agencies",
            "6-person agency", "We quoted 20 hours",
        ):
            assert needle in prompt

    def test_evidence_is_capped_before_it_reaches_the_prompt(self):
        many = ["excerpt number {}".format(i) for i in range(20)]
        lines = analysis._evidence_lines(many)
        assert len(lines.split("\n")) == analysis.DRAFT_MAX_EVIDENCE

    def test_a_poll_draft_with_too_many_options_is_capped_at_four(self, monkeypatch):
        # Every poll UI caps at four options; a live run once produced six, so
        # this pins the backstop that keeps the first four and drops the rest.
        post = (
            "Hook line.\n\nSome context.\n\nWhich do you use?\n"
            "Option: A\nOption: B\nOption: C\nOption: D\nOption: E\nOption: F\n\n"
            "Vote and tell me why in the comments."
        )
        client = self._client_with({"post": post})
        monkeypatch.setattr(analysis, "_client", client)

        result = analysis.draft_post(dict(self.IDEA, format="Poll"), self.EVIDENCE, "q", "")

        option_lines = [line for line in result["post"].split("\n") if line.startswith("Option:")]
        assert option_lines == ["Option: A", "Option: B", "Option: C", "Option: D"]
        # Everything else in the draft survives untouched.
        assert "Which do you use?" in result["post"]
        assert "Vote and tell me why in the comments." in result["post"]

    def test_a_non_poll_draft_with_option_like_lines_is_left_alone(self, monkeypatch):
        post = "\n".join("Option: {}".format(i) for i in range(6))
        client = self._client_with({"post": post})
        monkeypatch.setattr(analysis, "_client", client)

        result = analysis.draft_post(dict(self.IDEA, format="Text post"), self.EVIDENCE, "q", "")

        assert result["post"].count("Option:") == 6


class TestPerStageModelSelection:
    """Which model each stage runs on, and how a deployment moves one of them.

    The label a call already passes to `_chat_json` is the whole seam: high volume
    labelling (triage, classification) runs on the lite tier, everything that writes
    prose or recalls the world stays on the primary model, and anything unrecognised
    gets the primary model rather than being silently downgraded.
    """

    @pytest.fixture(autouse=True)
    def _no_sleep(self, monkeypatch):
        monkeypatch.setattr(analysis.time, "sleep", lambda seconds: None)

    def test_the_high_volume_labelling_stages_default_to_the_lite_tier(self):
        assert analysis.model_for_stage("relevance-triage") == analysis.LITE_MODEL
        assert analysis.model_for_stage("classification") == analysis.LITE_MODEL

    def test_the_lite_tier_is_a_real_vertex_model_id(self):
        assert analysis.LITE_MODEL == "gemini-2.5-flash-lite"
        assert analysis.LITE_MODEL in analysis.MODEL_DISPLAY_NAMES

    def test_everything_that_writes_or_recalls_keeps_the_primary_model(self):
        for label in ("discovery", "brief-decomposition", "synthesis", "draft"):
            assert analysis.model_for_stage(label) == analysis.PRIMARY_MODEL

    def test_an_unknown_label_gets_the_primary_model(self):
        assert analysis.model_for_stage("demo") == analysis.PRIMARY_MODEL
        assert analysis.model_for_stage("") == analysis.PRIMARY_MODEL

    def test_the_tier_table_covers_every_label_the_engine_actually_sends(self):
        # Discovery's label lives in discovery.py, so the table has to be checked
        # against both modules or a renamed stage quietly falls back to primary.
        assert set(analysis.STAGE_TIERS) == {
            "relevance-triage",
            "classification",
            "brief-decomposition",
            "discovery",
            "synthesis",
            "draft",
        }

    def test_the_env_var_name_is_derived_from_the_label(self):
        assert analysis._stage_env_var("relevance-triage") == "GEMINI_MODEL_RELEVANCE_TRIAGE"
        assert analysis._stage_env_var("synthesis") == "GEMINI_MODEL_SYNTHESIS"

    def test_a_per_stage_env_override_wins_over_the_tier(self, monkeypatch):
        monkeypatch.setenv("GEMINI_MODEL_RELEVANCE_TRIAGE", "gemini-experimental")
        monkeypatch.setenv("GEMINI_MODEL_SYNTHESIS", " gemini-other ")
        assert analysis.model_for_stage("relevance-triage") == "gemini-experimental"
        # Whitespace is trimmed, because an env var pasted into a dashboard
        # collects it and a padded model id is a 404 deep inside the SDK.
        assert analysis.model_for_stage("synthesis") == "gemini-other"
        # An untouched stage is unaffected.
        assert analysis.model_for_stage("classification") == analysis.LITE_MODEL

    def test_an_empty_env_override_is_ignored_rather_than_sent(self, monkeypatch):
        monkeypatch.setenv("GEMINI_MODEL_CLASSIFICATION", "   ")
        assert analysis.model_for_stage("classification") == analysis.LITE_MODEL

    def test_moving_the_globals_moves_every_stage_that_follows_them(self, monkeypatch):
        # Resolution is per call, not captured at import, which is what lets one
        # constant change re-point a whole tier.
        monkeypatch.setattr(analysis, "LITE_MODEL", "lite-next")
        monkeypatch.setattr(analysis, "PRIMARY_MODEL", "primary-next")
        assert analysis.model_for_stage("classification") == "lite-next"
        assert analysis.model_for_stage("synthesis") == "primary-next"

    def test_the_stage_model_is_what_reaches_the_client(self, monkeypatch):
        client = _FakeClient(['{"ok": 1}'])
        monkeypatch.setattr(analysis, "_client", client)

        analysis.chat_json("prompt", 100, "classification", thinking=False)
        analysis.chat_json("prompt", 100, "synthesis")

        assert client.models.models_used == [analysis.LITE_MODEL, analysis.PRIMARY_MODEL]

    def test_a_stage_already_on_the_lite_tier_falls_up_to_the_primary_model(self, monkeypatch):
        class _ModelMissingOnce:
            def __init__(self):
                self.models_used = []

            def generate_content(self, model, contents, config):
                self.models_used.append(model)
                if len(self.models_used) == 1:
                    raise Exception("404 NOT_FOUND: publisher model was not found")
                return _FakeResponse('{"ok": 1}')

        client = type("C", (), {"models": _ModelMissingOnce()})()
        monkeypatch.setattr(analysis, "_client", client)

        parsed, model = analysis.chat_json("prompt", 100, "classification", thinking=False)

        assert parsed == {"ok": 1}
        assert model == analysis.PRIMARY_MODEL
        assert client.models.models_used == [analysis.LITE_MODEL, analysis.PRIMARY_MODEL]

    def test_a_primary_stage_still_falls_down_to_the_lite_tier(self):
        assert analysis._fallback_model(analysis.PRIMARY_MODEL) == analysis.LITE_MODEL
        assert analysis._fallback_model(analysis.LITE_MODEL) == analysis.PRIMARY_MODEL

    def test_one_model_configured_everywhere_leaves_nowhere_to_fall_back_to(self, monkeypatch):
        monkeypatch.setattr(analysis, "PRIMARY_MODEL", "only-model")
        monkeypatch.setattr(analysis, "LITE_MODEL", "only-model")
        assert analysis._fallback_model("only-model") is None

    def test_moving_the_lite_model_moves_what_a_lite_stage_falls_up_from(self, monkeypatch):
        # The fallback is resolved against the current LITE_MODEL, not a copy taken
        # at import: with a stale copy in play, a lite stage whose model had moved
        # fell sideways onto the id LITE_MODEL used to hold, which is not a model
        # this deployment was told to use and not a step up either.
        monkeypatch.setattr(analysis, "LITE_MODEL", "lite-next")
        assert analysis._fallback_model("lite-next") == analysis.PRIMARY_MODEL
        assert analysis._fallback_model(analysis.PRIMARY_MODEL) == "lite-next"
        # The id it used to hold is now just another unavailable model.
        assert analysis._fallback_model("gemini-2.5-flash-lite") == "lite-next"

    def test_a_moved_lite_model_still_falls_up_to_primary_on_a_real_call(self, monkeypatch):
        class _ModelMissingOnce:
            def __init__(self):
                self.models_used = []

            def generate_content(self, model, contents, config):
                self.models_used.append(model)
                if len(self.models_used) == 1:
                    raise Exception("404 NOT_FOUND: publisher model was not found")
                return _FakeResponse('{"ok": 1}')

        monkeypatch.setattr(analysis, "LITE_MODEL", "lite-next")
        client = type("C", (), {"models": _ModelMissingOnce()})()
        monkeypatch.setattr(analysis, "_client", client)

        parsed, model = analysis.chat_json("prompt", 100, "classification", thinking=False)

        assert parsed == {"ok": 1}
        assert model == analysis.PRIMARY_MODEL
        assert client.models.models_used == ["lite-next", analysis.PRIMARY_MODEL]


class TestSynthesisThinkingBudget:
    """Synthesis is the one call that thinks, so its reasoning is capped explicitly."""

    def test_the_budget_is_modest_and_leaves_the_answer_its_room(self):
        assert 2048 <= analysis.SYNTHESIS_THINKING_BUDGET <= 4096
        assert analysis.SYNTHESIS_THINKING_BUDGET < analysis.SYNTHESIS_MAX_TOKENS // 4

    def test_a_thinking_call_with_no_budget_leaves_the_decision_to_gemini(self):
        assert "thinking_config" not in analysis._generation_config(100, thinking=True)

    def test_a_budget_is_sent_as_the_thinking_config(self):
        config = analysis._generation_config(100, thinking=True, thinking_budget=3072)
        assert config["thinking_config"] == {"thinking_budget": 3072}

    def test_thinking_switched_off_beats_any_budget(self):
        config = analysis._generation_config(100, thinking=False, thinking_budget=3072)
        assert config["thinking_config"] == {"thinking_budget": 0}

    def test_a_negative_budget_cannot_reach_the_api(self):
        config = analysis._generation_config(100, thinking=True, thinking_budget=-5)
        assert config["thinking_config"] == {"thinking_budget": 0}

    def test_the_real_synthesis_call_caps_its_reasoning(self, monkeypatch):
        client = _FakeClient(['{"content_ideas": [{"title": "A", "hook": "H"}]}'])
        monkeypatch.setattr(analysis, "_client", client)

        analysis.synthesize([], [], "a query", "", ["s"])

        assert client.models.last_config["thinking_config"] == {
            "thinking_budget": analysis.SYNTHESIS_THINKING_BUDGET
        }

    def test_a_truncation_downgrade_still_reaches_a_zero_budget(self, monkeypatch):
        monkeypatch.setattr(analysis.time, "sleep", lambda seconds: None)
        client = _FakeClient(["not json", '{"ok": 1}'])
        monkeypatch.setattr(analysis, "_client", client)

        analysis.chat_json("prompt", 100, "synthesis", thinking_budget=3072)

        assert client.models.configs[0]["thinking_config"] == {"thinking_budget": 3072}
        assert client.models.configs[1]["thinking_config"] == {"thinking_budget": 0}


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


class TestChatJsonDowngradesThinkingAfterATruncation:
    """Gemini spends reasoning tokens out of the same max_output_tokens as the answer,
    so an unparseable payload produced while thinking was on is not just ordinary
    flakiness: it is a sign the reasoning ate the allowance and left nothing for the
    JSON. `_chat_json` still retries exactly as before (that heals a flaky trailing
    comma too), but once this happens it also turns thinking off for the remaining
    attempts, so the retry gets the full ceiling for the answer alone."""

    @pytest.fixture(autouse=True)
    def _no_sleep(self, monkeypatch):
        monkeypatch.setattr(analysis.time, "sleep", lambda seconds: None)

    def test_an_unparseable_attempt_with_thinking_on_turns_thinking_off_for_the_retry(self, monkeypatch):
        client = _FakeClient(["not json at all", '{"ok": 1}'])
        monkeypatch.setattr(analysis, "_client", client)

        parsed, model = analysis.chat_json("prompt", 100, "demo", thinking=True)

        assert parsed == {"ok": 1}
        assert client.models.calls == 2
        first_config, second_config = client.models.configs
        # Absent means thinking was left on for the first attempt, exactly as asked.
        assert "thinking_config" not in first_config
        assert second_config["thinking_config"]["thinking_budget"] == 0

    def test_a_caller_with_thinking_off_is_unaffected_and_logs_no_downgrade(self, monkeypatch, caplog):
        client = _FakeClient(["not json at all", '{"ok": 1}'])
        monkeypatch.setattr(analysis, "_client", client)

        with caplog.at_level("WARNING", logger="analysis"):
            parsed, model = analysis.chat_json("prompt", 100, "demo", thinking=False)

        assert parsed == {"ok": 1}
        # Every attempt was already thinking-off; nothing here should look different
        # from a caller that never had a downgrade to trigger.
        for config in client.models.configs:
            assert config["thinking_config"]["thinking_budget"] == 0
        assert not any("turning thinking off" in message for message in caplog.messages)

    def test_all_attempts_unparseable_still_raises_the_incomplete_message(self, monkeypatch):
        # Same user-facing failure as before the downgrade existed; only the configs
        # sent along the way differ.
        client = _FakeClient(["not json at all"])
        monkeypatch.setattr(analysis, "_client", client)

        with pytest.raises(ValueError) as excinfo:
            analysis.chat_json("prompt", 100, "demo", thinking=True)

        assert str(excinfo.value) == analysis._INCOMPLETE
        assert client.models.calls == analysis.MAX_ATTEMPTS
        assert "thinking_config" not in client.models.configs[0]
        for config in client.models.configs[1:]:
            assert config["thinking_config"]["thinking_budget"] == 0

    def test_a_transient_api_exception_does_not_downgrade_thinking(self, monkeypatch):
        class _RaisesOnceThenValid:
            def __init__(self):
                self.calls = 0
                self.configs = []

            def generate_content(self, model, contents, config):
                self.calls += 1
                self.configs.append(config)
                if self.calls == 1:
                    raise Exception("temporary network blip")
                return _FakeResponse('{"ok": 1}')

        client = type("C", (), {"models": _RaisesOnceThenValid()})()
        monkeypatch.setattr(analysis, "_client", client)

        parsed, model = analysis.chat_json("prompt", 100, "demo", thinking=True)

        assert parsed == {"ok": 1}
        assert client.models.calls == 2
        for config in client.models.configs:
            assert "thinking_config" not in config

    def test_a_quota_error_still_retries_with_thinking_left_on(self, monkeypatch):
        class _QuotaOnceThenValid:
            def __init__(self):
                self.calls = 0
                self.configs = []

            def generate_content(self, model, contents, config):
                self.calls += 1
                self.configs.append(config)
                if self.calls == 1:
                    exc = Exception("429 RESOURCE_EXHAUSTED. Quota exceeded.")
                    exc.code = 429
                    raise exc
                return _FakeResponse('{"ok": 1}')

        client = type("C", (), {"models": _QuotaOnceThenValid()})()
        monkeypatch.setattr(analysis, "_client", client)

        parsed, model = analysis.chat_json("prompt", 100, "demo", thinking=True)

        assert parsed == {"ok": 1}
        for config in client.models.configs:
            assert "thinking_config" not in config

    def test_a_content_filter_block_still_fails_immediately_with_thinking_untouched(self, monkeypatch):
        class _BlockedModels:
            def __init__(self):
                self.calls = 0
                self.configs = []

            def generate_content(self, model, contents, config):
                self.calls += 1
                self.configs.append(config)
                response = _FakeResponse("")
                response.candidates = [_FakeCandidate("SAFETY")]
                return response

        client = type("C", (), {"models": _BlockedModels()})()
        monkeypatch.setattr(analysis, "_client", client)

        with pytest.raises(ValueError) as excinfo:
            analysis.chat_json("prompt", 100, "demo", thinking=True)

        assert str(excinfo.value) == analysis._CONTENT_BLOCKED
        assert client.models.calls == 1
        assert "thinking_config" not in client.models.configs[0]

    def test_the_happy_path_with_thinking_on_makes_exactly_one_call(self, monkeypatch):
        client = _FakeClient(['{"ok": 1}'])
        monkeypatch.setattr(analysis, "_client", client)

        parsed, model = analysis.chat_json("prompt", 100, "demo", thinking=True)

        assert parsed == {"ok": 1}
        assert client.models.calls == 1
        assert "thinking_config" not in client.models.configs[0]


class TestContentFilterIsNotRetried:
    """A safety block is a property of the text we sent. Retrying it three times
    spends a minute arriving at the same refusal and then reports it as temporary."""

    @pytest.fixture(autouse=True)
    def _no_sleep(self, monkeypatch):
        monkeypatch.setattr(analysis.time, "sleep", lambda seconds: None)

    class _Blocked:
        """A response the filter stopped: valid object, no usable text."""

        def __init__(self, finish="SAFETY", block_reason=None):
            self.text = ""
            self.candidates = [_FakeCandidate(finish)]
            if block_reason is not None:
                self.prompt_feedback = type("F", (), {"block_reason": block_reason})()

    @staticmethod
    def _client_returning(response):
        class Models:
            calls = 0

            def generate_content(self, model, contents, config):
                type(self).calls += 1
                return response

        return type("C", (), {"models": Models()})()

    @pytest.mark.parametrize("finish", ["SAFETY", "PROHIBITED_CONTENT", "RECITATION",
                                        "FinishReason.SAFETY"])
    def test_a_blocking_finish_reason_fails_immediately(self, monkeypatch, finish):
        client = self._client_returning(self._Blocked(finish))
        monkeypatch.setattr(analysis, "_client", client)

        with pytest.raises(ValueError) as excinfo:
            analysis.chat_json("prompt", 100, "demo")

        assert str(excinfo.value) == analysis._CONTENT_BLOCKED
        assert client.models.calls == 1

    def test_a_blocked_prompt_fails_immediately_too(self, monkeypatch):
        client = self._client_returning(self._Blocked(finish="STOP", block_reason="SAFETY"))
        monkeypatch.setattr(analysis, "_client", client)

        with pytest.raises(ValueError) as excinfo:
            analysis.chat_json("prompt", 100, "demo")

        assert str(excinfo.value) == analysis._CONTENT_BLOCKED
        assert client.models.calls == 1

    def test_the_message_names_the_fix_and_leaks_nothing(self):
        message = analysis._CONTENT_BLOCKED
        assert message[0].isupper() and message.endswith(".")
        assert "different communities" in message
        for jargon in ["SAFETY", "finish_reason", "block", "API", "http"]:
            assert jargon.lower() not in message.lower()

    def test_an_unspecified_block_reason_is_not_a_block(self, monkeypatch):
        response = self._Blocked(finish="STOP", block_reason="BLOCK_REASON_UNSPECIFIED")
        response.text = '{"ok": 1}'
        client = self._client_returning(response)
        monkeypatch.setattr(analysis, "_client", client)
        assert analysis.chat_json("prompt", 100, "demo")[0] == {"ok": 1}

    def test_an_ordinary_truncation_is_still_retried(self, monkeypatch):
        client = _FakeClient(["", '{"ok": 1}'])
        monkeypatch.setattr(analysis, "_client", client)
        assert analysis.chat_json("prompt", 100, "demo")[0] == {"ok": 1}
        assert client.models.calls == 2


def no_requirements(monkeypatch):
    """Stub the brief decomposition out with its own fail-open answer.

    classify_sample now makes one decomposition call before the triage, so any test
    that reaches selection with a non-empty query would otherwise make a live model
    call. Brief(query, ()) is exactly what the real function falls back to, so a
    test wired this way sees the behaviour a requirement-free brief gets.
    """
    monkeypatch.setattr(
        analysis, "decompose_brief",
        lambda query, profile="", **kwargs: analysis.Brief(query, ()),
    )


class TestRefitLoop:
    """A prompt the provider rejects shrinks THIS run's budget and nothing else."""

    @pytest.fixture(autouse=True)
    def _stub_decomposition(self, monkeypatch):
        no_requirements(monkeypatch)

    def _posts(self, count):
        return [
            {"kind": "post", "id": "p{}".format(i), "subreddit": "s",
             "title": "A title long enough to carry the whole idea on its own",
             "body": "A body with plenty of real text in it, past the substance bar.",
             "score": i, "comments": 0, "created_utc": 1700000000 + i}
            for i in range(count)
        ]

    def test_a_rejection_shrinks_the_sample_and_then_succeeds(self, monkeypatch):
        budgets = []
        attempts = {"n": 0}

        def fake_fit(posts, comments, subreddits, budget=None):
            budgets.append(budget)
            return list(posts)[:5], 200

        def fake_classify_items(items, subreddits, preview_chars=200):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise analysis.PayloadTooLarge("too big")
            return {"topics": [], "item_sentiments": [], "model": "m"}

        monkeypatch.setattr(analysis, "fit_sample", fake_fit)
        monkeypatch.setattr(analysis, "classify_items", fake_classify_items)
        monkeypatch.setattr(analysis, "rate_relevance", lambda *a, **k: None)

        analysis.classify_sample(self._posts(10), [], ["s"], "query", "")

        assert len(budgets) == 2
        assert budgets[0] == analysis.token_budget()
        assert budgets[1] == int(budgets[0] * 0.75)

    def test_the_shaved_budget_does_not_leak_into_the_next_run(self, monkeypatch):
        seen = []

        def fake_fit(posts, comments, subreddits, budget=None):
            seen.append(budget)
            return list(posts)[:5], 200

        monkeypatch.setattr(analysis, "fit_sample", fake_fit)
        monkeypatch.setattr(analysis, "rate_relevance", lambda *a, **k: None)
        monkeypatch.setattr(
            analysis, "classify_items",
            lambda items, subreddits, preview_chars=200: (_ for _ in ()).throw(
                analysis.PayloadTooLarge("too big")
            ),
        )

        with pytest.raises(ValueError):
            analysis.classify_sample(self._posts(10), [], ["s"], "query", "")
        first_run = list(seen)
        seen.clear()

        monkeypatch.setattr(
            analysis, "classify_items",
            lambda items, subreddits, preview_chars=200: {
                "topics": [], "item_sentiments": [], "model": "m"
            },
        )
        analysis.classify_sample(self._posts(10), [], ["s"], "query", "")

        assert first_run[0] == analysis.token_budget()
        assert first_run[-1] < first_run[0]          # it did shrink, within the run
        assert seen == [analysis.token_budget()]      # and the next run starts fresh

    def test_running_out_of_refits_raises_a_user_ready_sentence(self, monkeypatch):
        monkeypatch.setattr(analysis, "rate_relevance", lambda *a, **k: None)
        monkeypatch.setattr(
            analysis, "classify_items",
            lambda items, subreddits, preview_chars=200: (_ for _ in ()).throw(
                analysis.PayloadTooLarge("too big")
            ),
        )
        with pytest.raises(ValueError) as excinfo:
            analysis.classify_sample(self._posts(10), [], ["s"], "query", "")
        message = str(excinfo.value)
        assert message[0].isupper() and message.endswith(".")


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
        # The cap binds; the jitter above it is bounded by its own share, so the
        # worst case is still seconds rather than the ten minutes that was asked for.
        cap = analysis.MAX_RATE_LIMIT_WAIT_SECONDS
        assert cap <= wait <= cap * (1 + analysis.RETRY_JITTER_SHARE)

    def test_retry_wait_falls_back_to_the_default_ladder(self):
        first = analysis.RETRY_DELAYS_SECONDS[0]
        wait = analysis._retry_wait_seconds(Exception("no headers"), 0)
        assert first <= wait <= first * (1 + analysis.RETRY_JITTER_SHARE)


class TestRetryWaitsAreJittered:
    """Several triage calls are in flight at once, so identical waits are a problem.

    A rate limit hits the whole wave, every worker reads the same reset hint, and
    without jitter all of them sleep the same number of seconds and hit Vertex again
    in lockstep, which turns one 429 into a cycle of them.
    """

    class _Busy(Exception):
        response = type("R", (), {"headers": {"retry-after": "20"}})()

    def test_two_workers_on_the_same_failure_do_not_wake_together(self):
        waits = {analysis._retry_wait_seconds(self._Busy("busy"), 0) for _ in range(20)}
        # A single repeated value here is the bug: it means every worker in a wave
        # was handed the same wait.
        assert len(waits) > 1

    def test_the_jitter_only_ever_adds_and_stays_inside_its_share(self):
        base = min(20 + 0.5, analysis.MAX_RATE_LIMIT_WAIT_SECONDS)
        for _ in range(50):
            wait = analysis._retry_wait_seconds(self._Busy("busy"), 0)
            # Never below the provider's own hint: undercutting it would retry into
            # a limit that has not reset yet.
            assert base <= wait <= base * (1 + analysis.RETRY_JITTER_SHARE)

    def test_the_share_is_modest_enough_to_stay_inside_a_stage(self):
        assert 0 < analysis.RETRY_JITTER_SHARE <= 0.5


class TestRetriesStopAtTheirDeadline:
    """MAX_ATTEMPTS - 1 waits of MAX_RATE_LIMIT_WAIT_SECONDS outlast a whole stage.

    The call in flight cannot be interrupted, but the sleep before the next attempt
    can be skipped, and a batch that keeps retrying past the triage's window spends
    time the classification call is counting on to produce any report at all.
    """

    def _busy_client(self):
        class _AlwaysBusy:
            def __init__(self):
                self.calls = 0

            def generate_content(self, model, contents, config):
                self.calls += 1
                raise Exception("429 RESOURCE_EXHAUSTED: quota exceeded, retry later")

        return type("C", (), {"models": _AlwaysBusy()})()

    def test_a_rate_limited_call_stops_once_the_wait_would_overrun(self, monkeypatch):
        slept = []
        monkeypatch.setattr(analysis.time, "sleep", lambda seconds: slept.append(seconds))
        client = self._busy_client()
        monkeypatch.setattr(analysis, "_client", client)

        with pytest.raises(ValueError):
            analysis.chat_json(
                "prompt", 100, "relevance-triage", thinking=False,
                deadline=analysis.time.monotonic() + 0.01,
            )

        # One attempt, no sleep: the remaining attempts belonged to a window that
        # had already closed.
        assert client.models.calls == 1
        assert slept == []

    def test_no_deadline_spends_every_attempt_exactly_as_before(self, monkeypatch):
        slept = []
        monkeypatch.setattr(analysis.time, "sleep", lambda seconds: slept.append(seconds))
        client = self._busy_client()
        monkeypatch.setattr(analysis, "_client", client)

        with pytest.raises(ValueError):
            analysis.chat_json("prompt", 100, "relevance-triage", thinking=False)

        assert client.models.calls == analysis.MAX_ATTEMPTS
        assert len(slept) == analysis.MAX_ATTEMPTS - 1

    def test_a_generous_deadline_leaves_the_retries_alone(self, monkeypatch):
        monkeypatch.setattr(analysis.time, "sleep", lambda seconds: None)
        client = self._busy_client()
        monkeypatch.setattr(analysis, "_client", client)

        with pytest.raises(ValueError):
            analysis.chat_json(
                "prompt", 100, "relevance-triage", thinking=False,
                deadline=analysis.time.monotonic() + 3600,
            )

        assert client.models.calls == analysis.MAX_ATTEMPTS

    def test_the_triage_hands_its_own_deadline_to_the_call(self):
        seen = {}
        items = [
            {"kind": "post", "id": "p1", "subreddit": "s", "title": "t", "body": "b",
             "score": 1, "created_utc": 1}
        ]
        soft_deadline = time.monotonic() + 3600

        def fake_chat(prompt, max_tokens, label, thinking=True, deadline=None):
            seen["deadline"] = deadline
            return {"ratings": []}, "m"

        analysis.rate_relevance(items, "q", "p", chat=fake_chat, soft_deadline=soft_deadline)
        assert seen["deadline"] == soft_deadline

    def test_the_brief_decomposition_is_bounded_by_the_same_window(self):
        # It shares TRIAGE_ALLOWANCE_SECONDS with the triage it informs, so a rate
        # limited decomposition that slept out its attempts would leave the triage
        # nothing to run in.
        seen = {}

        def fake_chat(prompt, max_tokens, label, thinking=True, deadline=None):
            seen[label] = deadline
            return {"topic": "t", "requirements": []}, "m"

        analysis.decompose_brief("a brief", "", chat=fake_chat, soft_deadline=1234.5)
        assert seen["brief-decomposition"] == 1234.5

    def test_the_selection_hands_the_decomposition_its_deadline(self, monkeypatch):
        seen = {}

        def fake_decompose(query, profile="", chat=None, soft_deadline=None):
            seen["soft_deadline"] = soft_deadline
            return analysis.Brief(query, ())

        monkeypatch.setattr(analysis, "decompose_brief", fake_decompose)
        monkeypatch.setattr(analysis, "rate_relevance", lambda *a, **k: {})
        posts = [
            {"kind": "post", "id": "p1", "subreddit": "s", "score": 1, "comments": 0,
             "title": "A title long enough to carry the whole idea on its own",
             "body": "A body with plenty of real text in it, past the substance bar.",
             "created_utc": 1700000000}
        ]
        soft_deadline = time.monotonic() + 3600
        analysis._select_sample(posts, [], "a query", "", soft_deadline)
        assert seen["soft_deadline"] == soft_deadline

    def test_a_decomposition_fake_that_predates_the_deadline_still_works(self, monkeypatch):
        monkeypatch.setattr(
            analysis, "decompose_brief", lambda query, profile="": analysis.Brief(query, ())
        )
        monkeypatch.setattr(analysis, "rate_relevance", lambda *a, **k: {})
        posts = [
            {"kind": "post", "id": "p1", "subreddit": "s", "score": 1, "comments": 0,
             "title": "A title long enough to carry the whole idea on its own",
             "body": "A body with plenty of real text in it, past the substance bar.",
             "created_utc": 1700000000}
        ]
        _, _, selection = analysis._select_sample(
            posts, [], "a query", "", time.monotonic() + 3600
        )
        assert selection["method"] == "relevance+engagement"

    def test_a_chat_fake_that_predates_the_keyword_is_still_called(self):
        # The injected-callable contract: an older fake simply does not receive it.
        calls = []

        def old_chat(prompt, max_tokens, label, thinking=True):
            calls.append(label)
            return {"ratings": []}, "m"

        items = [
            {"kind": "post", "id": "p1", "subreddit": "s", "title": "t", "body": "b",
             "score": 1, "created_utc": 1}
        ]
        analysis.rate_relevance(
            items, "q", "p", chat=old_chat, soft_deadline=time.monotonic() + 3600
        )
        assert calls == ["relevance-triage"]


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

    def test_posts_come_first_and_both_lists_keep_their_given_order(self):
        # select_items is a pure slicer: rank_items has already decided the order,
        # so reordering here would silently undo the ranking.
        items = analysis.select_items(self._posts(3), self._comments(3))
        assert [item["kind"] for item in items] == ["post"] * 3 + ["comment"] * 3
        assert [item["score"] for item in items[3:]] == [0, 1, 2]

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

    def test_the_budget_is_the_configured_one_with_a_safety_margin(self):
        assert analysis.token_budget() == int(
            analysis.DEFAULT_TOKEN_BUDGET * analysis.BUDGET_SAFETY
        )

    def test_the_budget_has_no_module_level_state_to_race_on(self):
        # A "learned limit" global used to shrink here and stay shrunk for every
        # later run in the same process, and race between two concurrent ones.
        assert not hasattr(analysis, "_token_limit")
        assert analysis.token_budget() == analysis.token_budget()

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


class TestScoring:
    """The deterministic half of selection: engagement, recency, substance, fairness."""

    def _post(self, id, subreddit="subone", score=10, comments=5, age_days=0.0,
              title="A title long enough to carry the whole idea on its own",
              body="A body with plenty of real text in it, well past the substance bar.",
              now=1700000000.0):
        return {
            "kind": "post", "id": id, "subreddit": subreddit, "title": title,
            "body": body, "score": score, "comments": comments,
            "created_utc": int(now - age_days * 86400),
        }

    def _comment(self, id, subreddit="subone", score=10, age_days=0.0, now=1700000000.0):
        return {
            "kind": "comment", "id": id, "subreddit": subreddit, "title": "",
            "body": "comment body", "score": score,
            "created_utc": int(now - age_days * 86400),
        }

    NOW = 1700000000.0

    def test_recency_halves_at_the_half_life(self):
        fresh = analysis._recency_decay(self.NOW, self.NOW)
        aged = analysis._recency_decay(
            self.NOW - analysis.RECENCY_HALF_LIFE_DAYS * 86400, self.NOW
        )
        assert fresh == 1.0
        assert aged == pytest.approx(0.5)

    def test_missing_timestamp_gets_the_neutral_middle(self):
        assert analysis._recency_decay(None, self.NOW) == 0.5
        assert analysis._recency_decay(0, self.NOW) == 0.5
        assert analysis._recency_decay("nope", self.NOW) == 0.5

    def test_engagement_is_normalised_within_each_subreddit(self):
        # 40 upvotes in a quiet community outranks 400 in a loud one when the loud
        # community's own ceiling is 4000.
        small_star = self._post("a", subreddit="quiet", score=40, comments=0)
        big_mid = self._post("b", subreddit="loud", score=400, comments=0)
        big_star = self._post("c", subreddit="loud", score=4000, comments=0)
        scores = analysis._base_scores([small_star, big_mid, big_star], self.NOW)
        assert scores[0] > scores[1]
        assert scores[2] == pytest.approx(scores[0])

    def test_zero_engagement_everywhere_is_neutral_not_zero(self):
        posts = [self._post("a", score=0, comments=0)]
        scores = analysis._base_scores(posts, self.NOW)
        assert scores[0] > 0

    def test_substance_rejects_a_link_drop_with_a_short_title(self):
        assert not analysis._has_substance(
            {"title": "look at this", "body": "https://example.com/thing"}
        )

    def test_substance_accepts_a_real_body_or_a_carrying_title(self):
        assert analysis._has_substance({"title": "x", "body": "There is a real story in here."})
        assert analysis._has_substance(
            {"title": "A title long enough to carry the whole idea alone", "body": ""}
        )

    def test_relevance_zero_is_gated_behind_everything_eligible(self):
        viral_offtopic = self._post("off", score=5000, comments=900)
        quiet_ontopic = self._post("on", score=2, comments=0)
        ranked_posts, _ = analysis.rank_items(
            [viral_offtopic, quiet_ontopic], [],
            relevance={("post", "off"): 0, ("post", "on"): 2}, now=self.NOW,
        )
        assert [post["id"] for post in ranked_posts] == ["on", "off"]

    def test_substance_failed_posts_sink_to_the_backfill_tier(self):
        link_drop = self._post("drop", score=5000, comments=900, title="look", body="https://x.co")
        real = self._post("real", score=1, comments=0)
        ranked_posts, _ = analysis.rank_items([link_drop, real], [], now=self.NOW)
        assert [post["id"] for post in ranked_posts] == ["real", "drop"]

    def test_untriaged_items_default_to_maybe_relevant(self):
        strong = self._post("strong", score=100, comments=50)
        weak = self._post("weak", score=1, comments=0)
        ranked_posts, _ = analysis.rank_items([weak, strong], [], relevance=None, now=self.NOW)
        assert [post["id"] for post in ranked_posts] == ["strong", "weak"]

    def test_fairness_holds_for_every_prefix_with_two_communities(self):
        posts = [
            self._post("loud{}".format(i), subreddit="loud", score=1000 - i)
            for i in range(10)
        ] + [self._post("quiet{}".format(i), subreddit="quiet", score=10 - i) for i in range(10)]
        ranked_posts, _ = analysis.rank_items(posts, [], now=self.NOW)
        for size in range(1, len(ranked_posts) + 1):
            prefix = ranked_posts[:size]
            loud = sum(1 for post in prefix if post["subreddit"] == "loud")
            cap = max(1, -(-size // 2))  # ceil(size / 2)
            assert loud <= cap, "prefix {} holds {} loud posts".format(size, loud)

    def test_a_single_community_is_not_capped(self):
        posts = [self._post("p{}".format(i), score=100 - i) for i in range(6)]
        ranked_posts, _ = analysis.rank_items(posts, [], now=self.NOW)
        assert len(ranked_posts) == 6

    def test_ranking_is_deterministic_on_ties(self):
        posts = [self._post("p{}".format(i), score=5, comments=0) for i in range(8)]
        first, _ = analysis.rank_items(list(posts), [], now=self.NOW)
        second, _ = analysis.rank_items(list(posts), [], now=self.NOW)
        assert [post["id"] for post in first] == [post["id"] for post in second]

    def test_comments_are_ranked_but_never_substance_gated(self):
        good = self._comment("good", score=50)
        quiet = self._comment("quiet", score=0)
        _, ranked_comments = analysis.rank_items([], [good, quiet], now=self.NOW)
        assert [comment["id"] for comment in ranked_comments] == ["good", "quiet"]

    def test_the_fairness_cap_carries_across_the_eligible_backfill_seam(self):
        # "loud" fills the eligible tier on its own. Ordering each tier separately
        # restarted its count at zero, so it also took the head of the backfill tier
        # on raw score. With one shared cap, the slots at the seam go to the
        # community that is six picks behind.
        loud_eligible = [
            self._post("loud{}".format(i), subreddit="loud", score=1000 - i) for i in range(6)
        ]
        loud_backfill = [
            self._post("louddrop{}".format(i), subreddit="loud", score=900,
                       title="look", body="https://x.co")
            for i in range(4)
        ]
        quiet_backfill = [
            self._post("quietdrop{}".format(i), subreddit="quiet", score=1,
                       title="look", body="https://x.co")
            for i in range(4)
        ]
        ranked_posts, _ = analysis.rank_items(
            loud_eligible + loud_backfill + quiet_backfill, [], now=self.NOW
        )
        ids = [post["id"] for post in ranked_posts]
        assert ids[:6] == ["loud{}".format(i) for i in range(6)]
        # Every quiet backfill item comes before any loud one, despite scoring 1 to
        # their 900, because loud has already had six of the six picks so far.
        assert all(name.startswith("quietdrop") for name in ids[6:10])
        assert all(name.startswith("louddrop") for name in ids[10:])

    def test_no_backfill_item_outranks_an_eligible_one(self):
        link_drop = self._post("drop", subreddit="quiet", score=5000, comments=900,
                               title="look", body="https://x.co")
        real = self._post("real", subreddit="loud", score=1, comments=0)
        ranked_posts, _ = analysis.rank_items([link_drop, real], [], now=self.NOW)
        assert [post["id"] for post in ranked_posts] == ["real", "drop"]


class TestRelevanceKeying:
    """A rating belongs to exactly one item. Keying by the raw id alone handed one
    item's rating to another whenever ids collided or were missing."""

    def _post(self, id, score=10):
        return {
            "kind": "post", "id": id, "subreddit": "s", "score": score, "comments": 0,
            "title": "A title long enough to carry the whole idea on its own",
            "body": "A body with plenty of real text in it, past the substance bar.",
            "created_utc": 1700000000,
        }

    def _comment(self, id, score=10):
        return {"kind": "comment", "id": id, "subreddit": "s", "score": score,
                "title": "", "body": "a comment", "created_utc": 1700000000}

    def test_a_post_and_a_comment_sharing_an_id_are_rated_separately(self):
        post = self._post("abc", score=1)
        comment = self._comment("abc", score=1)
        # The post is off topic, the comment is on topic, and both carry id "abc".
        relevance = {("post", "abc"): 0, ("comment", "abc"): 2}
        ranked_posts, ranked_comments = analysis.rank_items(
            [post, self._post("other", score=1)], [comment], relevance, now=1700000000.0
        )
        # The rated-0 post sinks to the backfill tier; the comment is untouched.
        assert [item["id"] for item in ranked_posts] == ["other", "abc"]
        assert [item["id"] for item in ranked_comments] == ["abc"]

    def test_items_with_no_id_do_not_share_one_rating(self):
        first = self._post("", score=1)
        second = self._post("", score=1)
        relevance = {("post", "#0"): 0, ("post", "#1"): 2}
        ranked_posts, _ = analysis.rank_items([first, second], [], relevance, now=1700000000.0)
        # Only the second survives into the eligible tier, so it must come first.
        assert ranked_posts[0] is second

    def test_the_key_namespaces_the_kind_and_falls_back_to_the_position(self):
        assert analysis.relevance_key("post", {"id": "x"}, 3) == ("post", "x")
        assert analysis.relevance_key("comment", {"id": "x"}, 3) == ("comment", "x")
        assert analysis.relevance_key("post", {"id": ""}, 3) == ("post", "#3")
        assert analysis.relevance_key("post", {}, 0) == ("post", "#0")


class TestUntriagedTier:
    """An item the triage never saw is not the same as one a model looked at and
    called adjacent, and it must not rank as if it were."""

    @pytest.fixture(autouse=True)
    def _stub_decomposition(self, monkeypatch):
        no_requirements(monkeypatch)

    def _post(self, id, score=10):
        return {
            "kind": "post", "id": id, "subreddit": "s", "score": score, "comments": 0,
            "title": "A title long enough to carry the whole idea on its own",
            "body": "A body with plenty of real text in it, past the substance bar.",
            "created_utc": 1700000000,
        }

    NOW = 1700000000.0

    def test_a_rated_one_item_outranks_an_equal_untriaged_one(self):
        rated = self._post("rated")
        unrated = self._post("unrated")
        ranked_posts, _ = analysis.rank_items(
            [unrated, rated], [], relevance={("post", "rated"): 1}, now=self.NOW
        )
        assert [post["id"] for post in ranked_posts] == ["rated", "unrated"]

    def test_a_rated_zero_item_still_ranks_below_an_untriaged_one(self):
        zero = self._post("zero", score=5000)
        unrated = self._post("unrated", score=1)
        ranked_posts, _ = analysis.rank_items(
            [zero, unrated], [], relevance={("post", "zero"): 0}, now=self.NOW
        )
        assert [post["id"] for post in ranked_posts] == ["unrated", "zero"]

    def test_the_untriaged_weight_sits_between_zero_and_a_rated_one(self):
        assert 0 < analysis.UNTRIAGED_WEIGHT < analysis.RELEVANCE_WEIGHTS[1]

    def test_a_fully_untriaged_pool_keeps_its_engagement_order(self):
        strong = self._post("strong", score=100)
        weak = self._post("weak", score=1)
        ranked_posts, _ = analysis.rank_items([weak, strong], [], relevance=None, now=self.NOW)
        assert [post["id"] for post in ranked_posts] == ["strong", "weak"]

    def test_the_triage_pool_is_capped_and_the_rest_is_untriaged(self, monkeypatch):
        monkeypatch.setattr(analysis, "TRIAGE_MAX_POSTS", 3)
        monkeypatch.setattr(analysis, "TRIAGE_MAX_COMMENTS", 0)
        seen = {}

        def fake_rate(items, query, profile):
            seen["count"] = len(items)
            return {position: 2 for position in range(1, len(items) + 1)}

        monkeypatch.setattr(analysis, "rate_relevance", fake_rate)
        posts = [self._post("p{}".format(i), score=100 - i) for i in range(10)]
        ranked_posts, _, selection = analysis._select_sample(posts, [], "query", "")
        assert seen["count"] == 3
        assert selection["triaged"] == 3
        # The three rated-2 posts lead; the seven the triage never saw follow.
        assert [post["id"] for post in ranked_posts[:3]] == ["p0", "p1", "p2"]


class TestRateRelevance:
    """The triage call: batching, sanitisation and fail-open behaviour."""

    def _items(self, count):
        return [
            {"kind": "post", "id": "p{}".format(i), "subreddit": "s",
             "title": "title {}".format(i), "body": "body", "score": i, "created_utc": 1}
            for i in range(count)
        ]

    def test_happy_path_returns_every_rating_keyed_by_global_position(self):
        calls = []

        def fake_chat(prompt, max_tokens, label, thinking=True):
            calls.append({"prompt": prompt, "label": label, "thinking": thinking})
            return {"ratings": [{"i": 1, "r": 2}, {"i": 2, "r": 0}, {"i": 3, "r": 1}]}, "m"

        ratings = analysis.rate_relevance(self._items(3), "q", "p", chat=fake_chat)
        # A brief with no requirements carries no qualifier characters, so every
        # verdict is the topic rating and an empty string.
        assert ratings == {
            1: analysis.Verdict(2, ""),
            2: analysis.Verdict(0, ""),
            3: analysis.Verdict(1, ""),
        }
        assert calls[0]["label"] == "relevance-triage"
        assert calls[0]["thinking"] is False

    def test_malformed_entries_fail_open_to_one(self):
        def fake_chat(prompt, max_tokens, label, thinking=True):
            return {
                "ratings": [
                    {"i": 1, "r": 9},          # out-of-range rating
                    {"i": "2", "r": 0},        # stringly index
                    {"i": 99, "r": 0},          # index outside the batch
                    {"i": True, "r": 0},        # bool masquerading as int
                    "not even a dict",
                ]
            }, "m"

        ratings = analysis.rate_relevance(self._items(3), "q", "p", chat=fake_chat)
        assert ratings == {position: analysis.Verdict(1, "") for position in (1, 2, 3)}

    def test_duplicate_indices_first_wins(self):
        def fake_chat(prompt, max_tokens, label, thinking=True):
            return {"ratings": [{"i": 1, "r": 2}, {"i": 1, "r": 0}]}, "m"

        assert analysis.rate_relevance(self._items(1), "q", "p", chat=fake_chat) == {
            1: analysis.Verdict(2, "")
        }

    def test_batches_split_at_the_batch_size_with_global_indices(self):
        prompts = []

        def fake_chat(prompt, max_tokens, label, thinking=True):
            prompts.append(prompt)
            return {"ratings": []}, "m"

        count = analysis.TRIAGE_BATCH_SIZE + 10
        ratings = analysis.rate_relevance(self._items(count), "q", "p", chat=fake_chat)
        assert len(prompts) == 2
        second_start = analysis.TRIAGE_BATCH_SIZE + 1
        assert "{}. [post]".format(second_start) in prompts[1]
        assert set(ratings) == set(range(1, count + 1))
        assert set(ratings.values()) == {analysis.Verdict(1, "")}

    def test_one_failed_batch_defaults_only_that_batch(self):
        def fake_chat(prompt, max_tokens, label, thinking=True):
            if "[post] title 0" in prompt:
                raise ValueError("boom")
            return {"ratings": [{"i": analysis.TRIAGE_BATCH_SIZE + 1, "r": 2}]}, "m"

        count = analysis.TRIAGE_BATCH_SIZE + 1
        ratings = analysis.rate_relevance(self._items(count), "q", "p", chat=fake_chat)
        assert ratings == {analysis.TRIAGE_BATCH_SIZE + 1: analysis.Verdict(2, "")}

    def test_every_batch_failing_returns_none(self):
        def fake_chat(prompt, max_tokens, label, thinking=True):
            raise ValueError("boom")

        assert analysis.rate_relevance(self._items(5), "q", "p", chat=fake_chat) is None

    def test_no_items_means_no_calls_and_an_empty_result(self):
        def fake_chat(prompt, max_tokens, label, thinking=True):
            raise AssertionError("should not be called")

        assert analysis.rate_relevance([], "q", "p", chat=fake_chat) == {}

    def test_output_reservation_never_exceeds_the_ceiling(self):
        assert analysis._triage_output_tokens(100000) == analysis.TRIAGE_MAX_TOKENS
        assert analysis._triage_output_tokens(0) > 0

    def test_the_output_reservation_matches_the_measured_per_item_cost(self):
        # 45 is what the sibling classification call measures; the old 30 came from
        # nowhere in particular and under-reserved every batch.
        assert analysis.TRIAGE_OUTPUT_TOKENS_PER_ITEM == 45
        assert analysis._triage_output_tokens(10) == 45 * 10 + 300

    def test_previews_are_long_enough_to_judge_relevance_on(self):
        item = {"kind": "post", "title": "T", "body": "b" * 1000}
        assert len(analysis._triage_preview(item)) == analysis.TRIAGE_PREVIEW_CHARS
        assert analysis.TRIAGE_PREVIEW_CHARS >= 300

    def test_a_batch_fits_the_triage_share_of_the_budget(self):
        size = analysis._triage_batch_size("a query", "a profile")
        assert 0 < size <= analysis.TRIAGE_BATCH_SIZE
        batch = self._items(size)
        estimate = analysis.estimate_tokens(
            analysis._triage_prompt(batch, "a query", "a profile", 1)
        ) + analysis._triage_output_tokens(size)
        assert estimate <= int(analysis.token_budget() * analysis.TRIAGE_BUDGET_SHARE)

    def test_a_tiny_budget_shrinks_the_batch_rather_than_overflowing_it(self, monkeypatch):
        monkeypatch.setattr(analysis, "token_budget", lambda: 4000)
        size = analysis._triage_batch_size("q", "p")
        assert 1 <= size < analysis.TRIAGE_BATCH_SIZE

    def test_batches_are_split_at_the_budgeted_size(self):
        prompts = []

        def fake_chat(prompt, max_tokens, label, thinking=True):
            prompts.append(prompt)
            return {"ratings": []}, "m"

        size = analysis._triage_batch_size("q", "p")
        analysis.rate_relevance(self._items(size + 1), "q", "p", chat=fake_chat)
        assert len(prompts) == 2


class TestTriageRunsBatchesInParallel:
    """The batches are independent, so they go out together and merge by union.

    Independence is the whole licence for this: indices are global, the merge is a
    dict update, and a failure costs only its own batch's items. What these tests
    pin down is that the parallelism is real, that the merge does not depend on
    which call lands first, and that a failure inside one worker still degrades to
    the untriaged tier instead of taking the run with it.
    """

    def _items(self, count):
        return [
            {"kind": "post", "id": "p{}".format(i), "subreddit": "s",
             "title": "title {}".format(i), "body": "body", "score": i, "created_utc": 1}
            for i in range(count)
        ]

    @pytest.fixture(autouse=True)
    def _small_batches(self, monkeypatch):
        """Two items per batch, so a handful of items is a handful of batches."""
        monkeypatch.setattr(analysis, "_triage_batch_size", lambda *args, **kwargs: 2)

    def test_the_batches_are_genuinely_in_flight_at_the_same_time(self):
        # A barrier is the only honest test of concurrency: sequential calls can
        # never all reach it, so the wait times out and breaks the barrier.
        barrier = threading.Barrier(3, timeout=10)

        def fake_chat(prompt, max_tokens, label, thinking=True):
            barrier.wait()
            return {"ratings": []}, "m"

        ratings = analysis.rate_relevance(self._items(6), "q", "p", chat=fake_chat)
        assert set(ratings) == set(range(1, 7))

    def test_the_worker_count_is_capped_and_never_exceeds_the_batch_count(self):
        live = {"now": 0, "peak": 0}
        lock = threading.Lock()
        release = threading.Event()

        def fake_chat(prompt, max_tokens, label, thinking=True):
            with lock:
                live["now"] += 1
                live["peak"] = max(live["peak"], live["now"])
            # Every worker holds its slot until the last one that can start has,
            # so `peak` is the real ceiling rather than a scheduling accident.
            release.wait(timeout=10)
            with lock:
                live["now"] -= 1
            return {"ratings": []}, "m"

        items = self._items(2 * (analysis.TRIAGE_MAX_WORKERS + 3))

        def unblock():
            while live["peak"] < analysis.TRIAGE_MAX_WORKERS:
                time.sleep(0.01)
            release.set()

        watcher = threading.Thread(target=unblock)
        watcher.start()
        try:
            analysis.rate_relevance(items, "q", "p", chat=fake_chat)
        finally:
            release.set()
            watcher.join()

        assert live["peak"] == analysis.TRIAGE_MAX_WORKERS

    def test_results_merge_by_union_whatever_order_they_land_in(self):
        # The last batch answers first and the first answers last, which is exactly
        # the ordering a real fan-out produces and the one a positional merge would
        # get wrong.
        def fake_chat(prompt, max_tokens, label, thinking=True):
            first = int(prompt.split("[post]")[0].strip().split("\n")[-1].rstrip("."))
            if first == 1:
                time.sleep(0.05)
            return {"ratings": [{"i": first, "r": 2}, {"i": first + 1, "r": 0}]}, "m"

        ratings = analysis.rate_relevance(self._items(6), "q", "p", chat=fake_chat)
        assert ratings == {
            1: analysis.Verdict(2, ""), 2: analysis.Verdict(0, ""),
            3: analysis.Verdict(2, ""), 4: analysis.Verdict(0, ""),
            5: analysis.Verdict(2, ""), 6: analysis.Verdict(0, ""),
        }

    def test_one_failed_batch_costs_only_its_own_items(self):
        def fake_chat(prompt, max_tokens, label, thinking=True):
            if "[post] title 2" in prompt:
                raise ValueError("boom")
            return {"ratings": [{"i": 1, "r": 2}, {"i": 2, "r": 2},
                                {"i": 5, "r": 2}, {"i": 6, "r": 2}]}, "m"

        ratings = analysis.rate_relevance(self._items(6), "q", "p", chat=fake_chat)
        # The failed batch (items 3 and 4) contributes nothing at all, so those two
        # fall to the untriaged tier in rank_items rather than being rated 1 here.
        assert set(ratings) == {1, 2, 5, 6}
        assert set(ratings.values()) == {analysis.Verdict(2, "")}

    def test_a_payload_rejection_inside_a_worker_is_survived_like_any_failure(self):
        def fake_chat(prompt, max_tokens, label, thinking=True):
            if "[post] title 0" in prompt:
                raise analysis.PayloadTooLarge("too big")
            return {"ratings": []}, "m"

        ratings = analysis.rate_relevance(self._items(4), "q", "p", chat=fake_chat)
        assert set(ratings) == {3, 4}

    def test_every_batch_failing_still_returns_none_for_the_deterministic_fallback(self):
        def fake_chat(prompt, max_tokens, label, thinking=True):
            raise ValueError("boom")

        assert analysis.rate_relevance(self._items(6), "q", "p", chat=fake_chat) is None

    def test_an_unexpected_exception_still_reaches_the_caller(self):
        def fake_chat(prompt, max_tokens, label, thinking=True):
            raise RuntimeError("not a model failure at all")

        with pytest.raises(RuntimeError):
            analysis.rate_relevance(self._items(6), "q", "p", chat=fake_chat)

    def test_an_aborting_run_stops_paying_for_the_batches_behind_it(self, monkeypatch):
        # `pool.map` submits every batch up front, so a run on its way out used to
        # issue all of them and throw the answers away. The workers check an abort
        # flag instead, which is the only thing that still bites once there is
        # nothing left to cancel.
        monkeypatch.setattr(analysis, "TRIAGE_MAX_WORKERS", 2)
        calls = []
        lock = threading.Lock()

        def fake_chat(prompt, max_tokens, label, thinking=True):
            if "[post] title 0" in prompt:
                raise RuntimeError("not a model failure at all")
            with lock:
                calls.append(prompt)
            # Slower than the raise above, so the flag is set while this is in
            # flight and the batches behind it never start.
            time.sleep(0.02)
            return {"ratings": []}, "m"

        with pytest.raises(RuntimeError):
            analysis.rate_relevance(self._items(24), "q", "p", chat=fake_chat)

        # 12 batches, of which one raised and at most the wave it was in got issued.
        assert len(calls) <= 3

    def test_a_batch_dropped_by_a_model_failure_aborts_nothing(self):
        # The contract the abort flag must not disturb: a ValueError is this
        # function's own business, so every other batch still goes out.
        def fake_chat(prompt, max_tokens, label, thinking=True):
            if "[post] title 0" in prompt:
                raise ValueError("the model failed this batch")
            return {"ratings": []}, "m"

        ratings = analysis.rate_relevance(self._items(6), "q", "p", chat=fake_chat)
        assert set(ratings) == {3, 4, 5, 6}

    def test_every_batch_is_issued_exactly_once(self):
        seen = []
        lock = threading.Lock()

        def fake_chat(prompt, max_tokens, label, thinking=True):
            with lock:
                seen.append(prompt)
            return {"ratings": []}, "m"

        analysis.rate_relevance(self._items(9), "q", "p", chat=fake_chat)
        assert len(seen) == 5
        assert len(set(seen)) == 5

    def test_a_single_batch_needs_no_pool_at_all(self, monkeypatch):
        threads = []

        def fake_chat(prompt, max_tokens, label, thinking=True):
            threads.append(threading.current_thread().name)
            return {"ratings": []}, "m"

        analysis.rate_relevance(self._items(2), "q", "p", chat=fake_chat)
        assert threads == [threading.current_thread().name]


class TestTriageStopsSubmittingWhenOutOfTime:
    """The deadline is checked inside each worker, right before its own call.

    A batch that was queued while there was time can sit behind the wave in front
    of it for longer than the run has left, so checking only at submission time
    would let the last wave overrun the reserve the analysis calls depend on.
    """

    def _items(self, count):
        return [
            {"kind": "post", "id": "p{}".format(i), "subreddit": "s",
             "title": "title {}".format(i), "body": "body", "score": i, "created_utc": 1}
            for i in range(count)
        ]

    @pytest.fixture(autouse=True)
    def _small_batches(self, monkeypatch):
        monkeypatch.setattr(analysis, "_triage_batch_size", lambda *args, **kwargs: 2)

    def _fake_clock(self, monkeypatch):
        """A monotonic clock the test moves by hand, shared across the workers."""
        clock = {"now": 1000.0}
        monkeypatch.setattr(analysis.time, "monotonic", lambda: clock["now"])
        return clock

    def test_a_batch_queued_in_time_is_skipped_once_the_clock_runs_out(self, monkeypatch):
        clock = self._fake_clock(monkeypatch)
        # One worker, so the batches run in order and the clock can be spent
        # between two of them exactly as a slow first wave would spend it.
        monkeypatch.setattr(analysis, "TRIAGE_MAX_WORKERS", 1)
        calls = []

        def fake_chat(prompt, max_tokens, label, thinking=True):
            calls.append(prompt)
            clock["now"] += 40
            start = 2 * len(calls) - 1
            return {"ratings": [{"i": start, "r": 2}, {"i": start + 1, "r": 2}]}, "m"

        ratings = analysis.rate_relevance(
            self._items(8), "q", "p", chat=fake_chat, soft_deadline=clock["now"] + 50,
        )

        # Two batches fitted; the rest were skipped rather than issued.
        assert len(calls) == 2
        assert set(ratings) == {1, 2, 3, 4}

    def test_a_skipped_batch_is_not_a_failed_batch(self, monkeypatch):
        clock = self._fake_clock(monkeypatch)
        monkeypatch.setattr(analysis, "TRIAGE_MAX_WORKERS", 1)

        def fake_chat(prompt, max_tokens, label, thinking=True):
            clock["now"] += 100
            return {"ratings": [{"i": 1, "r": 2}, {"i": 2, "r": 1}]}, "m"

        ratings = analysis.rate_relevance(
            self._items(8), "q", "p", chat=fake_chat, soft_deadline=clock["now"] + 50,
        )
        # One batch came back and the other three never went out. That is a degraded
        # ranking, not the None that means "triage failed, select deterministically".
        assert ratings == {1: analysis.Verdict(2, ""), 2: analysis.Verdict(1, "")}

    def test_the_untriaged_count_is_reported_once_with_the_batches_that_landed(
        self, monkeypatch, caplog
    ):
        clock = self._fake_clock(monkeypatch)
        monkeypatch.setattr(analysis, "TRIAGE_MAX_WORKERS", 1)

        def fake_chat(prompt, max_tokens, label, thinking=True):
            clock["now"] += 100
            return {"ratings": []}, "m"

        with caplog.at_level("WARNING", logger="analysis"):
            analysis.rate_relevance(
                self._items(7), "q", "p", chat=fake_chat,
                soft_deadline=clock["now"] + 50,
            )

        messages = [record.getMessage() for record in caplog.records]
        # 4 batches (2, 2, 2, 1), one of which landed, so 5 items stay untriaged.
        assert any("1 of 4 triage batch(es) came back" in message for message in messages)
        assert any("5 items stay untriaged" in message for message in messages)

    def test_a_deadline_already_past_issues_nothing_and_fails_nothing(self, monkeypatch):
        def explode(*args, **kwargs):
            raise AssertionError("no batch may be issued past the deadline")

        ratings = analysis.rate_relevance(
            self._items(8), "q", "p", chat=explode,
            soft_deadline=time.monotonic() - 1,
        )
        assert ratings == {}

    def test_a_generous_deadline_reads_the_whole_pool(self):
        seen = []
        lock = threading.Lock()

        def fake_chat(prompt, max_tokens, label, thinking=True):
            with lock:
                seen.append(prompt)
            return {"ratings": []}, "m"

        ratings = analysis.rate_relevance(
            self._items(8), "q", "p", chat=fake_chat,
            soft_deadline=time.monotonic() + 3600,
        )
        assert len(seen) == 4
        assert set(ratings) == set(range(1, 9))


class TestSelectSample:
    """classify_sample's selection step: triage wiring and the deterministic fallback."""

    @pytest.fixture(autouse=True)
    def _stub_decomposition(self, monkeypatch):
        no_requirements(monkeypatch)

    def _posts(self, count):
        return [
            {"kind": "post", "id": "p{}".format(i), "subreddit": "s",
             "title": "A title long enough to carry the whole idea on its own",
             "body": "A body with plenty of real text in it, past the substance bar.",
             "score": i, "comments": 0, "created_utc": 1700000000 + i}
            for i in range(count)
        ]

    def test_triage_failure_falls_back_to_deterministic_selection(self, monkeypatch):
        monkeypatch.setattr(analysis, "rate_relevance", lambda *a, **k: None)
        posts, comments, selection = analysis._select_sample(self._posts(5), [], "query", "")
        assert len(posts) == 5 and comments == []
        assert selection["method"] == "deterministic-fallback"
        assert selection["triaged"] == 0

    def test_an_empty_query_skips_the_triage_call_entirely(self, monkeypatch):
        def explode(*a, **k):
            raise AssertionError("triage must not be called without a query")

        monkeypatch.setattr(analysis, "rate_relevance", explode)
        _, _, selection = analysis._select_sample(self._posts(3), [], "", "")
        assert selection["method"] == "deterministic-fallback"

    def test_ratings_are_mapped_back_to_items_and_counted(self, monkeypatch):
        # Pool order is by base score, best first: p2 (score 2), p1, p0.
        monkeypatch.setattr(
            analysis, "rate_relevance", lambda items, q, p: {1: 0, 2: 2, 3: 1}
        )
        ranked_posts, _, selection = analysis._select_sample(self._posts(3), [], "query", "")
        assert selection["method"] == "relevance+engagement"
        assert selection["triaged"] == 3
        assert selection["relevance_counts"] == {"0": 1, "1": 1, "2": 1}
        # p2 was rated 0 (unrelated), so it sinks below the others despite the
        # highest engagement.
        assert [post["id"] for post in ranked_posts][-1] == "p2"


# ── Qualifier-aware triage ────────────────────────────────────────────────────
# The real brief this whole section exists for. It carries a topic and two
# constraints about who posted, and before decomposition existed both constraints
# were silently discarded by a triage that only asked "is this on topic".
EXAMPLE_BRIEF = (
    "give me detailed posts related to running meta ads for b2b saas, not looking "
    "for threads asked by newbie or solo founder. Im specifically looking for "
    "threads posted by someone who is from a funded saas startups. (series a, b, c "
    "of that sort)"
)
EXAMPLE_DECOMPOSITION = {
    "topic": "running Meta ads for B2B SaaS",
    "requirements": [
        {"text": "The author works at a funded startup, Series A, B or C.",
         "kind": "require"},
        {"text": "The author is not a beginner or a solo founder.", "kind": "exclude"},
    ],
}


def two_requirements():
    return analysis.Brief(
        "running Meta ads for B2B SaaS",
        (
            analysis.Requirement(
                "The author works at a funded startup, Series A, B or C.", "require"
            ),
            analysis.Requirement(
                "The author is not a beginner or a solo founder.", "exclude"
            ),
        ),
    )


class TestTodaysTriageIsUnchanged:
    """A brief with no requirements must behave exactly as it did before qualifiers.

    This is the most valuable property in the whole feature: the common case is a
    plain topical brief, and it keeps today's prompt, today's batch size, today's
    call count and today's selection metadata, so it has no regression surface.
    """

    def _items(self, count=2):
        return [
            {"kind": "post", "id": "p{}".format(i), "subreddit": "s",
             "title": "title {}".format(i), "body": "body", "score": i, "created_utc": 1}
            for i in range(count)
        ]

    def _todays_prompt(self, items, query, profile, start):
        """The prompt as it read before requirements existed, written out in full.

        Pinned as a literal rather than compared against the function itself, which
        would pass however the function drifted.
        """
        listing = "\n".join(
            "{}. [{}] {}".format(
                position,
                "comment" if item.get("kind") == "comment" else "post",
                " ".join(
                    "{} {}".format(item.get("title") or "", item.get("body") or "").split()
                )[:analysis.TRIAGE_PREVIEW_CHARS],
            )
            for position, item in enumerate(items, start)
        )
        return (
            "You are rating Reddit items for relevance to a research brief. "
            "You never invent content.\n\n"
            "Brief: {query}\n"
            "Researcher: {profile}\n\n"
            "Below are {count} numbered items, each a short preview of a post or comment.\n\n"
            "{listing}\n\n"
            'Return ONLY a JSON object: {{"ratings": [{{"i": {start}, "r": 2}}, ...]}}\n\n'
            "Rules:\n"
            "- Include EVERY item number shown above, exactly once.\n"
            '- "r" is 2 (directly about the brief\'s topic), 1 (adjacent: the same '
            "audience or a neighbouring problem, partly related, or too short to tell), "
            "or 0 (a different audience or subject entirely, or spam).\n"
            "- When unsure, use 1. No markdown fences, no commentary. JSON only."
        ).format(
            query=query,
            profile=profile or "not given",
            count=len(items),
            listing=listing,
            start=start,
        )

    def test_the_prompt_is_byte_for_byte_what_it_was(self):
        items = self._items(3)
        assert analysis._triage_prompt(items, "a query", "a profile", 1) == (
            self._todays_prompt(items, "a query", "a profile", 1)
        )

    def test_an_empty_brief_object_is_the_same_as_no_brief_at_all(self):
        items = self._items(2)
        bare = analysis._triage_prompt(items, "a query", "", 1)
        with_brief = analysis._triage_prompt(
            items, "a query", "", 1, analysis.Brief("a query", ())
        )
        assert bare == with_brief

    def test_the_prompt_never_mentions_a_qualifier_field(self):
        prompt = analysis._triage_prompt(self._items(2), "a query", "", 1)
        assert '"q"' not in prompt
        assert "constraint" not in prompt

    def test_sanitize_ratings_returns_verdicts_with_an_empty_qualifier_string(self):
        ratings = analysis._sanitize_ratings(
            {"ratings": [{"i": 1, "r": 2}, {"i": 2, "r": 0}]}, 3, 1
        )
        assert ratings == {
            1: analysis.Verdict(2, ""),
            2: analysis.Verdict(0, ""),
            3: analysis.Verdict(1, ""),
        }

    def test_a_stray_q_on_a_no_requirement_run_is_ignored(self):
        ratings = analysis._sanitize_ratings({"ratings": [{"i": 1, "r": 2, "q": "yy"}]}, 1, 1)
        assert ratings == {1: analysis.Verdict(2, "")}

    def test_rank_items_still_accepts_bare_ints_in_the_relevance_mapping(self):
        def post(id, score):
            return {
                "kind": "post", "id": id, "subreddit": "s", "score": score, "comments": 0,
                "title": "A title long enough to carry the whole idea on its own",
                "body": "A body with plenty of real text in it, past the substance bar.",
                "created_utc": 1700000000,
            }

        ranked_posts, _ = analysis.rank_items(
            [post("off", 5000), post("on", 1)], [],
            relevance={("post", "off"): 0, ("post", "on"): 2}, now=1700000000.0,
        )
        assert [item["id"] for item in ranked_posts] == ["on", "off"]

    def test_the_selection_block_keeps_todays_shape_and_keys(self, monkeypatch):
        no_requirements(monkeypatch)
        monkeypatch.setattr(
            analysis, "rate_relevance", lambda items, q, p: {1: 2, 2: 1}
        )
        posts = [
            {"kind": "post", "id": "p{}".format(i), "subreddit": "s",
             "title": "A title long enough to carry the whole idea on its own",
             "body": "A body with plenty of real text in it, past the substance bar.",
             "score": i, "comments": 0, "created_utc": 1700000000 + i}
            for i in range(2)
        ]
        _, _, selection = analysis._select_sample(posts, [], "a query", "")
        assert selection["method"] == "relevance+engagement"
        assert set(selection) == {"method", "triaged", "relevance_counts"}

    def test_the_batch_size_is_unchanged_for_a_topical_brief(self):
        # The budget share went up, but the topical batch was already at its
        # TRIAGE_BATCH_SIZE ceiling, so nothing about a topical run moves.
        assert analysis._triage_batch_size("a query", "a profile") == analysis.TRIAGE_BATCH_SIZE


class TestDecomposeBrief:
    """One cheap call splits a brief into its topic and its author constraints."""

    def test_the_example_brief_splits_into_a_topic_and_two_requirements(self):
        prompts = []

        def fake_chat(prompt, max_tokens, label, thinking=True):
            prompts.append({"prompt": prompt, "label": label, "thinking": thinking})
            return EXAMPLE_DECOMPOSITION, "m"

        brief = analysis.decompose_brief(EXAMPLE_BRIEF, "", chat=fake_chat)

        # The topic is the subject matter with both author constraints taken out.
        assert brief.topic == "running Meta ads for B2B SaaS"
        for word in ("newbie", "solo founder", "funded", "series"):
            assert word not in brief.topic.lower()
        assert len(brief.requirements) == 2
        funded, newbie = brief.requirements
        assert funded.kind == "require"
        assert newbie.kind == "exclude"
        # Both phrased positively: the excluded one is a sentence an item either
        # meets or breaks, not an instruction to invert.
        for requirement in brief.requirements:
            assert requirement.text.startswith("The author")
            assert not requirement.text.lower().startswith("not ")
        assert prompts[0]["label"] == "brief-decomposition"
        assert prompts[0]["thinking"] is False

    def test_the_prompt_says_everything_the_answer_depends_on(self):
        prompt = analysis._decompose_prompt(EXAMPLE_BRIEF, "a profile")
        assert EXAMPLE_BRIEF in prompt
        assert "a profile" in prompt
        # An empty list is the normal answer, and saying so is what stops the model
        # inventing a constraint for every plain topical brief.
        flat = " ".join(prompt.split())
        assert "empty list" in flat
        assert "Never invent a constraint the brief does not state" in flat
        assert "never turn the topic into a requirement" in flat
        assert "POSITIVE" in prompt
        assert '"The author"' in prompt and '"The thread"' in prompt
        assert "JSON only" in prompt
        assert str(analysis.MAX_REQUIREMENTS) in prompt
        assert str(analysis.MAX_REQUIREMENT_CHARS) in prompt
        assert str(analysis.MAX_BRIEF_TOPIC_CHARS) in prompt

    def test_the_prompt_forbids_dashes_it_must_not_contain_either(self):
        prompt = analysis._decompose_prompt("a brief", "")
        assert "em dash" in prompt and "en dash" in prompt
        assert "—" not in prompt and "–" not in prompt

    def test_a_chat_that_raises_fails_open_to_the_query_alone(self):
        def boom(*args, **kwargs):
            raise ValueError("no")

        assert analysis.decompose_brief("a brief", "", chat=boom) == analysis.Brief(
            "a brief", ()
        )

    def test_a_chat_that_raises_something_unexpected_still_fails_open(self):
        def boom(*args, **kwargs):
            raise RuntimeError("not even a ValueError")

        assert analysis.decompose_brief("a brief", "", chat=boom) == analysis.Brief(
            "a brief", ()
        )

    def test_an_empty_answer_fails_open(self):
        assert analysis.decompose_brief(
            "a brief", "", chat=lambda *a, **k: ({}, "m")
        ) == analysis.Brief("a brief", ())

    def test_a_non_list_requirements_field_fails_open(self):
        brief = analysis.decompose_brief(
            "a brief", "",
            chat=lambda *a, **k: ({"topic": "t", "requirements": "no beginners"}, "m"),
        )
        assert brief.requirements == ()
        assert brief.topic == "t"

    def test_nine_requirements_are_capped_not_refused(self):
        brief = analysis.decompose_brief(
            "a brief", "",
            chat=lambda *a, **k: (
                {"topic": "t",
                 "requirements": [{"text": "The author is #{}.".format(i)} for i in range(9)]},
                "m",
            ),
        )
        assert len(brief.requirements) == analysis.MAX_REQUIREMENTS
        assert all(requirement.kind == "require" for requirement in brief.requirements)

    def test_requirement_text_is_stripped_of_markdown_dashes_and_links(self):
        raw = {
            "topic": "meta ads",
            "requirements": [
                {"text": "The author works at a funded startup — see "
                         "[our guide](https://example.com/x) or acme.io for detail.",
                 "kind": "require"}
            ],
        }
        brief = analysis.decompose_brief("a brief", "", chat=lambda *a, **k: (raw, "m"))
        text = brief.requirements[0].text
        assert "—" not in text and "–" not in text
        assert "https://" not in text and "acme.io" not in text
        assert "[" not in text and "](" not in text
        assert "our guide" in text

    def test_long_strings_are_cut_to_their_caps(self):
        raw = {
            "topic": "t" * 400,
            "requirements": [{"text": "The author " + "x" * 400, "kind": "require"}],
        }
        brief = analysis.decompose_brief("a brief", "", chat=lambda *a, **k: (raw, "m"))
        assert len(brief.topic) <= analysis.MAX_BRIEF_TOPIC_CHARS
        assert len(brief.requirements[0].text) <= analysis.MAX_REQUIREMENT_CHARS

    def test_an_unknown_kind_becomes_require_and_case_does_not_matter(self):
        raw = {
            "topic": "t",
            "requirements": [
                {"text": "The author is senior.", "kind": "banana"},
                {"text": "The thread is a case study.", "kind": "EXCLUDE"},
            ],
        }
        brief = analysis.decompose_brief("a brief", "", chat=lambda *a, **k: (raw, "m"))
        assert [requirement.kind for requirement in brief.requirements] == [
            "require", "exclude"
        ]

    def test_a_bare_string_requirement_is_accepted(self):
        raw = {"topic": "t", "requirements": ["The author is not a beginner."]}
        brief = analysis.decompose_brief("a brief", "", chat=lambda *a, **k: (raw, "m"))
        assert brief.requirements == (
            analysis.Requirement("The author is not a beginner.", "require"),
        )

    def test_an_empty_query_makes_no_call_at_all(self):
        def explode(*args, **kwargs):
            raise AssertionError("decomposition must not be called without a query")

        assert analysis.decompose_brief("", "", chat=explode) == analysis.Brief("", ())
        assert analysis.decompose_brief("   ", "", chat=explode).requirements == ()

    def test_a_topical_brief_comes_back_with_no_requirements(self):
        brief = analysis.decompose_brief(
            "what freelancers complain about", "",
            chat=lambda *a, **k: ({"topic": "freelancer complaints", "requirements": []}, "m"),
        )
        assert brief.requirements == ()

    def test_sanitize_brief_never_raises_on_anything(self):
        for raw in (None, [], "nope", 4, {"requirements": [None, 3, {}, {"text": ""}]}):
            assert analysis.sanitize_brief(raw, "a brief") == analysis.Brief("a brief", ())

    def test_a_bare_query_reads_as_a_requirement_free_brief(self):
        assert analysis._as_brief("a query") == analysis.Brief("a query", ())
        assert analysis._as_brief(None) == analysis.Brief("", ())
        assert analysis._as_brief(two_requirements()) == two_requirements()


class TestQualifierContract:
    """The "q" field: one character per requirement, and "u" for everything unsure."""

    def _items(self, count=2):
        return [
            {"kind": "post", "id": "p{}".format(i), "subreddit": "s",
             "title": "title {}".format(i), "body": "body", "score": i, "created_utc": 1}
            for i in range(count)
        ]

    def test_the_prompt_asks_for_one_character_per_requirement(self):
        prompt = analysis._triage_prompt(
            self._items(2), EXAMPLE_BRIEF, "", 1, two_requirements()
        )
        assert '"q": "uu"' in prompt
        assert "exactly 2 character(s)" in prompt
        assert "The author works at a funded startup, Series A, B or C." in prompt
        assert "The author is not a beginner or a solo founder." in prompt
        # The topic, not the raw brief: the constraints are listed separately, so
        # repeating them inside the topic would double-count them.
        assert "Topic: running Meta ads for B2B SaaS" in prompt

    def test_the_prompt_pushes_hard_toward_u(self):
        # The wording of the "u" bullets was rewritten after a live run marked 107
        # of 110 items as CONTRADICTING "The author is from a funded startup, Series
        # A, B or C" purely because they never mentioned the author's company. The
        # earlier prompt preferred "u" without separating "the text says something
        # incompatible" from "the text is silent", so the assertions below pin the
        # distinction rather than the old sentences: silence is never "n", and the
        # verdict is about the author rather than the subject of the thread.
        prompt = analysis._triage_prompt(
            self._items(1), EXAMPLE_BRIEF, "", 1, two_requirements()
        )
        assert '"u" is the correct answer, not a failure.' in prompt
        assert "most of the characters you return should be \"u\"" in prompt
        assert 'never mentions what a constraint is about is "u", never "n"' in prompt
        assert 'Silence is always "u".' in prompt
        assert "INCOMPATIBLE with the constraint" in prompt
        assert "only on what the item says about its AUTHOR" in prompt
        assert "or a username" in prompt
        assert "—" not in prompt and "–" not in prompt

    def test_the_prompt_works_the_positive_requirement_that_broke_in_the_live_run(self):
        # A worked example, because the model got this wrong in exactly one
        # direction: for a requirement phrased "The author IS x" it read absence of
        # evidence as failure. Naming the silent case and the incompatible case side
        # by side is what the prompt was missing.
        prompt = analysis._triage_prompt(
            self._items(1), EXAMPLE_BRIEF, "", 1, two_requirements()
        )
        assert 'The author is from a funded startup, Series A, B or C' in prompt
        assert "never mentions the author's company is \"u\"" in prompt
        assert 'solo bootstrapper with no investors" is "n"' in prompt
        assert 'we raised our Series B last year" is "y"' in prompt

    def test_a_valid_q_round_trips(self):
        ratings = analysis._sanitize_ratings(
            {"ratings": [{"i": 1, "r": 2, "q": "yu"}, {"i": 2, "r": 0, "q": "nn"}]}, 2, 1, 2
        )
        assert ratings == {1: analysis.Verdict(2, "yu"), 2: analysis.Verdict(0, "nn")}

    def test_uppercase_is_accepted(self):
        ratings = analysis._sanitize_ratings({"ratings": [{"i": 1, "r": 2, "q": "YN"}]}, 1, 1, 2)
        assert ratings[1] == analysis.Verdict(2, "yn")

    @pytest.mark.parametrize("bad", [None, "", "y", "yyyy", "xz", "?!", [], ["y", "u"], 12, True, {}])
    def test_every_malformed_q_fails_open_to_all_u_without_disturbing_r(self, bad):
        ratings = analysis._sanitize_ratings(
            {"ratings": [{"i": 1, "r": 2, "q": bad}]}, 1, 1, 2
        )
        verdict = ratings[1]
        # The valid topic rating survives untouched, and no character became "n".
        assert verdict.topic == 2
        assert len(verdict.qualifiers) == 2
        assert set(verdict.qualifiers) <= {"u", "y"}
        assert "n" not in verdict.qualifiers

    def test_a_missing_q_key_fails_open_to_all_u(self):
        ratings = analysis._sanitize_ratings({"ratings": [{"i": 1, "r": 1}]}, 1, 1, 3)
        assert ratings[1] == analysis.Verdict(1, "uuu")

    def test_a_short_q_is_padded_and_a_long_one_is_truncated(self):
        assert analysis._sanitize_qualifiers("y", 3) == "yuu"
        assert analysis._sanitize_qualifiers("ynynyn", 2) == "yn"
        assert analysis._sanitize_qualifiers("yu", 0) == ""

    def test_a_malformed_r_does_not_disturb_a_valid_q(self):
        ratings = analysis._sanitize_ratings(
            {"ratings": [{"i": 1, "r": "two", "q": "yn"}]}, 1, 1, 2
        )
        assert ratings[1] == analysis.Verdict(1, "yn")

    def test_items_the_model_never_mentioned_default_to_all_u(self):
        ratings = analysis._sanitize_ratings({"ratings": []}, 2, 1, 2)
        assert ratings == {1: analysis.Verdict(1, "uu"), 2: analysis.Verdict(1, "uu")}

    def test_the_tiers_read_off_the_qualifier_string(self):
        assert analysis.qualifier_tier(analysis.Verdict(2, "yu")) == "confirmed"
        assert analysis.qualifier_tier(analysis.Verdict(2, "un")) == "violated"
        assert analysis.qualifier_tier(analysis.Verdict(2, "uu")) == "unconfirmed"
        assert analysis.qualifier_tier(analysis.Verdict(2, "yn")) == "violated"
        assert analysis.qualifier_tier(analysis.Verdict(2, "")) == "unconfirmed"

    def test_a_bare_int_verdict_is_unconfirmed_rather_than_violated(self):
        # The shim's whole job: an old-shaped mapping value must never read as a
        # constraint violation, because nobody checked a constraint at all.
        assert analysis.qualifier_tier(2) == "unconfirmed"
        assert analysis.qualifier_tier(0) == "unconfirmed"
        assert analysis._as_verdict(2) == analysis.Verdict(2, "")
        assert analysis._as_verdict("nonsense") == analysis.Verdict(1, "")
        assert analysis._as_verdict(True) == analysis.Verdict(1, "")

    def test_the_qualifier_round_trip_survives_a_real_triage_call(self):
        def fake_chat(prompt, max_tokens, label, thinking=True):
            return {"ratings": [{"i": 1, "r": 2, "q": "yu"}, {"i": 2, "r": 1, "q": "nu"}]}, "m"

        ratings = analysis.rate_relevance(
            self._items(2), EXAMPLE_BRIEF, "", chat=fake_chat, brief=two_requirements()
        )
        assert ratings == {1: analysis.Verdict(2, "yu"), 2: analysis.Verdict(1, "nu")}


class TestQualifierRanking:
    """A third tier, and deliberately not a hard filter or a multiplier."""

    NOW = 1700000000.0

    def _post(self, id, subreddit="s", score=10, age_days=0.0):
        return {
            "kind": "post", "id": id, "subreddit": subreddit, "score": score, "comments": 0,
            "title": "A title long enough to carry the whole idea on its own",
            "body": "A body with plenty of real text in it, past the substance bar.",
            "created_utc": int(self.NOW - age_days * 86400),
        }

    def test_a_confirmed_item_at_base_point_one_outranks_an_unconfirmed_one_at_point_nine(self):
        # This is the test a multiplier fails. Both are rated 2 on topic, so the
        # only difference is the qualifier verdict.
        quiet_confirmed = self._post("confirmed", score=0, age_days=10.57)
        loud_silent = self._post("silent", score=1000, age_days=1.216)
        base = analysis._base_scores([quiet_confirmed, loud_silent], self.NOW)
        assert base[0] == pytest.approx(0.1, abs=0.01)
        assert base[1] == pytest.approx(0.9, abs=0.01)
        # Under the rejected multiplier design (confirmed 1.0, unknown 0.6) the loud
        # silent item wins on engagement alone, which is exactly the constraint the
        # brief cared about being drowned out.
        assert base[0] * 1.0 < base[1] * 0.6

        ranked_posts, _ = analysis.rank_items(
            [quiet_confirmed, loud_silent], [],
            relevance={
                ("post", "confirmed"): analysis.Verdict(2, "y"),
                ("post", "silent"): analysis.Verdict(2, "u"),
            },
            now=self.NOW,
        )
        assert [item["id"] for item in ranked_posts] == ["confirmed", "silent"]

    def test_a_violated_item_ranks_below_every_unconfirmed_and_untriaged_item(self):
        violated = self._post("violated", score=5000)
        unconfirmed = self._post("unconfirmed", score=1)
        untriaged = self._post("untriaged", score=1)
        ranked_posts, _ = analysis.rank_items(
            [violated, unconfirmed, untriaged], [],
            relevance={
                ("post", "violated"): analysis.Verdict(2, "n"),
                ("post", "unconfirmed"): analysis.Verdict(1, "u"),
            },
            now=self.NOW,
        )
        assert [item["id"] for item in ranked_posts][-1] == "violated"

    def test_nothing_is_deleted_by_a_violation(self):
        violated = self._post("violated", score=10)
        ranked_posts, _ = analysis.rank_items(
            [violated], [],
            relevance={("post", "violated"): analysis.Verdict(2, "nn")}, now=self.NOW,
        )
        assert [item["id"] for item in ranked_posts] == ["violated"]

    def test_a_topic_rated_zero_item_stays_in_backfill_however_well_it_qualifies(self):
        offtopic = self._post("offtopic", score=5000)
        ontopic = self._post("ontopic", score=1)
        ranked_posts, _ = analysis.rank_items(
            [offtopic, ontopic], [],
            relevance={
                ("post", "offtopic"): analysis.Verdict(0, "yy"),
                ("post", "ontopic"): analysis.Verdict(1, "uu"),
            },
            now=self.NOW,
        )
        assert [item["id"] for item in ranked_posts] == ["ontopic", "offtopic"]

    def test_absence_of_evidence_costs_an_item_nothing(self):
        # Polarity normalisation means only an explicit "n" demotes, so a silent
        # item ranks purely on its topic rating and its engagement.
        strong = self._post("strong", score=100)
        weak = self._post("weak", score=1)
        ranked_posts, _ = analysis.rank_items(
            [weak, strong], [],
            relevance={
                ("post", "strong"): analysis.Verdict(2, "uu"),
                ("post", "weak"): analysis.Verdict(2, "uu"),
            },
            now=self.NOW,
        )
        assert [item["id"] for item in ranked_posts] == ["strong", "weak"]

    def test_fairness_holds_across_all_three_tier_seams(self):
        # One loud community fills the confirmed tier on its own. The cap is shared
        # across every seam, so the quiet community catches up in the tiers below
        # rather than starting each one with a count of zero.
        loud_confirmed = [
            self._post("loudy{}".format(i), subreddit="loud", score=1000 - i)
            for i in range(6)
        ]
        loud_unconfirmed = [
            self._post("loudu{}".format(i), subreddit="loud", score=900 - i)
            for i in range(4)
        ]
        quiet_unconfirmed = [
            self._post("quietu{}".format(i), subreddit="quiet", score=1) for i in range(4)
        ]
        loud_violated = [
            self._post("loudn{}".format(i), subreddit="loud", score=800) for i in range(3)
        ]
        quiet_violated = [
            self._post("quietn{}".format(i), subreddit="quiet", score=1) for i in range(3)
        ]
        everything = (
            loud_confirmed + loud_unconfirmed + quiet_unconfirmed
            + loud_violated + quiet_violated
        )
        relevance = {}
        for item in loud_confirmed:
            relevance[("post", item["id"])] = analysis.Verdict(2, "y")
        for item in loud_unconfirmed + quiet_unconfirmed:
            relevance[("post", item["id"])] = analysis.Verdict(2, "u")
        for item in loud_violated + quiet_violated:
            relevance[("post", item["id"])] = analysis.Verdict(2, "n")

        ranked_posts, _ = analysis.rank_items(everything, [], relevance, now=self.NOW)
        ids = [item["id"] for item in ranked_posts]
        assert len(ids) == len(everything)
        # Tier order is absolute, so loud takes the whole confirmed tier: it is the
        # only community in it, and the cap yields rather than reordering tiers.
        assert ids[:6] == ["loudy{}".format(i) for i in range(6)]
        # Then the running counts are SHARED across the seam, so quiet catches up
        # inside the unconfirmed tier instead of starting it from zero. Loud is six
        # picks ahead, so every quiet item goes first despite scoring 1 to their 900.
        assert all(name.startswith("quietu") for name in ids[6:10])
        assert all(name.startswith("loudu") for name in ids[10:14])
        # And again at the second seam, into the violated tier.
        assert all(name.startswith("quietn") for name in ids[14:17])
        assert all(name.startswith("loudn") for name in ids[17:])

    def test_a_run_with_no_requirements_ranks_exactly_as_two_tiers_did(self):
        posts = [self._post("p{}".format(i), score=100 - i * 10) for i in range(6)]
        relevance = {("post", "p{}".format(i)): 2 for i in range(6)}
        as_ints, _ = analysis.rank_items(posts, [], relevance, now=self.NOW)
        as_verdicts, _ = analysis.rank_items(
            posts, [],
            {key: analysis.Verdict(2, "") for key in relevance}, now=self.NOW,
        )
        assert [item["id"] for item in as_ints] == [item["id"] for item in as_verdicts]


class TestDegenerateQualifierGuard:
    """A requirement the model marks as contradicted by nearly everything.

    Measured on a live run of "detailed posts about running meta ads for b2b saas,
    not from newbies or solo founders, ideally from funded startups series a b c":
    over 110 items, "The author is from a funded startup, series A, B, or C" came
    back 0 met / 107 contradicted / 3 did not say, while the two negatively phrased
    requirements in the same batch behaved correctly. Any "n" demoted, so 110 of 110
    items landed in the backfill tier: the ranking silently did nothing, and the
    report told the reader that 107 real threads contradicted what they asked for.
    """

    def _posts(self, count):
        # Identical engagement and age, so the triage pool keeps source order and
        # position i + 1 in the ratings mapping is posts[i].
        return [
            {"kind": "post", "id": "p{}".format(i), "subreddit": "s",
             "title": "A title long enough to carry the whole idea on its own",
             "body": "A body with plenty of real text in it, past the substance bar.",
             "score": 10, "comments": 0, "created_utc": 1700000000}
            for i in range(count)
        ]

    def _select(self, monkeypatch, verdicts, count, brief=None):
        brief = brief if brief is not None else two_requirements()
        monkeypatch.setattr(
            analysis, "decompose_brief", lambda query, profile="", **kwargs: brief
        )
        monkeypatch.setattr(
            analysis, "rate_relevance",
            lambda items, query, profile, **kwargs: dict(verdicts),
        )
        return analysis._select_sample(self._posts(count), [], EXAMPLE_BRIEF, "")

    def test_the_threshold_is_more_than_half_the_items_rated(self):
        assert analysis.DEGENERATE_VIOLATION_SHARE == 0.5
        assert analysis.requirement_unreliable(
            {"met": 0, "violated": 6, "unknown": 5}
        ) is True
        # Exactly half is not more than half. A pool can genuinely split down the
        # middle, and the guard only claims the lopsided case.
        assert analysis.requirement_unreliable(
            {"met": 0, "violated": 5, "unknown": 5}
        ) is False
        assert analysis.requirement_unreliable(
            {"met": 4, "violated": 6, "unknown": 0}
        ) is True
        assert analysis.requirement_unreliable(
            {"met": 0, "violated": 0, "unknown": 20}
        ) is False
        assert analysis.requirement_unreliable({}) is False
        assert analysis.requirement_unreliable(None) is False

    def test_a_handful_of_items_never_trips_the_guard(self):
        # 3 of 4 is noise, not evidence that a requirement was misread, and a pool
        # that small has no meaningful tiering to protect either.
        assert analysis.DEGENERATE_MIN_RATED == 8
        assert analysis.requirement_unreliable(
            {"met": 0, "violated": 3, "unknown": 1}
        ) is False
        assert analysis.requirement_unreliable(
            {"met": 0, "violated": 7, "unknown": 0}
        ) is False
        assert analysis.requirement_unreliable(
            {"met": 0, "violated": 8, "unknown": 0}
        ) is True

    def test_a_flagged_requirement_is_skipped_in_both_directions(self):
        # It cannot demote and it cannot promote: a verdict nobody trusts must not
        # decide the ranking either way.
        assert analysis.qualifier_tier(analysis.Verdict(2, "nu"), [True, False]) == (
            "unconfirmed"
        )
        assert analysis.qualifier_tier(analysis.Verdict(2, "yu"), [True, False]) == (
            "unconfirmed"
        )
        assert analysis.qualifier_tier(analysis.Verdict(2, "yn"), [True, False]) == (
            "violated"
        )
        assert analysis.qualifier_tier(analysis.Verdict(2, "nn"), [True, True]) == (
            "unconfirmed"
        )
        # No flags is the default and today's behaviour, and a flag list shorter
        # than the qualifier string leaves the characters it does not cover trusted.
        assert analysis.qualifier_tier(analysis.Verdict(2, "nu")) == "violated"
        assert analysis.qualifier_tier(analysis.Verdict(2, "un"), [True]) == "violated"

    def test_a_requirement_almost_everything_contradicts_is_reported_but_not_applied(
        self, monkeypatch
    ):
        verdicts = {position: analysis.Verdict(2, "nu") for position in range(1, 10)}
        verdicts[10] = analysis.Verdict(2, "uu")
        ranked_posts, _, selection = self._select(monkeypatch, verdicts, 10)
        funded, beginner = selection["requirements"]
        assert funded["unreliable"] is True
        assert beginner["unreliable"] is False
        # The counts are still reported in full. The reader is told what the model
        # said and told that it was not used, rather than being told nothing.
        assert (funded["met"], funded["violated"], funded["unknown"]) == (0, 9, 1)
        assert funded["text"] == "The author works at a funded startup, Series A, B or C."
        # Nothing reliable said anything, so nothing is demoted or promoted.
        assert selection["qualifier_counts"] == {
            "confirmed": 0, "unconfirmed": 10, "violated": 0
        }
        assert all(item["qualifier_tier"] == "unconfirmed" for item in ranked_posts)

    def test_a_normal_spread_of_verdicts_is_left_exactly_as_it_is_today(
        self, monkeypatch
    ):
        verdicts = {
            1: analysis.Verdict(2, "yu"), 2: analysis.Verdict(2, "nu"),
            3: analysis.Verdict(1, "uu"), 4: analysis.Verdict(2, "uy"),
            5: analysis.Verdict(2, "uu"), 6: analysis.Verdict(1, "un"),
            7: analysis.Verdict(2, "uu"), 8: analysis.Verdict(2, "yu"),
            9: analysis.Verdict(2, "uu"), 10: analysis.Verdict(1, "uu"),
        }
        ranked_posts, _, selection = self._select(monkeypatch, verdicts, 10)
        assert [
            requirement["unreliable"] for requirement in selection["requirements"]
        ] == [False, False]
        # Every tier is the one the pre-guard code would have computed.
        stamps = {item["id"]: item["qualifier_tier"] for item in ranked_posts}
        for position, verdict in verdicts.items():
            assert stamps["p{}".format(position - 1)] == analysis.qualifier_tier(verdict)
        assert selection["qualifier_counts"] == {
            "confirmed": 3, "unconfirmed": 5, "violated": 2
        }

    def test_tiering_follows_only_the_reliable_requirement(self, monkeypatch):
        verdicts = {position: analysis.Verdict(2, "nu") for position in range(1, 9)}
        verdicts[9] = analysis.Verdict(2, "un")   # breaks the reliable requirement
        verdicts[10] = analysis.Verdict(2, "uy")  # meets the reliable requirement
        ranked_posts, _, selection = self._select(monkeypatch, verdicts, 10)
        assert [
            requirement["unreliable"] for requirement in selection["requirements"]
        ] == [True, False]
        stamps = {item["id"]: item["qualifier_tier"] for item in ranked_posts}
        assert stamps["p8"] == "violated"
        assert stamps["p9"] == "confirmed"
        # The eight items that break only the flagged requirement keep the tier they
        # would have had if it had never been asked. This is the assertion the bug
        # fails: every one of them was in backfill before.
        assert all(
            stamps["p{}".format(index)] == "unconfirmed" for index in range(8)
        )
        assert ranked_posts[0]["id"] == "p9"
        assert ranked_posts[-1]["id"] == "p8"

    def test_rank_items_does_not_demote_an_item_on_a_flagged_requirement(self):
        def post(id, score):
            return {
                "kind": "post", "id": id, "subreddit": "s", "score": score, "comments": 0,
                "title": "A title long enough to carry the whole idea on its own",
                "body": "A body with plenty of real text in it, past the substance bar.",
                "created_utc": 1700000000,
            }

        posts = [post("breaks-flagged", 1000), post("silent", 1)]
        relevance = {
            ("post", "breaks-flagged"): analysis.Verdict(2, "nu"),
            ("post", "silent"): analysis.Verdict(2, "uu"),
        }
        demoted, _ = analysis.rank_items(posts, [], relevance, now=1700000000.0)
        assert [item["id"] for item in demoted] == ["silent", "breaks-flagged"]
        kept, _ = analysis.rank_items(
            posts, [], relevance, now=1700000000.0, unreliable=[True, False]
        )
        assert [item["id"] for item in kept] == ["breaks-flagged", "silent"]

    def test_the_live_run_that_put_every_item_in_backfill_cannot_recur(
        self, monkeypatch
    ):
        brief = analysis.Brief(
            'running "meta ads" for b2b saas',
            (
                analysis.Requirement("The author is not a newbie.", "exclude"),
                analysis.Requirement("The author is not a solo founder.", "exclude"),
                analysis.Requirement(
                    "The author is from a funded startup, series A, B, or C.", "require"
                ),
            ),
        )
        # The per-requirement verdict counts the real run produced:
        #   not a newbie:            2 met,   3 contradicted, 105 did not say
        #   not a solo founder:      3 met,   0 contradicted, 107 did not say
        #   from a funded startup:   0 met, 107 contradicted,   3 did not say
        verdicts = {}
        for position in range(1, 111):
            newbie = "y" if position <= 2 else ("n" if position <= 5 else "u")
            solo = "y" if position <= 3 else "u"
            funded = "n" if position <= 107 else "u"
            verdicts[position] = analysis.Verdict(2, newbie + solo + funded)
        # What the old tiering did with exactly these verdicts, pinned so the
        # regression this guards against is written down rather than described.
        assert sum(
            1 for verdict in verdicts.values()
            if analysis.qualifier_tier(verdict) == "violated"
        ) == 107

        ranked_posts, _, selection = self._select(monkeypatch, verdicts, 110, brief)
        assert [
            requirement["unreliable"] for requirement in selection["requirements"]
        ] == [False, False, True]
        assert selection["requirements"][2]["violated"] == 107
        # Three tiers again, and the 107 are no longer any of them's problem.
        assert selection["qualifier_counts"] == {
            "confirmed": 2, "unconfirmed": 105, "violated": 3
        }
        assert sum(selection["qualifier_counts"].values()) == 110
        tiers = [item["qualifier_tier"] for item in ranked_posts]
        assert tiers.count("violated") == 3
        assert tiers[0] == "confirmed"
        assert tiers[-1] == "violated"

    def test_the_no_requirement_path_is_untouched(self, monkeypatch):
        _, _, selection = self._select(
            monkeypatch, {1: analysis.Verdict(2, ""), 2: analysis.Verdict(2, "")}, 2,
            brief=analysis.Brief("running Meta ads for B2B SaaS", ()),
        )
        assert selection["method"] == "relevance+engagement"
        assert set(selection) == {"method", "triaged", "relevance_counts"}


class TestAdaptiveTriagePreview:
    """600 characters only when the brief has constraints; 300 stays the default."""

    def _item(self, **extra):
        item = {
            "kind": "post", "id": "p1", "subreddit": "s", "score": 1, "created_utc": 1,
            "title": "T", "body": "b" * 2000, "author": "growthguy99",
        }
        item.update(extra)
        return item

    def test_the_preview_doubles_only_for_a_qualifier_brief(self):
        assert analysis.TRIAGE_PREVIEW_CHARS == 300
        assert analysis.TRIAGE_QUALIFIER_PREVIEW_CHARS == 600
        topical = analysis._triage_prompt([self._item()], "a query", "", 1)
        qualifier = analysis._triage_prompt(
            [self._item()], EXAMPLE_BRIEF, "", 1, two_requirements()
        )
        assert "1. [post] T " + "b" * (analysis.TRIAGE_PREVIEW_CHARS - 3) in topical
        assert "b" * (analysis.TRIAGE_PREVIEW_CHARS + 1) not in topical
        assert "b" * (analysis.TRIAGE_QUALIFIER_PREVIEW_CHARS - 3) in qualifier

    def test_flair_is_prepended_when_there_is_any(self):
        item = self._item(
            author_flair_text="Series B | Head of Growth", link_flair_text="Case Study"
        )
        prompt = analysis._triage_prompt([item], EXAMPLE_BRIEF, "", 1, two_requirements())
        assert "(flair: Series B | Head of Growth / Case" in prompt

    def test_the_line_is_clean_when_there_is_no_flair(self):
        prompt = analysis._triage_prompt(
            [self._item()], EXAMPLE_BRIEF, "", 1, two_requirements()
        )
        assert "flair:" not in prompt
        assert "1. [post] T bbb" in prompt

    def test_flair_is_capped_and_stripped(self):
        item = self._item(author_flair_text="**Founder** at acme.io " + "x" * 200)
        flair = analysis._triage_flair(item)
        assert "*" not in flair and "acme.io" not in flair
        assert len(flair) <= analysis.TRIAGE_FLAIR_CHARS + len("(flair: ) ")

    def test_flair_never_appears_on_a_topical_run(self):
        item = self._item(author_flair_text="Series B | Head of Growth")
        assert "flair" not in analysis._triage_prompt([item], "a query", "", 1)

    def test_the_username_never_reaches_the_prompt(self):
        item = self._item(author="growthguy99", author_flair_text="Series B")
        for prompt in (
            analysis._triage_prompt([item], "a query", "", 1),
            analysis._triage_prompt([item], EXAMPLE_BRIEF, "", 1, two_requirements()),
        ):
            assert "growthguy99" not in prompt


class TestTriageBudgetArithmetic:
    """A longer preview must not silently push a batch past what the request takes,
    nor silently double the number of sequential calls a pool costs."""

    def _items(self, count):
        return [
            {"kind": "post", "id": "p{}".format(i), "subreddit": "s",
             "title": "title {}".format(i), "body": "b" * 1200,
             "author_flair_text": "Series B founder",
             "score": i, "created_utc": 1}
            for i in range(count)
        ]

    def test_the_share_was_raised_to_keep_the_call_count(self):
        assert analysis.TRIAGE_BUDGET_SHARE == 0.15

    def test_a_two_requirement_batch_fits_the_triage_share(self):
        brief = two_requirements()
        size = analysis._triage_batch_size(EXAMPLE_BRIEF, "a profile", brief)
        assert 0 < size <= analysis.TRIAGE_BATCH_SIZE
        estimate = analysis.estimate_tokens(
            analysis._triage_prompt(self._items(size), EXAMPLE_BRIEF, "a profile", 1, brief)
        ) + analysis._triage_output_tokens(
            size, analysis.TRIAGE_QUALIFIER_OUTPUT_TOKENS_PER_ITEM
        )
        assert estimate <= int(analysis.token_budget() * analysis.TRIAGE_BUDGET_SHARE)

    def test_the_batch_size_never_grows_past_the_listing_ceiling(self):
        assert analysis.TRIAGE_BATCH_SIZE == 120
        assert analysis._triage_batch_size(
            EXAMPLE_BRIEF, "a profile", two_requirements()
        ) <= analysis.TRIAGE_BATCH_SIZE

    def test_the_output_reservation_for_a_full_batch_stays_under_the_ceiling(self):
        reserved = analysis._triage_output_tokens(
            analysis.TRIAGE_BATCH_SIZE, analysis.TRIAGE_QUALIFIER_OUTPUT_TOKENS_PER_ITEM
        )
        assert reserved < analysis.TRIAGE_MAX_TOKENS
        # 200 items is what would truncate, which is why the batch cap must hold.
        assert analysis._triage_output_tokens(
            200, analysis.TRIAGE_QUALIFIER_OUTPUT_TOKENS_PER_ITEM
        ) == analysis.TRIAGE_MAX_TOKENS

    def test_an_eight_hundred_item_pool_still_costs_about_seven_calls(self):
        size = analysis._triage_batch_size(
            EXAMPLE_BRIEF, "a profile", two_requirements()
        )
        calls = -(-800 // size)
        assert calls == 7, "800 items at {} per batch is {} calls".format(size, calls)

    def test_a_topical_pool_costs_the_same_seven_calls_it_always_did(self):
        size = analysis._triage_batch_size(EXAMPLE_BRIEF, "a profile")
        assert -(-800 // size) == 7


class TestTriageDeadline:
    """Triage is several uninterruptible calls inside one soft deadline."""

    def _items(self, count):
        return [
            {"kind": "post", "id": "p{}".format(i), "subreddit": "s",
             "title": "title {}".format(i), "body": "body", "score": i, "created_utc": 1}
            for i in range(count)
        ]

    def test_a_deadline_already_past_issues_no_batch_at_all(self):
        def explode(*args, **kwargs):
            raise AssertionError("no batch may be issued past the deadline")

        ratings = analysis.rate_relevance(
            self._items(5), "a query", "", chat=explode,
            soft_deadline=time.monotonic() - 1,
        )
        assert ratings == {}

    def test_the_caller_degrades_to_the_untriaged_tier(self, monkeypatch):
        no_requirements(monkeypatch)
        posts = [
            {"kind": "post", "id": "p{}".format(i), "subreddit": "s",
             "title": "A title long enough to carry the whole idea on its own",
             "body": "A body with plenty of real text in it, past the substance bar.",
             "score": 100 - i, "comments": 0, "created_utc": 1700000000}
            for i in range(4)
        ]
        ranked_posts, _, selection = analysis._select_sample(
            posts, [], "a query", "", time.monotonic() - 1
        )
        # Nothing was rated, so nothing was promoted, and the engagement order
        # stands. The run is degraded, not failed.
        assert selection["triaged"] == 0
        assert [item["id"] for item in ranked_posts] == ["p0", "p1", "p2", "p3"]

    def test_a_deadline_stops_the_pool_rather_than_the_run(self):
        state = {"calls": 0}

        def fake_chat(prompt, max_tokens, label, thinking=True):
            state["calls"] += 1
            return {"ratings": [{"i": 1, "r": 2}]}, "m"

        size = analysis._triage_batch_size("a query", "")
        items = self._items(size * 2)
        # A generous deadline reads the whole pool in two batches.
        assert analysis.rate_relevance(
            items, "a query", "", chat=fake_chat, soft_deadline=time.monotonic() + 3600
        )
        assert state["calls"] == 2
        # A spent one reads none of it, and still returns a mapping rather than the
        # None that means "triage failed, select deterministically".
        state["calls"] = 0
        ratings = analysis.rate_relevance(
            items, "a query", "", chat=fake_chat, soft_deadline=time.monotonic() - 1
        )
        assert state["calls"] == 0
        assert ratings == {}

    def test_no_deadline_behaves_exactly_as_before(self):
        prompts = []

        def fake_chat(prompt, max_tokens, label, thinking=True):
            prompts.append(prompt)
            return {"ratings": []}, "m"

        analysis.rate_relevance(self._items(3), "a query", "", chat=fake_chat)
        assert len(prompts) == 1

    def test_classify_sample_forwards_the_deadline_to_the_triage(self, monkeypatch):
        no_requirements(monkeypatch)
        seen = {}

        def fake_rate(items, query, profile, chat=None, brief=None, soft_deadline=None):
            seen["soft_deadline"] = soft_deadline
            return {}

        monkeypatch.setattr(analysis, "rate_relevance", fake_rate)
        monkeypatch.setattr(analysis, "fit_sample", lambda p, c, s, budget=None: (list(p), 200))
        monkeypatch.setattr(
            analysis, "classify_items",
            lambda items, subreddits, preview_chars=200: {
                "topics": [], "item_sentiments": [], "model": "m"
            },
        )
        posts = [
            {"kind": "post", "id": "p1", "subreddit": "s", "score": 1, "comments": 0,
             "title": "A title long enough to carry the whole idea on its own",
             "body": "A body with plenty of real text in it, past the substance bar.",
             "created_utc": 1700000000}
        ]
        analysis.classify_sample(posts, [], ["s"], "a query", "", soft_deadline=1234.5)
        assert seen["soft_deadline"] == 1234.5

    def test_a_triage_fake_that_predates_the_deadline_still_works(self, monkeypatch):
        no_requirements(monkeypatch)
        monkeypatch.setattr(analysis, "rate_relevance", lambda items, q, p: {1: 2})
        posts = [
            {"kind": "post", "id": "p1", "subreddit": "s", "score": 1, "comments": 0,
             "title": "A title long enough to carry the whole idea on its own",
             "body": "A body with plenty of real text in it, past the substance bar.",
             "created_utc": 1700000000}
        ]
        _, _, selection = analysis._select_sample(posts, [], "a query", "", 1e18)
        assert selection["triaged"] == 1


class TestSelectionDisclosure:
    """What the report is allowed to say about how the sample was chosen."""

    def _posts(self, count):
        return [
            {"kind": "post", "id": "p{}".format(i), "subreddit": "s",
             "title": "A title long enough to carry the whole idea on its own",
             "body": "A body with plenty of real text in it, past the substance bar.",
             "score": count - i, "comments": 0, "created_utc": 1700000000}
            for i in range(count)
        ]

    def _select(self, monkeypatch, verdicts, count=4, brief=None):
        brief = brief if brief is not None else two_requirements()
        monkeypatch.setattr(
            analysis, "decompose_brief", lambda query, profile="", **kwargs: brief
        )
        monkeypatch.setattr(
            analysis, "rate_relevance",
            lambda items, query, profile, **kwargs: dict(verdicts),
        )
        return analysis._select_sample(self._posts(count), [], EXAMPLE_BRIEF, "")

    def test_the_new_keys_appear_with_the_names_the_report_reads(self, monkeypatch):
        _, _, selection = self._select(monkeypatch, {
            1: analysis.Verdict(2, "yu"),
            2: analysis.Verdict(2, "nu"),
            3: analysis.Verdict(1, "uu"),
            4: analysis.Verdict(1, "yy"),
        })
        assert selection["method"] == "relevance+qualifiers"
        assert selection["pool"] == 4
        assert selection["topic"] == "running Meta ads for B2B SaaS"
        assert selection["qualifier_counts"] == {
            "confirmed": 2, "unconfirmed": 1, "violated": 1
        }
        assert [requirement["kind"] for requirement in selection["requirements"]] == [
            "require", "exclude"
        ]
        # "unreliable" joins the four names the report already read. It is written
        # for every requirement rather than only for a flagged one, so a persisted
        # bundle says false explicitly instead of leaving a reader to guess whether
        # the key is missing because the guard passed or because the run predates it.
        assert selection["requirements"][0] == {
            "text": "The author works at a funded startup, Series A, B or C.",
            "kind": "require", "met": 2, "violated": 1, "unknown": 1,
            "unreliable": False,
        }

    def test_every_requirement_count_sums_to_the_triaged_total(self, monkeypatch):
        _, _, selection = self._select(monkeypatch, {
            1: analysis.Verdict(2, "yu"),
            2: analysis.Verdict(2, "nu"),
            3: analysis.Verdict(1, "uu"),
            4: analysis.Verdict(1, "yy"),
        })
        for requirement in selection["requirements"]:
            total = requirement["met"] + requirement["violated"] + requirement["unknown"]
            assert total == selection["triaged"]

    def test_the_tier_counts_also_sum_to_the_triaged_total(self, monkeypatch):
        _, _, selection = self._select(monkeypatch, {
            1: analysis.Verdict(2, "yu"), 2: analysis.Verdict(2, "nu"),
        })
        assert sum(selection["qualifier_counts"].values()) == selection["triaged"]

    def test_no_new_key_appears_without_a_requirement(self, monkeypatch):
        _, _, selection = self._select(
            monkeypatch, {1: analysis.Verdict(2, "")},
            brief=analysis.Brief("running Meta ads for B2B SaaS", ()),
        )
        assert selection["method"] == "relevance+engagement"
        for key in ("pool", "topic", "requirements", "qualifier_counts",
                    "qualifier_analysed"):
            assert key not in selection

    def test_the_analysed_counts_come_off_the_stamp_and_never_exceed_the_sample(
        self, monkeypatch
    ):
        monkeypatch.setattr(
            analysis, "decompose_brief",
            lambda query, profile="", **kwargs: two_requirements(),
        )
        monkeypatch.setattr(
            analysis, "rate_relevance",
            lambda items, query, profile, **kwargs: {
                1: analysis.Verdict(2, "yu"),
                2: analysis.Verdict(2, "nu"),
                3: analysis.Verdict(2, "uu"),
            },
        )
        monkeypatch.setattr(
            analysis, "fit_sample", lambda p, c, s, budget=None: (list(p)[:2], 200)
        )
        monkeypatch.setattr(
            analysis, "classify_items",
            lambda items, subreddits, preview_chars=200: {
                "topics": [], "item_sentiments": [], "model": "m"
            },
        )
        items, _, classification = analysis.classify_sample(
            self._posts(3), [], ["s"], EXAMPLE_BRIEF, ""
        )
        selection = classification["selection"]
        analysed = selection["qualifier_analysed"]
        assert sum(analysed.values()) == len(items) == 2
        assert sum(analysed.values()) <= sum(selection["qualifier_counts"].values())
        for tier in ("confirmed", "unconfirmed", "violated"):
            assert analysed[tier] <= selection["qualifier_counts"][tier]

    def test_an_unstamped_item_counts_as_unconfirmed(self):
        counts = analysis.analysed_qualifier_counts(
            [{"qualifier_tier": "confirmed"}, {}, {"qualifier_tier": "nonsense"}]
        )
        assert counts == {"confirmed": 1, "unconfirmed": 2, "violated": 0}

    def test_the_stamp_matches_the_tier_the_ranking_used(self, monkeypatch):
        ranked_posts, _, _ = self._select(monkeypatch, {
            1: analysis.Verdict(2, "yu"),
            2: analysis.Verdict(2, "nu"),
            3: analysis.Verdict(2, "uu"),
            4: analysis.Verdict(2, "uu"),
        })
        assert ranked_posts[0]["qualifier_tier"] == "confirmed"
        assert ranked_posts[-1]["qualifier_tier"] == "violated"


class TestGetClient:
    """`_get_client` is the one place a real `genai.Client` gets built. Every other
    test in this file bypasses it entirely (`monkeypatch.setattr(analysis, "_client",
    fake)`), which is why a real per-request timeout needs its own coverage here:
    without it, a hung `generate_content` call has nothing to interrupt it, since the
    pipeline's stage-boundary deadline check only ever runs between stages, never
    inside one already in flight."""

    @pytest.fixture(autouse=True)
    def _reset_client_singleton(self, monkeypatch):
        # `_get_client` memoises into the module-level `_client` global, so a run
        # started earlier in the suite would otherwise make this test observe a
        # client nothing here built. Save and restore rather than just clearing,
        # since a real client built earlier must still be there for later tests.
        monkeypatch.setattr(analysis, "_client", None)
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")

    def test_the_client_is_built_with_a_request_timeout(self, monkeypatch):
        captured = {}

        def fake_client(**kwargs):
            captured.update(kwargs)
            return object()

        monkeypatch.setattr(analysis.genai, "Client", fake_client)

        analysis._get_client()

        http_options = captured["http_options"]
        assert http_options["timeout"] == analysis._MODEL_CALL_TIMEOUT_MS
        # Milliseconds, not seconds: google-genai's own HttpOptions.timeout field is
        # documented in milliseconds, and a value this large only makes sense as one.
        assert http_options["timeout"] >= 1000
        # The backstop has to actually exceed the documented worst-case call (~70s)
        # or it would fire on ordinary synthesis calls instead of only hung ones.
        assert http_options["timeout"] > 70_000

    def test_the_existing_single_attempt_retry_option_survives(self, monkeypatch):
        captured = {}

        def fake_client(**kwargs):
            captured.update(kwargs)
            return object()

        monkeypatch.setattr(analysis.genai, "Client", fake_client)

        analysis._get_client()

        assert captured["http_options"]["retry_options"] == {"attempts": 1}

    def test_the_client_is_memoised_across_calls(self, monkeypatch):
        calls = []

        def fake_client(**kwargs):
            calls.append(kwargs)
            return object()

        monkeypatch.setattr(analysis.genai, "Client", fake_client)

        first = analysis._get_client()
        second = analysis._get_client()

        assert first is second
        assert len(calls) == 1
