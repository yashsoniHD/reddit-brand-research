"""Quoted-phrase parsing and matching: the today-is-unchanged guarantee comes first."""

import pytest

import keywords


class TestParsePhrasesUnchangedToday:
    def test_unquoted_brief_yields_no_phrases_and_unchanged_query(self):
        text = "give me detailed posts about running meta ads for b2b saas"
        result = keywords.parse_phrases(text)
        assert result.phrases == []
        assert result.dropped == []
        assert result.query == text

    def test_none_does_not_raise(self):
        assert keywords.parse_phrases(None) == keywords.PhraseParse([], "", [])

    def test_empty_string_does_not_raise(self):
        assert keywords.parse_phrases("") == keywords.PhraseParse([], "", [])


class TestParsePhrasesExtraction:
    def test_single_phrase_extracted_and_quotes_gone_from_query(self):
        result = keywords.parse_phrases('detailed posts about "meta ads" for b2b saas')
        assert result.phrases == ["meta ads"]
        assert result.query == "detailed posts about meta ads for b2b saas"
        assert result.dropped == []

    def test_curly_quotes_behave_like_straight_quotes(self):
        straight = keywords.parse_phrases('detailed posts about "meta ads" for b2b saas')
        curly = keywords.parse_phrases("detailed posts about “meta ads” for b2b saas")
        assert curly.phrases == straight.phrases == ["meta ads"]
        assert curly.query == straight.query

    def test_single_quotes_never_delimit_a_phrase(self):
        # The regression guard: apostrophes in "don't" and "clients'" must not
        # be read as opening or closing a phrase.
        text = "what don't freelancers understand about clients' budgets"
        result = keywords.parse_phrases(text)
        assert result.phrases == []
        assert result.query == text

    def test_unbalanced_quote_yields_none_and_strips_the_stray_character(self):
        result = keywords.parse_phrases('detailed posts about "meta ads for b2b saas')
        assert result.phrases == []
        assert result.dropped == []
        assert result.query == "detailed posts about meta ads for b2b saas"

    def test_two_phrases_extracted_in_order(self):
        result = keywords.parse_phrases('"meta ads" for "b2b saas" companies')
        assert result.phrases == ["meta ads", "b2b saas"]

    def test_three_phrases_extracted_in_order(self):
        result = keywords.parse_phrases('"one" and "two" and "three"')
        assert result.phrases == ["one", "two", "three"]

    def test_a_fourth_phrase_goes_to_dropped(self):
        result = keywords.parse_phrases('"one" "two" "three" "four"')
        assert result.phrases == ["one", "two", "three"]
        assert result.dropped == ["four"]

    @pytest.mark.parametrize("quoted", ['""', '"   "'])
    def test_empty_pair_ignored_silently(self, quoted):
        result = keywords.parse_phrases(f"posts about {quoted} today")
        assert result.phrases == []
        assert result.dropped == []

    def test_too_short_phrase_dropped(self):
        result = keywords.parse_phrases('the future of "ai" in marketing')
        assert result.phrases == []
        assert result.dropped == ["ai"]

    def test_too_long_phrase_by_characters_dropped(self):
        long_phrase = "x" * (keywords.MAX_PHRASE_CHARS + 10)
        result = keywords.parse_phrases(f'"{long_phrase}"')
        assert result.phrases == []
        assert result.dropped == [long_phrase]

    def test_too_long_phrase_by_word_count_dropped(self):
        nine_words = "one two three four five six seven eight nine"
        result = keywords.parse_phrases(f'"{nine_words}"')
        assert result.phrases == []
        assert result.dropped == [nine_words]

    def test_whole_query_quoted_becomes_one_phrase_and_unquoted_query(self):
        result = keywords.parse_phrases('"posts about meta ads for b2b saas"')
        assert result.phrases == ["posts about meta ads for b2b saas"]
        assert result.query == "posts about meta ads for b2b saas"

    def test_nested_odd_pairing_is_strict_left_to_right(self):
        # Deterministic pairing: quote 1 opens, quote 2 closes the first
        # phrase, quote 3 opens, quote 4 closes the second. Uses phrases long
        # enough to clear MIN_PHRASE_CHARS so the pairing mechanism is tested
        # on its own, independent of the length-floor rule (covered above).
        result = keywords.parse_phrases('"aaa "bbb" ccc"')
        assert result.phrases == ["aaa", "ccc"]

    def test_dedupe_keeps_the_first_spelling(self):
        result = keywords.parse_phrases('"Meta Ads" campaigns mention "meta ads" again')
        assert result.phrases == ["Meta Ads"]


class TestMatchesAll:
    def test_empty_phrase_list_is_true(self):
        assert keywords.matches_all("anything at all", []) is True

    def test_case_insensitive(self):
        assert keywords.matches_all("Running META ADS campaigns", ["meta ads"]) is True

    def test_newline_between_words_still_matches(self):
        assert keywords.matches_all("running meta\nads campaigns", ["meta ads"]) is True

    def test_short_phrase_does_not_match_inside_a_longer_word(self):
        assert keywords.matches_all("a trip to Seoul next year", ["seo"]) is False

    def test_does_not_match_a_different_word(self):
        assert keywords.matches_all("we ran one meta ad last week", ["meta ads"]) is False

    def test_matches_with_trailing_punctuation(self):
        assert keywords.matches_all("we love meta ads, honestly", ["meta ads"]) is True

    def test_matches_inside_parentheses(self):
        assert keywords.matches_all("results (meta ads) were strong", ["meta ads"]) is True

    def test_multi_phrase_requires_all_present_in_any_order(self):
        text = "running b2b saas campaigns with meta ads this quarter"
        assert keywords.matches_all(text, ["meta ads", "b2b saas"]) is True
        assert keywords.matches_all("just b2b saas here", ["meta ads", "b2b saas"]) is False

    def test_regex_metacharacters_match_literally_without_raising(self):
        assert keywords.matches_all("do you use c++ ads in your stack?", ["c++ ads"]) is True
        assert keywords.matches_all("what? seriously?", ["what?"]) is True
