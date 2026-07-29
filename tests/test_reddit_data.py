"""Input normalisation: the layer between what people type and what we request."""

import pytest

import reddit_data


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
            reddit_data.parse_subreddit_input("one,two,three,four,five")
        assert str(reddit_data.MAX_SUBREDDITS) in str(excinfo.value)

    def test_allows_exactly_the_cap(self):
        assert len(reddit_data.parse_subreddit_input("one,two,three,four")) == 4

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
    def test_query_param_carries_the_phrase_and_not_the_comments_param(self, monkeypatch):
        router = _CapturingRouter([_FakeResponse({"data": [_post_row("a", 1700000000)]})])
        monkeypatch.setattr(reddit_data, "_request_page", router)
        reddit_data.fetch_posts("x", 7, phrases=["meta ads"])
        params = router.calls[0]["params"]
        assert params.get(reddit_data.POSTS_TEXT_PARAM) == "meta ads"
        assert reddit_data.COMMENTS_TEXT_PARAM not in params

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
    def test_body_param_carries_the_phrase_and_not_the_posts_param(self, monkeypatch):
        router = _CapturingRouter([_FakeResponse({"data": [_comment_row("c1", 1700000000)]})])
        monkeypatch.setattr(reddit_data, "_request_page", router)
        reddit_data.fetch_comments("x", 7, phrases=["meta ads"])
        params = router.calls[0]["params"]
        assert params.get(reddit_data.COMMENTS_TEXT_PARAM) == "meta ads"
        assert reddit_data.POSTS_TEXT_PARAM not in params

    def test_page_ceiling_is_the_lower_phrase_ceiling(self, monkeypatch):
        full_page = {
            "data": [_comment_row("c{}".format(i), 1700000000 + i) for i in range(reddit_data.PAGE_SIZE)]
        }
        router = _CapturingRouter([_FakeResponse(full_page)])
        monkeypatch.setattr(reddit_data, "_request_page", router)
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
