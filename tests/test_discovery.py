"""Discovery: the model proposes subreddits, sanitisation and the archive vet them.

Every case here is a shape a real LLM or a real outage has produced: invented
prefixes, duplicates, markdown in a reason, a candidate that does not exist, a
private or over-18 community, and the lookup service itself being unavailable.
"""

import pytest

import discovery


def _chat(raw):
    """A fake `chat` callable matching analysis.chat_json's (prompt, max_tokens, label,
    thinking=True) signature and (parsed, model) return shape. Discovery calls it with
    thinking=False and DISCOVERY_MAX_TOKENS, so the fake must accept the keyword even
    though this test double ignores its value."""

    def fake(prompt, max_tokens, label, thinking=True):
        return raw, "test-model"

    return fake


def _lookup(table):
    """A fake `lookup` callable. `table[name]` is a dict (found), None (missing) or
    the string "raise" (service outage)."""

    def fake(name):
        entry = table.get(name, None)
        if entry == "raise":
            raise RuntimeError("The Reddit data service is temporarily unavailable.")
        return entry

    return fake


def _found(name, subscribers=100, over18=False, public=True):
    return {"name": name, "subscribers": subscribers, "over18": over18, "public": public}


class TestSanitizeCandidates:
    """Direct tests of the trust-nothing gate between the model and the lookup step."""

    def test_accepts_bare_names(self):
        raw = {"candidates": [{"name": "productivity", "reason": "Talks shop"}]}
        result = discovery._sanitize_candidates(raw)
        assert result == [{"name": "productivity", "reason": "Talks shop"}]

    def test_accepts_r_prefixed_names(self):
        raw = {"candidates": [{"name": "r/Productivity", "reason": "x"}]}
        assert discovery._sanitize_candidates(raw)[0]["name"] == "Productivity"

    def test_drops_invalid_names(self):
        raw = {"candidates": [
            {"name": "two words", "reason": "x"},
            {"name": "ab", "reason": "too short"},
            {"name": "", "reason": "empty"},
            {"name": None, "reason": "none"},
            {"name": "sk!ncare", "reason": "bad chars"},
            {"name": "validname", "reason": "kept"},
        ]}
        result = discovery._sanitize_candidates(raw)
        assert [c["name"] for c in result] == ["validname"]

    def test_dedupes_case_insensitively_keeping_first_spelling(self):
        raw = {"candidates": [
            {"name": "Productivity", "reason": "first"},
            {"name": "productivity", "reason": "second"},
            {"name": "PRODUCTIVITY", "reason": "third"},
        ]}
        result = discovery._sanitize_candidates(raw)
        assert len(result) == 1
        assert result[0]["name"] == "Productivity"
        assert result[0]["reason"] == "first"

    def test_strips_markdown_from_reasons(self):
        raw = {"candidates": [
            {"name": "productivity", "reason": "See ![x](http://evil.example/p.png) here"}
        ]}
        assert discovery._sanitize_candidates(raw)[0]["reason"] == "See x here"

    def test_truncates_overlong_reasons_to_max_words(self):
        long_reason = " ".join("word{}".format(i) for i in range(30))
        raw = {"candidates": [{"name": "productivity", "reason": long_reason}]}
        reason = discovery._sanitize_candidates(raw)[0]["reason"]
        assert len(reason.split()) == discovery.MAX_REASON_WORDS

    def test_bare_string_candidates_are_accepted(self):
        raw = {"candidates": ["productivity", "r/programming"]}
        result = discovery._sanitize_candidates(raw)
        assert [c["name"] for c in result] == ["productivity", "programming"]
        assert all(c["reason"] == "" for c in result)

    def test_non_dict_and_non_string_entries_are_skipped(self):
        raw = {"candidates": [None, 5, [], {"name": "productivity", "reason": "x"}]}
        result = discovery._sanitize_candidates(raw)
        assert [c["name"] for c in result] == ["productivity"]

    def test_caps_at_max_candidates(self):
        raw = {"candidates": [
            {"name": "sub{}".format(i), "reason": "x"} for i in range(20)
        ]}
        assert len(discovery._sanitize_candidates(raw)) == discovery.MAX_CANDIDATES

    @pytest.mark.parametrize("raw", [None, [], "text", 5, {"candidates": None}, {"candidates": []}, {}])
    def test_missing_or_wrong_shaped_output_gives_no_candidates(self, raw):
        assert discovery._sanitize_candidates(raw) == []


class TestDiscover:
    def test_lookup_filtering_drops_missing_private_over18_preserves_order_caps_at_four(self):
        raw = {"candidates": [
            {"name": "subaaa", "reason": "r a"},
            {"name": "subbbb", "reason": "r b"},
            {"name": "subccc", "reason": "r c"},
            {"name": "subddd", "reason": "r d"},
            {"name": "subeee", "reason": "r e"},
            {"name": "subfff", "reason": "r f"},
            {"name": "subggg", "reason": "r g"},
        ]}
        table = {
            "subaaa": _found("subaaa"),
            "subbbb": None,                       # does not exist
            "subccc": _found("subccc", over18=True),    # over-18
            "subddd": _found("subddd", public=False),   # private
            "subeee": _found("subeee"),
            "subfff": _found("subfff"),
            "subggg": _found("subggg"),
        }
        result = discovery.discover("what are people saying", chat=_chat(raw), lookup=_lookup(table))
        assert [c["name"] for c in result["communities"]] == ["subaaa", "subeee", "subfff", "subggg"]
        assert all(c["checked"] for c in result["communities"])
        assert result["considered"] == [
            "subaaa", "subbbb", "subccc", "subddd", "subeee", "subfff", "subggg"
        ]

    def test_communities_carry_reason_and_subscriber_count(self):
        raw = {"candidates": [{"name": "subaaa", "reason": "Where people talk shop"}]}
        table = {"subaaa": _found("subaaa", subscribers=54321)}
        result = discovery.discover("q", chat=_chat(raw), lookup=_lookup(table))
        community = result["communities"][0]
        assert community["reason"] == "Where people talk shop"
        assert community["subscribers"] == 54321

    def test_fail_open_when_every_lookup_is_unreachable(self):
        names = ["subaaa", "subbbb", "subccc", "subddd", "subeee"]
        raw = {"candidates": [{"name": name, "reason": "x"} for name in names]}
        table = {name: "raise" for name in names}
        result = discovery.discover("q", chat=_chat(raw), lookup=_lookup(table))
        assert [c["name"] for c in result["communities"]] == names[:4]
        assert all(c["subscribers"] == 0 for c in result["communities"])
        assert all(not c["checked"] for c in result["communities"])

    def test_mixed_failures_and_finds_do_not_trigger_fail_open(self):
        raw = {"candidates": [
            {"name": "subaaa", "reason": "x"},
            {"name": "subbbb", "reason": "x"},
            {"name": "subccc", "reason": "x"},
        ]}
        table = {"subaaa": "raise", "subbbb": _found("subbbb", subscribers=42), "subccc": "raise"}
        result = discovery.discover("q", chat=_chat(raw), lookup=_lookup(table))
        assert [c["name"] for c in result["communities"]] == ["subbbb"]
        assert result["communities"][0]["checked"] is True
        assert result["communities"][0]["subscribers"] == 42

    def test_empty_model_output_raises_value_error(self):
        with pytest.raises(ValueError):
            discovery.discover("q", chat=_chat({}), lookup=_lookup({}))

    def test_garbage_model_output_raises_value_error(self):
        with pytest.raises(ValueError):
            discovery.discover("q", chat=_chat({"candidates": ["!!", "ab", None]}), lookup=_lookup({}))

    def test_all_candidates_failing_lookup_checks_raises_value_error(self):
        raw = {"candidates": [{"name": "subaaa", "reason": "x"}]}
        table = {"subaaa": None}  # exists check comes back negative, not unreachable
        with pytest.raises(ValueError):
            discovery.discover("q", chat=_chat(raw), lookup=_lookup(table))

    def test_blank_query_raises_without_calling_chat(self):
        calls = []

        def chat(prompt, max_tokens, label):
            calls.append(1)
            return {"candidates": []}, "test-model"

        with pytest.raises(ValueError):
            discovery.discover("   ", chat=chat, lookup=_lookup({}))
        assert not calls

    def test_error_message_is_a_plain_sentence(self):
        with pytest.raises(ValueError) as excinfo:
            discovery.discover("q", chat=_chat({}), lookup=_lookup({}))
        message = str(excinfo.value)
        assert message.endswith(".")
        assert "http" not in message


class TestPromptShape:
    def test_prompt_includes_query_and_profile_only_when_present(self):
        without_profile = discovery._discovery_prompt("skincare routines", "")
        with_profile = discovery._discovery_prompt("skincare routines", "I run a skincare brand")
        assert "skincare routines" in without_profile
        assert "I run a skincare brand" not in without_profile
        assert "I run a skincare brand" in with_profile

    def test_prompt_asks_for_json_only(self):
        prompt = discovery._discovery_prompt("skincare routines", "")
        assert "candidates" in prompt
        assert "JSON" in prompt


class TestManual:
    def test_looked_up_names_carry_real_subscriber_counts(self):
        def lookup(name):
            return {"name": "Freelance", "subscribers": 120000, "over18": False, "public": True}

        result = discovery.manual(["freelance"], lookup=lookup)
        community = result["communities"][0]
        assert community == {
            "name": "Freelance", "reason": "chosen by you",
            "subscribers": 120000, "checked": True,
        }
        assert result["considered"] == ["freelance"]

    def test_a_failed_lookup_keeps_the_name_rather_than_dropping_it(self):
        def lookup(name):
            raise RuntimeError("archive down")

        community = discovery.manual(["mysub"], lookup=lookup)["communities"][0]
        assert community["name"] == "mysub"
        assert community["checked"] is False

    def test_an_unknown_community_is_kept_for_the_fetch_stage_to_judge(self):
        community = discovery.manual(["ghost"], lookup=lambda name: None)["communities"][0]
        assert community["name"] == "ghost"
        assert community["checked"] is False
        assert community["subscribers"] == 0


class TestDeadline:
    """A run that has spent its time should not go on checking communities it will
    never have time to read."""

    @staticmethod
    def _chat(names):
        def chat(prompt, max_tokens, label, thinking=True):
            return {"candidates": [{"name": name, "reason": "r"} for name in names]}, "m"

        return chat

    def test_a_deadline_already_past_stops_the_candidate_checks(self):
        looked_up = []

        def lookup(name):
            looked_up.append(name)
            return {"name": name, "subscribers": 1, "over18": False, "public": True}

        with pytest.raises(ValueError):
            discovery.discover(
                "query", chat=self._chat(["one", "two", "three"]), lookup=lookup,
                deadline=discovery.time.monotonic() - 1,
            )
        assert looked_up == []

    def test_a_deadline_reached_partway_keeps_what_was_already_checked(self):
        clock = {"now": discovery.time.monotonic()}
        looked_up = []

        def lookup(name):
            looked_up.append(name)
            # The clock runs out the moment the first community is in hand.
            clock["now"] += 100
            return {"name": name, "subscribers": 1, "over18": False, "public": True}

        original = discovery.time.monotonic
        try:
            discovery.time.monotonic = lambda: clock["now"]
            result = discovery.discover(
                "query", chat=self._chat(["one", "two", "three"]), lookup=lookup,
                deadline=clock["now"] + 50,
            )
        finally:
            discovery.time.monotonic = original

        assert looked_up == ["one"]
        assert [c["name"] for c in result["communities"]] == ["one"]

    def test_a_generous_deadline_changes_nothing(self):
        def lookup(name):
            return {"name": name, "subscribers": 1, "over18": False, "public": True}

        result = discovery.discover(
            "query", chat=self._chat(["one", "two"]), lookup=lookup,
            deadline=discovery.time.monotonic() + 3600,
        )
        assert len(result["communities"]) == 2

    def test_manual_keeps_every_name_even_when_the_clock_has_run_out(self):
        def lookup(name):
            raise AssertionError("no lookup once the clock has run out")

        result = discovery.manual(
            ["one", "two"], lookup=lookup, deadline=discovery.time.monotonic() - 1
        )
        assert [c["name"] for c in result["communities"]] == ["one", "two"]
        assert all(c["checked"] is False for c in result["communities"])
