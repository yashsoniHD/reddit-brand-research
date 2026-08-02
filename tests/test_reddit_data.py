"""Input normalisation: the layer between what people type and what we request."""

import time

import pytest

import discovery
import reddit_data


@pytest.fixture(autouse=True)
def _clear_lookup_cache():
    """`lookup_subreddit` now caches; without this, one test's fake response for
    "productivity" would leak into the next test that looks up the same name."""
    reddit_data._lookup_cache.clear()
    yield
    reddit_data._lookup_cache.clear()


class TestNormalizeSubreddit:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("productivity", "productivity"),
            ("  productivity  ", "productivity"),
            ("r/productivity", "productivity"),
            ("/r/productivity", "productivity"),
            ("R/Productivity", "Productivity"),
            ("/R/productivity/", "productivity"),
            ("reddit.com/r/productivity", "productivity"),
            ("www.reddit.com/r/productivity", "productivity"),
            ("https://www.reddit.com/r/productivity", "productivity"),
            ("https://www.reddit.com/r/productivity/", "productivity"),
            ("http://old.reddit.com/r/SkincareAddiction/", "SkincareAddiction"),
            ("https://www.reddit.com/r/x_y-z/comments/abc123/some_title/", "x_y-z"),
            ("https://www.reddit.com/r/productivity/?sort=new", "productivity"),
        ],
    )
    def test_accepts_every_reasonable_form(self, raw, expected):
        assert reddit_data.normalize_subreddit(raw) == expected

    @pytest.mark.parametrize(
        "raw", ["", "   ", None, "two words", "sk!ncare", "ab", "r/", "https://reddit.com/r/",
                "a" * 22, "with space/here"]
    )
    def test_rejects_impossible_names(self, raw):
        with pytest.raises(ValueError):
            reddit_data.normalize_subreddit(raw)

    def test_error_message_is_a_sentence_without_internals(self):
        with pytest.raises(ValueError) as excinfo:
            reddit_data.normalize_subreddit("two words")
        message = str(excinfo.value)
        assert message.endswith(".")
        assert "http" not in message
        assert "regex" not in message.lower()


class TestManualCapMatchesDiscovery:
    """A manual pick may not read more communities than automatic discovery would.
    The two constants live in separate modules (discovery imports reddit_data, not
    the reverse) so nothing enforces this at import time; this test is what keeps
    them from silently drifting apart again."""

    def test_manual_cap_matches_discovery_cap(self):
        assert reddit_data.MAX_SUBREDDITS == discovery.MAX_COMMUNITIES


class TestParseSubredditInput:
    def test_splits_and_normalizes(self):
        assert reddit_data.parse_subreddit_input(
            "r/one, https://reddit.com/r/two/, THREE"
        ) == ["one", "two", "THREE"]

    def test_dedupes_case_insensitively_keeping_first_spelling(self):
        assert reddit_data.parse_subreddit_input("Productivity, productivity") == ["Productivity"]

    def test_ignores_empty_segments(self):
        assert reddit_data.parse_subreddit_input("one,,two, ") == ["one", "two"]

    def test_rejects_more_than_the_cap(self):
        with pytest.raises(ValueError) as excinfo:
            reddit_data.parse_subreddit_input("one,two,three,four,five,six,seven,eight,nine")
        assert str(reddit_data.MAX_SUBREDDITS) in str(excinfo.value)

    def test_allows_exactly_the_cap(self):
        assert len(
            reddit_data.parse_subreddit_input("one,two,three,four,five,six,seven,eight")
        ) == 8

    @pytest.mark.parametrize("raw", ["", "   ", None, ","])
    def test_rejects_empty_input(self, raw):
        with pytest.raises(ValueError):
            reddit_data.parse_subreddit_input(raw)

    def test_one_bad_name_fails_the_whole_field(self):
        with pytest.raises(ValueError):
            reddit_data.parse_subreddit_input("productivity, not a name")


class TestSafeInt:
    """Every number in an archive record goes through this. The archive is a
    community mirror, not a typed API, so any field can arrive as anything."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            (5, 5), ("5", 5), (5.9, 5), ("5.9", 5), (" 7 ", 7), (-3, -3),
            (None, 0), ("", 0), ("bogus", 0), ([], 0), ({}, 0),
            (True, 0), (False, 0),
        ],
    )
    def test_coerces_anything_without_raising(self, raw, expected):
        assert reddit_data._safe_int(raw) == expected

    def test_honours_a_custom_default(self):
        assert reddit_data._safe_int("nope", default=-1) == -1


class TestLookupSubreddit:
    """This has to tell "does not exist" apart from "could not check": discovery
    treats those two cases very differently."""

    @staticmethod
    def _router(monkeypatch, exact=()):
        def fake_request(url, params, **kwargs):
            return _FakeResponse({"data": list(exact)})

        monkeypatch.setattr(reddit_data, "_request_page", fake_request)

    def test_found_returns_canonical_casing_and_subscribers(self, monkeypatch):
        self._router(monkeypatch, exact=[
            {"display_name": "Productivity", "subscribers": 123456, "over18": False,
             "subreddit_type": "public"}
        ])
        result = reddit_data.lookup_subreddit("productivity")
        assert result == {
            "name": "Productivity", "subscribers": 123456, "over18": False, "public": True,
        }

    def test_not_found_returns_none(self, monkeypatch):
        self._router(monkeypatch, exact=[])
        assert reddit_data.lookup_subreddit("nosuchsubredditxyz") is None

    def test_near_miss_results_do_not_count_as_a_match(self, monkeypatch):
        self._router(monkeypatch, exact=[{"display_name": "productivitycafe"}])
        assert reddit_data.lookup_subreddit("productivity") is None

    def test_service_down_raises_runtime_error(self, monkeypatch):
        def explode(*args, **kwargs):
            raise reddit_data.requests.ConnectionError("down")

        monkeypatch.setattr(reddit_data, "_request_page", explode)
        with pytest.raises(RuntimeError):
            reddit_data.lookup_subreddit("productivity")

    def test_private_and_over18_flags_are_surfaced(self, monkeypatch):
        self._router(monkeypatch, exact=[
            {"display_name": "somethingPrivate", "subreddit_type": "private", "over18": False}
        ])
        result = reddit_data.lookup_subreddit("somethingPrivate")
        assert result["public"] is False

        self._router(monkeypatch, exact=[
            {"display_name": "somethingAdult", "subreddit_type": "public", "over18": True}
        ])
        result = reddit_data.lookup_subreddit("somethingAdult")
        assert result["over18"] is True

    def test_missing_subscriber_count_defaults_to_zero(self, monkeypatch):
        self._router(monkeypatch, exact=[{"display_name": "productivity"}])
        assert reddit_data.lookup_subreddit("productivity")["subscribers"] == 0

    def test_negative_or_malformed_subscriber_count_is_clamped(self, monkeypatch):
        self._router(monkeypatch, exact=[{"display_name": "productivity", "subscribers": "bogus"}])
        assert reddit_data.lookup_subreddit("productivity")["subscribers"] == 0


class TestLookupSubredditCache:
    """`lookup_subreddit` is pure metadata, so a short TTL cache in front of it is
    safe. These pin down hit, miss, expiry and the "never cache a failure" rule."""

    def test_repeated_lookup_within_ttl_does_not_hit_the_network_again(self, monkeypatch):
        calls = {"n": 0}

        def fake_request(url, params, **kwargs):
            calls["n"] += 1
            return _FakeResponse({"data": [
                {"display_name": "productivity", "subscribers": 100, "over18": False,
                 "subreddit_type": "public"}
            ]})

        monkeypatch.setattr(reddit_data, "_request_page", fake_request)
        first = reddit_data.lookup_subreddit("productivity")
        second = reddit_data.lookup_subreddit("productivity")
        assert first == second
        assert calls["n"] == 1

    def test_cache_key_is_case_insensitive(self, monkeypatch):
        calls = {"n": 0}

        def fake_request(url, params, **kwargs):
            calls["n"] += 1
            return _FakeResponse({"data": [
                {"display_name": "Productivity", "subscribers": 1, "over18": False,
                 "subreddit_type": "public"}
            ]})

        monkeypatch.setattr(reddit_data, "_request_page", fake_request)
        reddit_data.lookup_subreddit("Productivity")
        reddit_data.lookup_subreddit("PRODUCTIVITY")
        assert calls["n"] == 1

    def test_a_not_found_result_is_cached_too(self, monkeypatch):
        calls = {"n": 0}

        def fake_request(url, params, **kwargs):
            calls["n"] += 1
            return _FakeResponse({"data": []})

        monkeypatch.setattr(reddit_data, "_request_page", fake_request)
        assert reddit_data.lookup_subreddit("nosuchsubredditxyz") is None
        assert reddit_data.lookup_subreddit("nosuchsubredditxyz") is None
        assert calls["n"] == 1

    def test_expiry_after_the_ttl_triggers_a_fresh_lookup(self, monkeypatch):
        calls = {"n": 0}
        clock = {"now": reddit_data.time.monotonic()}

        def fake_request(url, params, **kwargs):
            calls["n"] += 1
            return _FakeResponse({"data": [
                {"display_name": "productivity", "subscribers": calls["n"], "over18": False,
                 "subreddit_type": "public"}
            ]})

        monkeypatch.setattr(reddit_data, "_request_page", fake_request)
        monkeypatch.setattr(reddit_data.time, "monotonic", lambda: clock["now"])

        first = reddit_data.lookup_subreddit("productivity")
        clock["now"] += reddit_data.LOOKUP_CACHE_TTL_SECONDS + 1
        second = reddit_data.lookup_subreddit("productivity")

        assert calls["n"] == 2
        assert first["subscribers"] == 1
        assert second["subscribers"] == 2

    def test_just_under_the_ttl_still_hits_the_cache(self, monkeypatch):
        calls = {"n": 0}
        clock = {"now": reddit_data.time.monotonic()}

        def fake_request(url, params, **kwargs):
            calls["n"] += 1
            return _FakeResponse({"data": [
                {"display_name": "productivity", "subscribers": 1, "over18": False,
                 "subreddit_type": "public"}
            ]})

        monkeypatch.setattr(reddit_data, "_request_page", fake_request)
        monkeypatch.setattr(reddit_data.time, "monotonic", lambda: clock["now"])

        reddit_data.lookup_subreddit("productivity")
        clock["now"] += reddit_data.LOOKUP_CACHE_TTL_SECONDS - 1
        reddit_data.lookup_subreddit("productivity")

        assert calls["n"] == 1

    def test_a_service_failure_is_never_cached(self, monkeypatch):
        calls = {"n": 0}

        def explode(*args, **kwargs):
            calls["n"] += 1
            raise reddit_data.requests.ConnectionError("down")

        monkeypatch.setattr(reddit_data, "_request_page", explode)
        with pytest.raises(RuntimeError):
            reddit_data.lookup_subreddit("productivity")
        with pytest.raises(RuntimeError):
            reddit_data.lookup_subreddit("productivity")
        assert calls["n"] == 2

    def test_deadline_is_passed_all_the_way_down_to_request_page(self, monkeypatch):
        """A lookup issued near the end of a run's time budget must be able to cut
        its own retries short (see `_request_page`'s deadline check), which only
        works if the deadline actually reaches it rather than stopping at
        `_search_subreddits`."""
        captured = {}

        def fake_request(url, params, timeout=None, deadline=None):
            captured["deadline"] = deadline
            return _FakeResponse({"data": []})

        monkeypatch.setattr(reddit_data, "_request_page", fake_request)
        deadline = reddit_data.time.monotonic() + 5
        reddit_data.lookup_subreddit("productivity", deadline=deadline)
        assert captured["deadline"] == deadline

    def test_returned_dict_is_a_copy_a_caller_cannot_poison_the_cache_with(self, monkeypatch):
        def fake_request(url, params, **kwargs):
            return _FakeResponse({"data": [
                {"display_name": "productivity", "subscribers": 100, "over18": False,
                 "subreddit_type": "public"}
            ]})

        monkeypatch.setattr(reddit_data, "_request_page", fake_request)
        first = reddit_data.lookup_subreddit("productivity")
        first["subscribers"] = 999999
        first["name"] = "tampered"

        second = reddit_data.lookup_subreddit("productivity")
        assert second["subscribers"] == 100
        assert second["name"] == "productivity"

    def test_expired_entries_are_purged_on_the_next_write_not_left_forever(self, monkeypatch):
        clock = {"now": reddit_data.time.monotonic()}
        monkeypatch.setattr(reddit_data.time, "monotonic", lambda: clock["now"])
        monkeypatch.setattr(
            reddit_data, "_request_page",
            lambda *a, **k: _FakeResponse({"data": [{"display_name": "subaaa"}]}),
        )

        reddit_data.lookup_subreddit("subaaa")
        assert "subaaa" in reddit_data._lookup_cache

        clock["now"] += reddit_data.LOOKUP_CACHE_TTL_SECONDS + 1
        # A second, different name's lookup is the write that should sweep the
        # now-expired "subaaa" entry out of the dict entirely, not just make it
        # unreachable through _cache_get.
        reddit_data.lookup_subreddit("subbbb")
        assert "subaaa" not in reddit_data._lookup_cache

    def test_cache_size_is_capped_oldest_entries_evicted_first(self, monkeypatch):
        clock = {"now": reddit_data.time.monotonic()}
        monkeypatch.setattr(reddit_data.time, "monotonic", lambda: clock["now"])
        monkeypatch.setattr(
            reddit_data, "_request_page",
            lambda *a, **k: _FakeResponse({"data": []}),
        )

        total = reddit_data.LOOKUP_CACHE_MAX_ENTRIES + 5
        for i in range(total):
            reddit_data.lookup_subreddit("sub{:04d}".format(i))
            clock["now"] += 1  # each write strictly newer than the last

        assert len(reddit_data._lookup_cache) <= reddit_data.LOOKUP_CACHE_MAX_ENTRIES
        # The five oldest (first inserted) should be the ones gone, not an
        # arbitrary five: "sub0000".."sub0004" were written first.
        for i in range(5):
            assert "sub{:04d}".format(i) not in reddit_data._lookup_cache
        assert "sub{:04d}".format(total - 1) in reddit_data._lookup_cache


class TestFetchPosts:
    def test_skips_deleted_bodies_with_no_title_and_dedupes(self, monkeypatch):
        page = {
            "data": [
                {"id": "a", "title": "Real post", "selftext": "body", "score": 5,
                 "num_comments": 2, "permalink": "/r/x/comments/a/", "created_utc": 1700000000,
                 "subreddit": "x"},
                {"id": "b", "title": "", "selftext": "[removed]", "permalink": "/r/x/comments/b/",
                 "created_utc": 1700000001, "subreddit": "x"},
                {"id": "a", "title": "Real post", "selftext": "body", "permalink": "/r/x/comments/a/",
                 "created_utc": 1700000000, "subreddit": "x"},
                {"id": "c", "title": "Kept title", "selftext": "[deleted]",
                 "permalink": "/r/x/comments/c/", "created_utc": 1700000002, "subreddit": "x"},
            ]
        }
        monkeypatch.setattr(reddit_data, "_request_page", lambda *a, **k: _FakeResponse(page))
        posts = reddit_data.fetch_posts("x", 7)
        assert [post["id"] for post in posts] == ["a", "c"]
        assert posts[0]["url"] == "https://reddit.com/r/x/comments/a/"
        assert posts[0]["created_utc"] == 1700000000
        assert posts[1]["body"] == ""  # deleted body dropped, title kept

    def test_client_errors_become_readable_messages(self, monkeypatch):
        monkeypatch.setattr(
            reddit_data, "_request_page", lambda *a, **k: _FakeResponse({}, status_code=404)
        )
        with pytest.raises(ValueError) as excinfo:
            reddit_data.fetch_posts("nope", 7)
        assert "r/nope" in str(excinfo.value)
        assert "404" not in str(excinfo.value)

    def test_first_page_transport_failure_raises_runtime_error(self, monkeypatch):
        def explode(*args, **kwargs):
            raise reddit_data.requests.Timeout("slow")

        monkeypatch.setattr(reddit_data, "_request_page", explode)
        with pytest.raises(RuntimeError):
            reddit_data.fetch_posts("x", 7)

    def test_stringly_typed_numbers_do_not_break_a_fetch(self, monkeypatch):
        page = {
            "data": [
                {"id": "a", "title": "Real post", "selftext": "body", "score": "12",
                 "num_comments": None, "permalink": "/r/x/comments/a/",
                 "created_utc": "1700000000", "subreddit": "x"},
                {"id": "b", "title": "Second", "selftext": "body", "score": {},
                 "num_comments": 3.7, "permalink": "/r/x/comments/b/",
                 "created_utc": "bogus", "subreddit": "x"},
            ]
        }
        monkeypatch.setattr(reddit_data, "_request_page", lambda *a, **k: _FakeResponse(page))
        posts = reddit_data.fetch_posts("x", 7)
        assert posts[0]["score"] == 12 and posts[0]["comments"] == 0
        assert posts[0]["created_utc"] == 1700000000
        assert posts[1]["score"] == 0 and posts[1]["comments"] == 3
        assert posts[1]["created_utc"] == 0 and posts[1]["date"] == ""

    def test_a_deadline_already_past_stops_after_the_first_page(self, monkeypatch):
        calls = {"n": 0}
        page = {
            "data": [
                {"id": "p{}".format(i), "title": "Post {}".format(i), "selftext": "body",
                 "permalink": "/r/x/comments/p{}/".format(i),
                 "created_utc": 1700000000 + i, "subreddit": "x"}
                for i in range(reddit_data.PAGE_SIZE)
            ]
        }

        def counting(*args, **kwargs):
            calls["n"] += 1
            return _FakeResponse(page)

        monkeypatch.setattr(reddit_data, "_request_page", counting)
        posts = reddit_data.fetch_posts("x", 7, deadline=reddit_data.time.monotonic() - 1)
        # The first page still runs: an empty result is worse than a partial one.
        assert calls["n"] == 1
        assert len(posts) == reddit_data.PAGE_SIZE

    def test_a_generous_deadline_pages_normally(self, monkeypatch):
        calls = {"n": 0}
        page = {
            "data": [
                {"id": "p{}".format(i), "title": "Post {}".format(i), "selftext": "body",
                 "permalink": "/r/x/comments/p{}/".format(i),
                 "created_utc": 1700000000 + i, "subreddit": "x"}
                for i in range(reddit_data.PAGE_SIZE)
            ]
        }

        def counting(*args, **kwargs):
            calls["n"] += 1
            return _FakeResponse(page)

        monkeypatch.setattr(reddit_data, "_request_page", counting)
        reddit_data.fetch_posts("x", 7, deadline=reddit_data.time.monotonic() + 3600)
        assert calls["n"] == reddit_data.MAX_POST_PAGES


class TestFetchComments:
    def test_skips_deleted_and_returns_newest_first(self, monkeypatch):
        page = {
            "data": [
                {"id": "c1", "body": "older", "score": 9, "permalink": "/r/x/comments/p/c1/",
                 "created_utc": 1700000000, "subreddit": "x"},
                {"id": "c2", "body": "[deleted]", "permalink": "/r/x/comments/p/c2/",
                 "created_utc": 1700000005, "subreddit": "x"},
                {"id": "c3", "body": "newer", "score": 1, "permalink": "/r/x/comments/p/c3/",
                 "created_utc": 1700000010, "subreddit": "x"},
            ]
        }
        monkeypatch.setattr(reddit_data, "_request_page", lambda *a, **k: _FakeResponse(page))
        comments = reddit_data.fetch_comments("x", 7)
        assert [comment["id"] for comment in comments] == ["c3", "c1"]
        assert comments[0]["kind"] == "comment"

    def test_failure_returns_empty_rather_than_raising(self, monkeypatch):
        def explode(*args, **kwargs):
            raise reddit_data.requests.ConnectionError("down")

        monkeypatch.setattr(reddit_data, "_request_page", explode)
        assert reddit_data.fetch_comments("x", 7) == []

    def test_a_comment_with_no_id_is_identified_by_its_permalink(self, monkeypatch):
        # An empty id used to be stored verbatim, so every id-less comment shared one
        # identity: one relevance rating, applied to all of them.
        page = {
            "data": [
                {"body": "first", "permalink": "/r/x/comments/p/c1/",
                 "created_utc": 1700000000, "subreddit": "x"},
                {"body": "second", "permalink": "/r/x/comments/p/c2/",
                 "created_utc": 1700000001, "subreddit": "x"},
            ]
        }
        monkeypatch.setattr(reddit_data, "_request_page", lambda *a, **k: _FakeResponse(page))
        comments = reddit_data.fetch_comments("x", 7)
        ids = [comment["id"] for comment in comments]
        assert all(ids) and len(set(ids)) == 2

    def test_respects_the_limit(self, monkeypatch):
        page = {
            "data": [
                {"id": "c{}".format(i), "body": "text", "permalink": "/r/x/c{}/".format(i),
                 "created_utc": 1700000000 + i, "subreddit": "x"}
                for i in range(50)
            ]
        }
        monkeypatch.setattr(reddit_data, "_request_page", lambda *a, **k: _FakeResponse(page))
        assert len(reddit_data.fetch_comments("x", 7, limit=10)) == 10


class TestFetchPostsNoPhrases:
    """The today-is-unchanged guarantee at the data layer."""

    def test_params_are_exactly_the_four_today_sends_no_text_key(self, monkeypatch):
        router = _CapturingRouter([_FakeResponse({"data": [_post_row("a", 1700000000)]})])
        monkeypatch.setattr(reddit_data, "_request_page", router)
        posts = reddit_data.fetch_posts("x", 7)
        assert set(router.calls[0]["params"].keys()) == {"subreddit", "limit", "after", "sort"}
        assert "phrase_match" not in posts[0]

    def test_before_cursor_appears_only_once_paging(self, monkeypatch):
        full_page = {
            "data": [_post_row("p{}".format(i), 1700000000 + i) for i in range(reddit_data.PAGE_SIZE)]
        }
        router = _CapturingRouter([_FakeResponse(full_page), _FakeResponse({"data": []})])
        monkeypatch.setattr(reddit_data, "_request_page", router)
        reddit_data.fetch_posts("x", 7)
        assert "before" not in router.calls[0]["params"]
        assert "before" in router.calls[1]["params"]


class TestFetchPostsPhrases:
    def test_query_param_carries_the_phrase_and_not_a_body_param(self, monkeypatch):
        router = _CapturingRouter([_FakeResponse({"data": [_post_row("a", 1700000000)]})])
        monkeypatch.setattr(reddit_data, "_request_page", router)
        reddit_data.fetch_posts("x", 7, phrases=["meta ads"])
        params = router.calls[0]["params"]
        assert params.get(reddit_data.POSTS_TEXT_PARAM) == "meta ads"
        assert "body" not in params

    def test_two_phrases_produce_one_prefilter_string_with_all_words(self, monkeypatch):
        router = _CapturingRouter([_FakeResponse({"data": [_post_row("a", 1700000000)]})])
        monkeypatch.setattr(reddit_data, "_request_page", router)
        reddit_data.fetch_posts("x", 7, phrases=["meta ads", "b2b saas"])
        joined_words = set(router.calls[0]["params"][reddit_data.POSTS_TEXT_PARAM].split())
        assert joined_words == {"meta", "ads", "b2b", "saas"}

    def test_oversized_phrase_set_sends_only_the_first_phrase(self, monkeypatch):
        long1, long2 = "a" * 70, "b" * 70
        assert len(long1) + 1 + len(long2) > reddit_data.MAX_TEXT_PARAM_CHARS
        row = _post_row("a", 1700000000, title=long1, selftext="unrelated body text")
        router = _CapturingRouter([_FakeResponse({"data": [row]})])
        monkeypatch.setattr(reddit_data, "_request_page", router)
        posts = reddit_data.fetch_posts("x", 7, phrases=[long1, long2])
        assert router.calls[0]["params"][reddit_data.POSTS_TEXT_PARAM] == long1
        # The post-filter still demands both phrases: this item has only long1.
        assert posts[0]["phrase_match"] is False

    def test_items_are_not_filtered_only_stamped(self, monkeypatch):
        rows = [
            _post_row("a", 1700000000, title="meta ads works great"),
            _post_row("b", 1700000001, title="something unrelated"),
            _post_row("c", 1700000002, title="another unrelated thing"),
        ]
        router = _CapturingRouter([_FakeResponse({"data": rows})])
        monkeypatch.setattr(reddit_data, "_request_page", router)
        posts = reddit_data.fetch_posts("x", 7, phrases=["meta ads"])
        assert len(posts) == 3
        assert [post["phrase_match"] for post in posts] == [True, False, False]

    def test_page_ceiling_is_the_lower_phrase_ceiling(self, monkeypatch):
        full_page = {
            "data": [_post_row("p{}".format(i), 1700000000 + i) for i in range(reddit_data.PAGE_SIZE)]
        }
        router = _CapturingRouter([_FakeResponse(full_page)])  # repeats when exhausted
        monkeypatch.setattr(reddit_data, "_request_page", router)
        reddit_data.fetch_posts("x", 7, phrases=["meta ads"])
        assert len(router.calls) == reddit_data.PHRASE_MAX_POST_PAGES

    def test_page_ceiling_without_phrases_is_the_default(self, monkeypatch):
        full_page = {
            "data": [_post_row("p{}".format(i), 1700000000 + i) for i in range(reddit_data.PAGE_SIZE)]
        }
        router = _CapturingRouter([_FakeResponse(full_page)])
        monkeypatch.setattr(reddit_data, "_request_page", router)
        reddit_data.fetch_posts("x", 7)
        assert len(router.calls) == reddit_data.MAX_POST_PAGES


class TestFetchPosts400Retry:
    def test_400_with_phrases_retries_without_text_params_and_does_not_raise(self, monkeypatch):
        row = _post_row("a", 1700000000)
        router = _CapturingRouter(
            [_FakeResponse({}, status_code=400), _FakeResponse({"data": [row]})]
        )
        monkeypatch.setattr(reddit_data, "_request_page", router)
        posts = reddit_data.fetch_posts("x", 7, phrases=["meta ads"])
        assert len(router.calls) == 2
        assert reddit_data.POSTS_TEXT_PARAM in router.calls[0]["params"]
        assert reddit_data.POSTS_TEXT_PARAM not in router.calls[1]["params"]
        assert [post["id"] for post in posts] == ["a"]

    def test_400_on_both_attempts_raises_the_existing_error(self, monkeypatch):
        router = _CapturingRouter(
            [_FakeResponse({}, status_code=400), _FakeResponse({}, status_code=400)]
        )
        monkeypatch.setattr(reddit_data, "_request_page", router)
        with pytest.raises(ValueError) as excinfo:
            reddit_data.fetch_posts("x", 7, phrases=["meta ads"])
        assert "r/x" in str(excinfo.value)
        assert len(router.calls) == 2

    def test_400_without_phrases_raises_immediately_with_one_request(self, monkeypatch):
        router = _CapturingRouter([_FakeResponse({}, status_code=400)])
        monkeypatch.setattr(reddit_data, "_request_page", router)
        with pytest.raises(ValueError):
            reddit_data.fetch_posts("x", 7)
        assert len(router.calls) == 1


class TestFetchPostsMetadata:
    def test_captures_author_flair_and_upvote_ratio(self, monkeypatch):
        row = _post_row(
            "a", 1700000000, author="funded_founder", author_flair_text="Series B",
            link_flair_text="Discussion", upvote_ratio="0.93",
        )
        monkeypatch.setattr(reddit_data, "_request_page", lambda *a, **k: _FakeResponse({"data": [row]}))
        posts = reddit_data.fetch_posts("x", 7)
        assert posts[0]["author"] == "funded_founder"
        assert posts[0]["author_flair_text"] == "Series B"
        assert posts[0]["link_flair_text"] == "Discussion"
        assert posts[0]["upvote_ratio"] == 0.93

    def test_missing_metadata_defaults_to_empty_and_zero(self, monkeypatch):
        row = _post_row("a", 1700000000)
        monkeypatch.setattr(reddit_data, "_request_page", lambda *a, **k: _FakeResponse({"data": [row]}))
        posts = reddit_data.fetch_posts("x", 7)
        assert posts[0]["author"] == ""
        assert posts[0]["author_flair_text"] == ""
        assert posts[0]["link_flair_text"] == ""
        assert posts[0]["upvote_ratio"] == 0.0

    def test_upvote_ratio_above_one_clamps_to_one(self, monkeypatch):
        row = _post_row("a", 1700000000, upvote_ratio=1.7)
        monkeypatch.setattr(reddit_data, "_request_page", lambda *a, **k: _FakeResponse({"data": [row]}))
        posts = reddit_data.fetch_posts("x", 7)
        assert posts[0]["upvote_ratio"] == 1.0

    @pytest.mark.parametrize("raw", [None, "bogus", [], {}])
    def test_garbage_upvote_ratio_gives_zero(self, monkeypatch, raw):
        row = _post_row("a", 1700000000, upvote_ratio=raw)
        monkeypatch.setattr(reddit_data, "_request_page", lambda *a, **k: _FakeResponse({"data": [row]}))
        posts = reddit_data.fetch_posts("x", 7)
        assert posts[0]["upvote_ratio"] == 0.0


class TestFetchCommentsPhrases:
    def test_never_sends_a_body_param_even_with_phrases_active(self, monkeypatch):
        # Comment search rejects `body=` at subreddit scope with a 422 on
        # effectively every attempt (docs/ARCTIC_SHIFT_API.md), so sending it
        # only bought MAX_ATTEMPTS wasted requests and their backoff for a page
        # that always came back unfiltered anyway. It is never sent now.
        router = _CapturingRouter([_FakeResponse({"data": [_comment_row("c1", 1700000000)]})])
        monkeypatch.setattr(reddit_data, "_request_page", router)
        reddit_data.fetch_comments("x", 7, phrases=["meta ads"])
        params = router.calls[0]["params"]
        assert "body" not in params
        assert reddit_data.POSTS_TEXT_PARAM not in params
        assert set(params.keys()) == {"subreddit", "limit", "after", "sort"}

    def test_phrase_match_is_still_stamped_without_the_prefilter(self, monkeypatch):
        rows = [
            _comment_row("c1", 1700000000, body="meta ads works great"),
            _comment_row("c2", 1700000001, body="something unrelated"),
        ]
        router = _CapturingRouter([_FakeResponse({"data": rows})])
        monkeypatch.setattr(reddit_data, "_request_page", router)
        comments = reddit_data.fetch_comments("x", 7, phrases=["meta ads"])
        by_id = {c["id"]: c["phrase_match"] for c in comments}
        assert by_id == {"c1": True, "c2": False}

    def test_no_more_422_retries_because_body_is_never_sent(self, monkeypatch):
        """Regression test for the bug this fixed: with `body=` sent, the archive
        422s deterministically at subreddit scope (docs/ARCTIC_SHIFT_API.md), 422
        sits in RETRYABLE_STATUS_CODES, so `_request_page` retried up to
        MAX_ATTEMPTS times with real sleeps and then raised, and fetch_comments
        came back with zero comments. This exercises the real `_request_page`
        (only `requests.get` is faked) so it proves the fix end to end rather
        than just the outgoing params.
        """
        sleeps = []
        monkeypatch.setattr(reddit_data.time, "sleep", lambda seconds: sleeps.append(seconds))

        def fake_get(url, params=None, timeout=None):
            if "body" in params:
                return _FakeResponse({"error": "Timeout. Maybe slow down a bit"}, status_code=422)
            return _FakeResponse({"data": [_comment_row("c1", 1700000000)]})

        monkeypatch.setattr(reddit_data.requests, "get", fake_get)
        comments = reddit_data.fetch_comments("x", 7, phrases=["meta ads"])

        assert sleeps == []  # no retry backoff was ever needed
        assert [c["id"] for c in comments] == ["c1"]

    def test_phrase_mode_page_ceiling_matches_the_unfiltered_ceiling(self, monkeypatch):
        """Regression test for the bug this fixed: phrase mode used to fetch fewer
        pages than unfiltered mode (PHRASE_MAX_COMMENT_PAGES < MAX_COMMENT_PAGES),
        backwards for the mode that has to sift every comment client-side rather
        than rely on an archive-side prefilter. The two ceilings are now equal
        (see PHRASE_MAX_COMMENT_PAGES's definition); this pins down that equality
        rather than a specific page count, so it stays true if both move together.
        """
        full_page = {
            "data": [_comment_row("c{}".format(i), 1700000000 + i) for i in range(reddit_data.PAGE_SIZE)]
        }
        router = _CapturingRouter([_FakeResponse(full_page)])
        monkeypatch.setattr(reddit_data, "_request_page", router)
        assert reddit_data.PHRASE_MAX_COMMENT_PAGES == reddit_data.MAX_COMMENT_PAGES
        reddit_data.fetch_comments("x", 7, limit=100000, phrases=["meta ads"])
        assert len(router.calls) == reddit_data.PHRASE_MAX_COMMENT_PAGES


class TestFetchCommentsMetadata:
    def test_captures_author_but_not_link_flair_or_upvote_ratio(self, monkeypatch):
        row = _comment_row("c1", 1700000000, author="someone", author_flair_text="Head of Growth")
        monkeypatch.setattr(reddit_data, "_request_page", lambda *a, **k: _FakeResponse({"data": [row]}))
        comments = reddit_data.fetch_comments("x", 7)
        assert comments[0]["author"] == "someone"
        assert comments[0]["author_flair_text"] == "Head of Growth"
        assert "link_flair_text" not in comments[0]
        assert "upvote_ratio" not in comments[0]


class TestSafeFloat:
    @pytest.mark.parametrize(
        "raw,expected",
        [(0.93, 0.93), ("0.93", 0.93), (1.7, 1.7), (0, 0.0), (None, 0.0), ("bogus", 0.0),
         ([], 0.0), ({}, 0.0), (True, 0.0), (False, 0.0)],
    )
    def test_coerces_anything_without_raising(self, raw, expected):
        assert reddit_data._safe_float(raw) == expected

    def test_honours_a_custom_default(self):
        assert reddit_data._safe_float("nope", default=-1.0) == -1.0


class TestRequestPageDeadline:
    def test_does_not_sleep_or_retry_when_deadline_already_past(self, monkeypatch):
        calls = {"n": 0}

        def fake_get(url, params=None, timeout=None):
            calls["n"] += 1
            return _FakeResponse({}, status_code=422)  # a retryable status

        def boom_if_called(*args, **kwargs):
            raise AssertionError("should not sleep once the deadline has passed")

        monkeypatch.setattr(reddit_data.requests, "get", fake_get)
        monkeypatch.setattr(reddit_data.time, "sleep", boom_if_called)

        past_deadline = reddit_data.time.monotonic() - 1
        with pytest.raises(reddit_data.requests.RequestException):
            reddit_data._request_page("http://example.test", {}, deadline=past_deadline)
        assert calls["n"] == 1


def _post_row(post_id, created_utc, **overrides):
    row = {
        "id": post_id,
        "title": "Post {}".format(post_id),
        "selftext": "body",
        "permalink": "/r/x/comments/{}/".format(post_id),
        "created_utc": created_utc,
        "subreddit": "x",
    }
    row.update(overrides)
    return row


def _comment_row(comment_id, created_utc, **overrides):
    row = {
        "id": comment_id,
        "body": "a comment body",
        "permalink": "/r/x/comments/p/{}/".format(comment_id),
        "created_utc": created_utc,
        "subreddit": "x",
    }
    row.update(overrides)
    return row


class _CapturingRouter:
    """Records every call's params and replays canned responses in order.

    Once the canned responses are exhausted, the last one repeats: several
    tests need to page past the ceiling with an always-full page rather than
    scripting an exact number of responses.
    """

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def __call__(self, url, params, timeout=None, deadline=None):
        self.calls.append({"url": url, "params": dict(params)})
        index = min(len(self.calls) - 1, len(self._responses) - 1)
        return self._responses[index]


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise reddit_data.requests.HTTPError(str(self.status_code))


# ── Searching ─────────────────────────────────────────────────────────────────
class TestCleanSearchTerms:
    def test_collapses_whitespace_and_dedupes_case_insensitively(self):
        assert reddit_data.clean_search_terms(
            ["  ad   spend ", "AD SPEND", "attribution"]
        ) == ["ad spend", "attribution"]

    def test_caps_at_max_search_terms(self):
        many = ["one", "two", "three", "four"]
        assert len(reddit_data.clean_search_terms(many)) == reddit_data.MAX_SEARCH_TERMS

    @pytest.mark.parametrize("raw", [None, [], ["", "   "], [None, 5, True, {}]])
    def test_drops_everything_unusable(self, raw):
        assert reddit_data.clean_search_terms(raw) == []


class TestSearchTermForPhrases:
    def test_joins_every_phrase_into_one_and_query(self):
        # One string, not one per phrase: `query=` ANDs its words and the
        # post-filter ANDs its phrases, so separate terms would union them.
        assert reddit_data.search_term_for_phrases(
            ["meta ads", "series b"]
        ) == "meta ads series b"

    def test_falls_back_to_the_first_phrase_when_too_long(self):
        long_phrase = "a" * 100
        joined = reddit_data.search_term_for_phrases([long_phrase, "b" * 100])
        assert joined == long_phrase

    def test_no_phrases_is_no_term(self):
        assert reddit_data.search_term_for_phrases([]) == ""
        assert reddit_data.search_term_for_phrases(None) == ""


class TestTheSearchPacer:
    """The gate that keeps two text searches from starting together.

    Driven by a fake clock, so these assert what the pacing does rather than
    waiting out real seconds. The rest of the suite runs with a zero-spacing
    pacer (see tests/conftest.py), which is why the behaviour is pinned here.
    """

    class Clock:
        """A clock that only moves when something sleeps on it."""

        def __init__(self):
            self.now = 1000.0
            self.slept = []

        def monotonic(self):
            return self.now

        def sleep(self, seconds):
            self.slept.append(seconds)
            self.now += seconds

    def test_the_first_caller_is_never_held(self):
        clock = self.Clock()
        pacer = reddit_data._StartPacer(1.5, clock=clock.monotonic, sleep=clock.sleep)
        pacer.wait()
        assert clock.slept == []

    def test_the_next_caller_waits_out_the_spacing(self):
        clock = self.Clock()
        pacer = reddit_data._StartPacer(1.5, clock=clock.monotonic, sleep=clock.sleep)
        pacer.wait()
        pacer.wait()
        assert clock.slept == [1.5]

    def test_a_caller_that_arrives_late_is_not_held_at_all(self):
        # The spacing is a floor on how close two starts may be, not a delay
        # charged to every request: a request that arrives after the gap has
        # already elapsed goes straight through.
        clock = self.Clock()
        pacer = reddit_data._StartPacer(1.5, clock=clock.monotonic, sleep=clock.sleep)
        pacer.wait()
        clock.now += 10
        pacer.wait()
        assert clock.slept == []

    def test_three_callers_are_spaced_one_gap_apart_each(self):
        clock = self.Clock()
        pacer = reddit_data._StartPacer(1.5, clock=clock.monotonic, sleep=clock.sleep)
        started = clock.now
        for _ in range(3):
            pacer.wait()
        assert clock.now - started == pytest.approx(3.0)

    def test_zero_spacing_holds_nobody(self):
        # What the suite runs with, and what a deployment that wanted the pacing
        # off would set: it must not turn into a busy loop.
        clock = self.Clock()
        pacer = reddit_data._StartPacer(0, clock=clock.monotonic, sleep=clock.sleep)
        for _ in range(5):
            pacer.wait()
        assert clock.slept == []

    def test_the_module_pacer_uses_the_probed_spacing(self, unpaced_search):
        # `unpaced_search` hands back the real pacer it displaced for the suite,
        # so this asserts on what production runs with, not on the stand-in.
        assert reddit_data.SEARCH_MIN_SPACING_SECONDS >= 1.0
        assert unpaced_search._spacing == reddit_data.SEARCH_MIN_SPACING_SECONDS


class TestSearchPosts:
    def test_every_search_request_takes_a_start_slot_first(self, monkeypatch):
        """The pacing is what allows the caller to run several of these at once.

        Asserted at the call site rather than only on the pacer, because a
        request that skipped the gate would still pass every other test in this
        class and would only show up as 429s against the live archive.
        """
        taken = []

        class SpyPacer:
            def wait(self):
                taken.append(True)

        monkeypatch.setattr(reddit_data, "_search_pacer", SpyPacer())
        monkeypatch.setattr(
            reddit_data,
            "_request_page",
            lambda url, params, timeout=None, deadline=None: _FakeResponse({"data": []}),
        )
        reddit_data.search_posts("x", ["one", "two"])
        assert len(taken) == 2

    def _capture(self, monkeypatch, payloads):
        """Record every request and answer from `payloads`, one per call."""
        calls = []

        def request(url, params, timeout=None, deadline=None):
            calls.append({"url": url, "params": dict(params), "timeout": timeout})
            payload = payloads[min(len(calls) - 1, len(payloads) - 1)]
            if isinstance(payload, int):
                return _FakeResponse({"data": None, "error": "nope"}, status_code=payload)
            return _FakeResponse(payload)

        monkeypatch.setattr(reddit_data, "_request_page", request)
        return calls

    def test_sends_query_and_never_sends_a_window(self, monkeypatch):
        calls = self._capture(monkeypatch, [{"data": [_post_row("a", 1700000000)]}])
        posts = reddit_data.search_posts("x", ["ad spend"])
        assert [post["id"] for post in posts] == ["a"]
        assert calls[0]["params"]["query"] == "ad spend"
        assert calls[0]["params"]["subreddit"] == "x"
        assert calls[0]["params"]["sort"] == "desc"
        # The whole point of search mode: no `after`, so the most recent matches
        # come back however old they are.
        assert "after" not in calls[0]["params"]
        assert "before" not in calls[0]["params"]

    def test_uses_the_longer_search_timeout(self, monkeypatch):
        calls = self._capture(monkeypatch, [{"data": []}])
        reddit_data.search_posts("x", ["ad spend"])
        assert calls[0]["timeout"] == reddit_data.SEARCH_TIMEOUT_SECONDS
        assert reddit_data.SEARCH_TIMEOUT_SECONDS > reddit_data.REQUEST_TIMEOUT_SECONDS

    def test_one_request_per_term_merged_and_deduped(self, monkeypatch):
        calls = self._capture(monkeypatch, [
            {"data": [_post_row("a", 1700000000), _post_row("b", 1700000002)]},
            {"data": [_post_row("b", 1700000002), _post_row("c", 1700000004)]},
        ])
        posts = reddit_data.search_posts("x", ["one", "two"])
        assert [call["params"]["query"] for call in calls] == ["one", "two"]
        # Newest first, each post once.
        assert [post["id"] for post in posts] == ["c", "b", "a"]

    def test_a_failing_term_does_not_lose_the_others(self, monkeypatch):
        self._capture(monkeypatch, [422, {"data": [_post_row("a", 1700000000)]}])
        assert [p["id"] for p in reddit_data.search_posts("x", ["one", "two"])] == ["a"]

    def test_returns_empty_rather_than_raising_when_nothing_answers(self, monkeypatch):
        # A caller falls back to reading the community; a raise here would throw
        # away every community that did answer.
        self._capture(monkeypatch, [500])
        assert reddit_data.search_posts("x", ["one"]) == []

    def test_a_client_error_is_never_reported_as_a_missing_community(self, monkeypatch):
        # `_raise_for_client_error` maps 400 to "no such community", which would be
        # a false claim about a healthy one when the term is what upset the archive.
        self._capture(monkeypatch, [400])
        assert reddit_data.search_posts("x", ["one"]) == []

    def test_no_usable_terms_makes_no_request(self, monkeypatch):
        calls = self._capture(monkeypatch, [{"data": [_post_row("a", 1)]}])
        assert reddit_data.search_posts("x", ["", None]) == []
        assert calls == []

    def test_stamps_phrase_match_without_dropping_anything(self, monkeypatch):
        self._capture(monkeypatch, [{"data": [
            _post_row("a", 1700000002, title="meta ads win", selftext="body"),
            _post_row("b", 1700000001, title="unrelated", selftext="body"),
        ]}])
        posts = reddit_data.search_posts("x", ["meta ads"], phrases=["meta ads"])
        assert [(p["id"], p["phrase_match"]) for p in posts] == [("a", True), ("b", False)]

    def test_stops_between_terms_once_out_of_time(self, monkeypatch):
        calls = self._capture(monkeypatch, [{"data": [_post_row("a", 1)]}])
        reddit_data.search_posts("x", ["one", "two"], deadline=time.monotonic() - 1)
        # The first term is always attempted: an empty result is worse than a
        # partial one. The second is not, because the clock is already gone.
        assert len(calls) == 1

    def test_a_community_cannot_spend_more_than_its_budget(self, monkeypatch):
        # Measured live before this bound existed, one community took 49.9s of a
        # ~180s retrieval budget by stacking 422 retries onto a slow success.
        calls = self._capture(monkeypatch, [{"data": []}])
        reddit_data.search_posts("x", ["one"])
        assert calls[0]["timeout"] <= reddit_data.SEARCH_TIMEOUT_SECONDS
        # The retry gate the request loop sees is this community's own deadline,
        # not the caller's, so retries stop when the share is spent.
        assert reddit_data.SEARCH_BUDGET_SECONDS >= reddit_data.SEARCH_TIMEOUT_SECONDS

    def test_the_caller_deadline_wins_when_it_is_sooner(self, monkeypatch):
        calls = self._capture(monkeypatch, [{"data": []}])
        reddit_data.search_posts("x", ["one"], deadline=time.monotonic() + 6)
        # Sized from what is left, not from the per-attempt ceiling.
        assert calls[0]["timeout"] < reddit_data.SEARCH_TIMEOUT_SECONDS


class TestFetchCommentsForPosts:
    def _capture(self, monkeypatch, by_id):
        calls = []

        def request(url, params, timeout=None, deadline=None):
            calls.append(dict(params))
            rows = by_id.get(params["link_id"], [])
            if isinstance(rows, int):
                return _FakeResponse({"data": None}, status_code=rows)
            return _FakeResponse({"data": rows})

        monkeypatch.setattr(reddit_data, "_request_page", request)
        return calls

    def test_fetches_by_link_id_and_never_filters_on_body(self, monkeypatch):
        calls = self._capture(monkeypatch, {"p1": [_comment_row("c1", 1700000000)]})
        comments = reddit_data.fetch_comments_for_posts(["p1"])
        assert [c["id"] for c in comments] == ["c1"]
        assert calls[0]["link_id"] == "p1"
        # Adding `body=` to a link_id query reintroduces the timeout that scoping
        # by post exists to avoid.
        assert "body" not in calls[0]
        assert "subreddit" not in calls[0]

    def test_every_comment_request_takes_a_start_slot_first(self, monkeypatch):
        # These land immediately after a wave of searches and share that budget:
        # unpaced, 5 of 25 posts lost their replies to exhausted 429 retries.
        taken = []

        class SpyPacer:
            def wait(self):
                taken.append(True)

        monkeypatch.setattr(reddit_data, "_comment_pacer", SpyPacer())
        self._capture(monkeypatch, {"p1": [], "p2": [], "p3": []})
        reddit_data.fetch_comments_for_posts(["p1", "p2", "p3"])
        assert len(taken) == 3

    def test_comments_are_paced_more_gently_than_searches(self):
        # One shared gate at the search spacing would stretch 25 comment
        # requests to nearly 40 seconds for a limit that tolerates them far
        # closer together.
        assert 0 < reddit_data.COMMENT_MIN_SPACING_SECONDS
        assert reddit_data.COMMENT_MIN_SPACING_SECONDS < reddit_data.SEARCH_MIN_SPACING_SECONDS

    def test_a_request_that_waited_past_the_deadline_is_not_issued(self, monkeypatch):
        # The pacer can hold a request for many seconds when 25 of them queue, so
        # the deadline is re-checked on the far side of the wait: spending a
        # request the run can no longer use is exactly what the budget cannot
        # afford.
        calls = self._capture(monkeypatch, {"p1": [_comment_row("c1", 1700000000)]})

        class ExpiringPacer:
            """Stands in for a real wait that outlives the deadline."""

            def __init__(self):
                self.waited = False

            def wait(self):
                self.waited = True

        pacer = ExpiringPacer()
        monkeypatch.setattr(reddit_data, "_comment_pacer", pacer)
        # A clock that is inside the deadline on the first reading and past it on
        # the second: failing the first check already would prove nothing about
        # whether the second one exists.
        now = time.monotonic()
        deadline = now + 100
        readings = iter([now, deadline + 1])
        monkeypatch.setattr(
            reddit_data.time, "monotonic", lambda: next(readings, deadline + 1)
        )
        assert reddit_data.fetch_comments_for_posts(["p1"], deadline=deadline) == []
        assert pacer.waited is True
        assert calls == []

    def test_merges_across_posts_newest_first_and_dedupes(self, monkeypatch):
        self._capture(monkeypatch, {
            "p1": [_comment_row("c1", 1700000000), _comment_row("shared", 1700000009)],
            "p2": [_comment_row("shared", 1700000009), _comment_row("c2", 1700000005)],
        })
        comments = reddit_data.fetch_comments_for_posts(["p1", "p2"])
        assert [c["id"] for c in comments] == ["shared", "c2", "c1"]

    def test_carries_the_thread_id_so_a_reply_is_traceable(self, monkeypatch):
        self._capture(monkeypatch, {
            "p1": [_comment_row("c1", 1700000000, link_id="t3_p1")],
        })
        assert reddit_data.fetch_comments_for_posts(["p1"])[0]["link_id"] == "t3_p1"

    def test_reads_the_subreddit_off_the_record(self, monkeypatch):
        # A link_id request names no subreddit, so the record is the only source.
        self._capture(monkeypatch, {"p1": [_comment_row("c1", 1, subreddit="realsub")]})
        assert reddit_data.fetch_comments_for_posts(["p1"])[0]["subreddit"] == "realsub"

    def test_one_failing_post_does_not_lose_the_others(self, monkeypatch):
        self._capture(monkeypatch, {"p1": 500, "p2": [_comment_row("c2", 1700000000)]})
        assert [c["id"] for c in reddit_data.fetch_comments_for_posts(["p1", "p2"])] == ["c2"]

    def test_caps_the_number_of_posts_and_dedupes_ids(self, monkeypatch):
        calls = self._capture(monkeypatch, {})
        ids = ["p{}".format(n) for n in range(reddit_data.MAX_COMMENT_POSTS + 10)]
        reddit_data.fetch_comments_for_posts(ids + ids)
        assert len(calls) == reddit_data.MAX_COMMENT_POSTS

    def test_no_ids_makes_no_request(self, monkeypatch):
        calls = self._capture(monkeypatch, {})
        assert reddit_data.fetch_comments_for_posts(["", None]) == []
        assert calls == []

    def test_drops_deleted_bodies(self, monkeypatch):
        self._capture(monkeypatch, {"p1": [
            _comment_row("c1", 1700000001, body="[removed]"),
            _comment_row("c2", 1700000000, body="real reply"),
        ]})
        assert [c["id"] for c in reddit_data.fetch_comments_for_posts(["p1"])] == ["c2"]
