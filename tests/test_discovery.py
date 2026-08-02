"""Discovery: the model proposes subreddits, sanitisation and the archive vet them.

Every case here is a shape a real LLM or a real outage has produced: invented
prefixes, duplicates, markdown in a reason, a candidate that does not exist, a
private or over-18 community, and the lookup service itself being unavailable.
"""

import threading
import time

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
    the string "raise" (service outage). Accepts (and ignores) `deadline` because
    discover()/manual() now call every `lookup` as `lookup(name, deadline=...)`,
    passing it down to `reddit_data.lookup_subreddit` so a call already in flight
    can cut its own retries short (see `_run_lookup`)."""

    def fake(name, deadline=None):
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
        # A candidate that proposed no search terms still gets the key, empty: the
        # pipeline reads it unconditionally to decide search versus scrape.
        assert result == [{"name": "productivity", "reason": "Talks shop", "terms": []}]

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
        # Against the constant, not a literal: MAX_COMMUNITIES is a budget
        # decision that has already moved once.
        assert [c["name"] for c in result["communities"]] == names[: discovery.MAX_COMMUNITIES]
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


class TestDiscoverConcurrentLookups:
    """Candidate lookups run in a thread pool (each is an independent network
    round trip), not one at a time. These pin down what has to survive that: the
    model's ranking order in the result, and the stop-at-cap semantics even
    though validating a candidate no longer happens one at a time."""

    def test_lookups_run_concurrently_not_one_request_at_a_time(self):
        # Each fake lookup blocks for a bit. If they ran serially, three of them
        # would take at least 3 * delay; run concurrently, the whole call should
        # finish in well under that.
        delay = 0.2
        names = ["subaaa", "subbbb", "subccc"]
        raw = {"candidates": [{"name": n, "reason": "x"} for n in names]}

        def lookup(name, deadline=None):
            time.sleep(delay)
            return _found(name)

        started = time.monotonic()
        discovery.discover("q", chat=_chat(raw), lookup=lookup)
        elapsed = time.monotonic() - started
        assert elapsed < delay * len(names)

    def test_result_order_follows_the_models_ranking_not_finish_order(self):
        # subaaa is slowest, subbbb finishes first, subccc is in between. If
        # ordering leaked from thread completion order instead of being read
        # back against the original candidate list, this would come back
        # bbb-ccc-aaa instead of the model's aaa-bbb-ccc.
        raw = {"candidates": [
            {"name": "subaaa", "reason": "a"},
            {"name": "subbbb", "reason": "b"},
            {"name": "subccc", "reason": "c"},
        ]}
        delays = {"subaaa": 0.06, "subbbb": 0.0, "subccc": 0.03}

        def lookup(name, deadline=None):
            time.sleep(delays[name])
            return _found(name)

        result = discovery.discover("q", chat=_chat(raw), lookup=lookup)
        assert [c["name"] for c in result["communities"]] == ["subaaa", "subbbb", "subccc"]

    def test_every_candidate_is_validated_even_though_only_the_cap_survives(self):
        # MAX_CANDIDATES worth of good candidates, all past MAX_COMMUNITIES.
        # Validating all of them concurrently is fine (the audit's explicit
        # trade-off); what must hold is that only the cap's worth make it into
        # the result, in the model's order.
        names = ["sub{:02d}".format(i) for i in range(discovery.MAX_CANDIDATES)]
        raw = {"candidates": [{"name": n, "reason": "x"} for n in names]}
        looked_up = []

        def lookup(name, deadline=None):
            looked_up.append(name)
            return _found(name)

        result = discovery.discover("q", chat=_chat(raw), lookup=lookup)
        assert [c["name"] for c in result["communities"]] == names[: discovery.MAX_COMMUNITIES]
        assert sorted(looked_up) == sorted(names)

    def test_a_slow_rejected_candidate_does_not_block_a_faster_later_one(self):
        # subaaa exists but is over-18 (rejected); subbbb is fine. Both are
        # looked up concurrently, so subbbb's accept does not wait on subaaa.
        raw = {"candidates": [
            {"name": "subaaa", "reason": "a"},
            {"name": "subbbb", "reason": "b"},
        ]}

        def lookup(name, deadline=None):
            if name == "subaaa":
                time.sleep(0.05)
                return _found(name, over18=True)
            return _found(name)

        result = discovery.discover("q", chat=_chat(raw), lookup=lookup)
        assert [c["name"] for c in result["communities"]] == ["subbbb"]


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
        def lookup(name, deadline=None):
            return {"name": "Freelance", "subscribers": 120000, "over18": False, "public": True}

        result = discovery.manual(["freelance"], lookup=lookup)
        community = result["communities"][0]
        assert community == {
            "name": "Freelance", "reason": "chosen by you",
            "subscribers": 120000, "checked": True, "terms": [],
        }
        assert result["considered"] == ["freelance"]

    def test_a_failed_lookup_keeps_the_name_rather_than_dropping_it(self):
        def lookup(name, deadline=None):
            raise RuntimeError("archive down")

        community = discovery.manual(["mysub"], lookup=lookup)["communities"][0]
        assert community["name"] == "mysub"
        assert community["checked"] is False

    def test_an_unknown_community_is_kept_for_the_fetch_stage_to_judge(self):
        community = discovery.manual(
            ["ghost"], lookup=lambda name, deadline=None: None
        )["communities"][0]
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

        def lookup(name, deadline=None):
            looked_up.append(name)
            return {"name": name, "subscribers": 1, "over18": False, "public": True}

        with pytest.raises(ValueError):
            discovery.discover(
                "query", chat=self._chat(["one", "two", "three"]), lookup=lookup,
                deadline=discovery.time.monotonic() - 1,
            )
        assert looked_up == []

    def test_a_deadline_expiring_partway_through_the_batch_stops_the_rest(self):
        """Lookups run concurrently, but not all at once when candidates outnumber
        LOOKUP_WORKERS: the pool's task queue is FIFO, so the first LOOKUP_WORKERS
        candidates start immediately and the rest wait for a worker to free up.
        The deadline is rechecked right before each individual lookup fires (see
        `_run_lookup`), so a deadline that only expires once that first wave is
        already in flight must stop the still-queued second wave from starting,
        rather than committing to the full batch regardless.

        A `Barrier` sized to LOOKUP_WORKERS proves exactly that many run
        concurrently before the deadline flips; its `action` callback flips a
        shared "expired" flag while all of them are still blocked on it, so the
        flip is guaranteed to happen before any of the first wave returns and
        frees a worker for the second wave to start on.
        """
        names = ["sub{:02d}".format(i) for i in range(discovery.LOOKUP_WORKERS + 2)]
        expired = {"flag": False}

        def _mark_expired():
            expired["flag"] = True

        first_wave = threading.Barrier(
            discovery.LOOKUP_WORKERS, action=_mark_expired, timeout=5
        )
        looked_up = []
        lock = threading.Lock()

        def lookup(name, deadline=None):
            with lock:
                looked_up.append(name)
            first_wave.wait()  # blocks until LOOKUP_WORKERS lookups are in flight
            return {"name": name, "subscribers": 1, "over18": False, "public": True}

        def fake_monotonic():
            return 1.0 if expired["flag"] else 0.0

        original = discovery.time.monotonic
        try:
            discovery.time.monotonic = fake_monotonic
            result = discovery.discover(
                "query", chat=self._chat(names), lookup=lookup, deadline=0.5,
            )
        finally:
            discovery.time.monotonic = original

        # Only the first wave (exactly LOOKUP_WORKERS candidates) was ever looked
        # up; the second wave was skipped once the deadline had passed instead of
        # committing to the rest of the batch.
        assert len(looked_up) == discovery.LOOKUP_WORKERS
        assert set(looked_up) == set(names[: discovery.LOOKUP_WORKERS])
        assert [c["name"] for c in result["communities"]] == names[: discovery.LOOKUP_WORKERS]

    def test_a_generous_deadline_changes_nothing(self):
        def lookup(name, deadline=None):
            return {"name": name, "subscribers": 1, "over18": False, "public": True}

        result = discovery.discover(
            "query", chat=self._chat(["one", "two"]), lookup=lookup,
            deadline=discovery.time.monotonic() + 3600,
        )
        assert len(result["communities"]) == 2

    def test_manual_keeps_every_name_even_when_the_clock_has_run_out(self):
        def lookup(name, deadline=None):
            raise AssertionError("no lookup once the clock has run out")

        result = discovery.manual(
            ["one", "two"], lookup=lookup, deadline=discovery.time.monotonic() - 1
        )
        assert [c["name"] for c in result["communities"]] == ["one", "two"]
        assert all(c["checked"] is False for c in result["communities"])
