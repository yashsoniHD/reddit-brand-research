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


class TestValidateSubreddit:
    """`subreddit` is an exact match on this API and `subreddit_prefix` is the prefix
    search, so validation and suggestions are two different requests."""

    @staticmethod
    def _router(monkeypatch, exact=(), prefix=None):
        prefix = prefix or {}

        def fake_request(url, params, **kwargs):
            if "subreddit_prefix" in params:
                return _FakeResponse({"data": prefix.get(params["subreddit_prefix"], [])})
            return _FakeResponse({"data": list(exact)})

        monkeypatch.setattr(reddit_data, "_request_page", fake_request)

    def test_exact_match_returns_canonical_casing(self, monkeypatch):
        self._router(monkeypatch, exact=[{"display_name": "Productivity"}])
        check = reddit_data.validate_subreddit("productivity")
        assert check.exists and check.name == "Productivity" and check.checked

    def test_missing_community_returns_popular_prefix_suggestions(self, monkeypatch):
        self._router(
            monkeypatch,
            exact=[],
            prefix={"productivit": [
                {"display_name": "productivity", "subreddit_type": "public"},
                {"display_name": "productivitycafe", "subreddit_type": "public"},
                {"display_name": "productivityapps", "subreddit_type": "public"},
                {"display_name": "productivityextra", "subreddit_type": "public"},
            ]},
        )
        check = reddit_data.validate_subreddit("productivit")
        assert not check.exists
        assert check.suggestions == ["productivity", "productivitycafe", "productivityapps"]

    def test_falls_back_to_a_stem_when_the_typo_is_mid_word(self, monkeypatch):
        self._router(
            monkeypatch,
            exact=[],
            prefix={"skinca": [{"display_name": "SkincareAddiction", "subreddit_type": "public"}]},
        )
        check = reddit_data.validate_subreddit("skincareaddictoin")
        assert check.suggestions == ["SkincareAddiction"]

    def test_private_and_adult_communities_are_not_suggested(self, monkeypatch):
        self._router(
            monkeypatch,
            exact=[],
            prefix={"someth": [
                {"display_name": "somethingPrivate", "subreddit_type": "private"},
                {"display_name": "somethingAdult", "subreddit_type": "public", "over18": True},
                {"display_name": "somethingFine", "subreddit_type": "public"},
            ]},
        )
        assert reddit_data.validate_subreddit("somethingelse").suggestions == ["somethingFine"]

    def test_empty_result_is_not_found(self, monkeypatch):
        self._router(monkeypatch, exact=[], prefix={})
        check = reddit_data.validate_subreddit("nosuchsubredditxyz")
        assert not check.exists and check.suggestions == []

    def test_near_miss_results_do_not_count_as_an_exact_match(self, monkeypatch):
        self._router(monkeypatch, exact=[{"display_name": "productivitycafe"}], prefix={})
        assert not reddit_data.validate_subreddit("productivity").exists

    def test_lookup_failure_fails_open(self, monkeypatch):
        def explode(*args, **kwargs):
            raise reddit_data.requests.ConnectionError("down")

        monkeypatch.setattr(reddit_data, "_request_page", explode)
        check = reddit_data.validate_subreddit("productivity")
        assert check.exists and not check.checked

    def test_suggestion_lookup_failure_still_reports_not_found(self, monkeypatch):
        calls = {"n": 0}

        def fake_request(url, params, **kwargs):
            calls["n"] += 1
            if "subreddit_prefix" in params:
                raise reddit_data.requests.ConnectionError("down")
            return _FakeResponse({"data": []})

        monkeypatch.setattr(reddit_data, "_request_page", fake_request)
        check = reddit_data.validate_subreddit("something")
        assert not check.exists and check.suggestions == [] and check.checked


class TestLookupSubreddit:
    """Unlike `validate_subreddit`, this must tell "does not exist" apart from
    "could not check" — discovery treats those two cases very differently."""

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


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise reddit_data.requests.HTTPError(str(self.status_code))
