"""The numbers in the report. If these are wrong, the product is dishonest."""

from datetime import datetime, timedelta, timezone

import pytest

import stats


def _post(created_utc, score=0, comments=0, title="t", body="", url="https://reddit.com/x"):
    return {
        "kind": "post", "title": title, "body": body, "score": score, "comments": comments,
        "url": url, "created_utc": created_utc,
        "date": datetime.fromtimestamp(created_utc, tz=timezone.utc).strftime("%Y-%m-%d"),
    }


class TestLargestRemainder:
    def test_thirds_still_sum_to_one_hundred(self):
        result = stats.largest_remainder_percentages({"pos": 1, "neu": 1, "neg": 1})
        assert sum(result.values()) == 100

    @pytest.mark.parametrize(
        "counts",
        [
            {"pos": 1, "neu": 1, "neg": 1},
            {"pos": 7, "neu": 11, "neg": 13},
            {"pos": 1, "neu": 0, "neg": 2},
            {"pos": 33, "neu": 33, "neg": 34},
            {"pos": 1, "neu": 1, "neg": 0},
            {"pos": 5, "neu": 0, "neg": 0},
            {"pos": 22, "neu": 30, "neg": 8},
        ],
    )
    def test_always_sums_to_one_hundred(self, counts):
        assert sum(stats.largest_remainder_percentages(counts).values()) == 100

    def test_all_zero_stays_zero(self):
        assert stats.largest_remainder_percentages({"pos": 0, "neu": 0, "neg": 0}) == {
            "pos": 0, "neu": 0, "neg": 0
        }

    def test_negative_counts_are_treated_as_zero(self):
        result = stats.largest_remainder_percentages({"pos": -5, "neu": 1, "neg": 1})
        assert result["pos"] == 0 and sum(result.values()) == 100


class TestSentimentBreakdown:
    def test_counts_only_valid_labels(self):
        entries = [
            {"i": 1, "s": "pos"}, {"i": 2, "s": "neg"}, {"i": 3, "s": "neu"},
            {"i": 99, "s": "pos"},        # out of range
            {"i": "4", "s": "pos"},       # wrong type
            {"i": 4, "s": "mixed"},       # unknown label
            {"i": 1, "s": "neg"},         # duplicate index
            "not a dict",
        ]
        result = stats.sentiment_breakdown(entries, n_items=5)
        assert result["counts"] == {"pos": 1, "neu": 1, "neg": 1}
        assert result["classified"] == 3
        assert result["unclassified"] == 2
        assert sum(result["percentages"].values()) == 100

    def test_no_classifications_yields_zeroes(self):
        result = stats.sentiment_breakdown([], n_items=10)
        assert result["classified"] == 0
        assert result["percentages"] == {"pos": 0, "neu": 0, "neg": 0}

    def test_nothing_classified_is_flagged_so_a_renderer_can_say_so(self):
        # All-zero percentages and "0% of everything" look identical in a chart, and
        # only one of them is honest.
        assert stats.sentiment_breakdown([], n_items=10)["none_classified"] is True
        assert stats.sentiment_breakdown(
            [{"i": 1, "s": "pos"}], n_items=10
        )["none_classified"] is False

    def test_percentages_ignore_unclassified_items(self):
        result = stats.sentiment_breakdown([{"i": 1, "s": "pos"}], n_items=100)
        assert result["percentages"]["pos"] == 100

    def test_sentiment_by_index_skips_junk(self):
        lookup = stats.sentiment_by_index(
            [{"i": 1, "s": "pos"}, {"i": 2, "s": "??"}, {"i": 50, "s": "neg"}, None], 3
        )
        assert lookup == {1: "pos"}


class TestTopicVolumes:
    def test_counts_uniques_and_sorts_by_size(self):
        topics = [
            {"id": 1, "label": "Small", "item_indices": [1]},
            {"id": 2, "label": "Big", "item_indices": [1, 2, 3, 3, 99, "x", None]},
        ]
        volumes = stats.topic_volumes(topics, n_items=4)
        assert [volume["label"] for volume in volumes] == ["Big", "Small"]
        assert volumes[0]["count"] == 3
        assert volumes[0]["share"] == 75.0
        assert volumes[0]["item_indices"] == [1, 2, 3]

    def test_zero_items_does_not_divide_by_zero(self):
        volumes = stats.topic_volumes([{"id": 1, "label": "x", "item_indices": []}], 0)
        assert volumes[0]["share"] == 0.0

    def test_ignores_malformed_topics(self):
        assert stats.topic_volumes(["nope", None], 5) == []


class TestTrendBuckets:
    def test_spans_the_actual_coverage_including_empty_days(self):
        now = datetime.now(timezone.utc)
        posts = [
            _post(int(now.timestamp())),
            _post(int(now.timestamp())),
            _post(int((now - timedelta(days=2)).timestamp())),
        ]
        buckets = stats.trend_buckets(posts)
        assert len(buckets) == 3  # oldest to newest, the empty middle day zero-filled
        assert buckets[0][1] == 1
        assert buckets[1][1] == 0
        assert buckets[-1][1] == 2

    def test_dates_are_ordered_oldest_first(self):
        now = datetime.now(timezone.utc)
        posts = [_post(int((now - timedelta(days=offset)).timestamp())) for offset in range(5)]
        dates = [date for date, _ in stats.trend_buckets(posts)]
        assert dates == sorted(dates) and len(dates) == 5

    def test_a_single_day_of_posts_is_one_bucket(self):
        buckets = stats.trend_buckets([_post(1700000000), _post(1700000000)])
        assert buckets == [("2023-11-14", 2)]

    def test_a_span_wider_than_the_cap_keeps_the_newest_days(self):
        now = datetime.now(timezone.utc)
        posts = [_post(int(now.timestamp())), _post(int((now - timedelta(days=40)).timestamp()))]
        buckets = stats.trend_buckets(posts, max_days=30)
        # A 30-day window reaches back across 31 calendar dates: today and the 30
        # days before it. Capping at 30 dropped the oldest day of every full window.
        assert len(buckets) == 31
        assert buckets[-1][1] == 1  # the newest post survives
        assert sum(count for _, count in buckets) == 1  # the 40-day-old one falls outside

    def test_a_full_window_keeps_its_oldest_day(self):
        now = datetime.now(timezone.utc)
        posts = [
            _post(int((now - timedelta(days=offset)).timestamp())) for offset in (0, 7)
        ]
        buckets = stats.trend_buckets(posts, max_days=7)
        assert len(buckets) == 8
        assert buckets[0][1] == 1 and buckets[-1][1] == 1

    def test_no_posts_means_no_trend(self):
        assert stats.trend_buckets([]) == []

    def test_malformed_dates_are_ignored(self):
        assert stats.trend_buckets([{"date": "not-a-date"}, {"date": ""}]) == []


class TestEngagementAndDates:
    def test_totals_survive_bad_values(self):
        posts = [_post(1700000000, score=10, comments=2), {"score": None, "comments": "x"}]
        totals = stats.engagement_totals(posts)
        assert totals["score"] == 10 and totals["comments"] == 2
        assert totals["analysed"] == {"upvotes": 0, "comments": 0}

    def test_the_analysed_sample_is_counted_separately_from_the_pool(self):
        posts = [_post(1700000000, score=10, comments=2), _post(1700000001, score=5, comments=1)]
        items = [
            posts[0],
            {"kind": "comment", "score": 4},
            {"kind": "comment", "score": 3},
        ]
        totals = stats.engagement_totals(posts, items)
        # The pool is both posts; the sample is one post and two comments, whose
        # upvotes count and whose (absent) reply counts do not.
        assert totals["score"] == 15 and totals["comments"] == 3
        assert totals["analysed"] == {"upvotes": 17, "comments": 2}

    def test_no_analysed_sample_still_reports_zeroes_not_a_missing_key(self):
        assert stats.engagement_totals([])["analysed"] == {"upvotes": 0, "comments": 0}

    def test_date_range_reports_actual_coverage(self):
        posts = [_post(1700000000), _post(1700200000)]
        result = stats.actual_date_range(posts)
        assert result["oldest"] == "2023-11-14"
        assert result["newest"] == "2023-11-17"
        assert result["newest_ts"] == 1700200000
        assert "2023" in result["label"]

    def test_short_label_drops_the_year_to_fit_a_metric_tile(self):
        result = stats.actual_date_range([_post(1700000000), _post(1700200000)])
        assert "2023" not in result["short_label"]
        assert len(result["short_label"]) < len(result["label"])

    def test_short_label_always_exists_even_with_no_posts(self):
        assert stats.actual_date_range([])["short_label"]

    def test_empty_posts_do_not_crash(self):
        result = stats.actual_date_range([])
        assert result["newest_ts"] == 0 and result["label"]

    def test_lag_hours_is_never_negative(self):
        future = int(datetime.now(timezone.utc).timestamp()) + 10000
        assert stats.data_lag_hours(future) == 0.0
        assert stats.data_lag_hours(0) == 0.0


class TestCitations:
    def _items(self):
        return [
            {"kind": "post", "title": "First post", "body": "", "url": "https://reddit.com/1",
             "date": "2026-07-20", "score": 4, "subreddit": "x"},
            {"kind": "comment", "title": "", "body": "a" * 300, "url": "https://reddit.com/2",
             "date": "2026-07-21", "score": 9, "subreddit": "x"},
            {"kind": "post", "title": "No link", "body": "", "url": "", "date": "", "score": 0},
        ]

    def test_resolves_from_our_data_not_model_text(self):
        citations = stats.citations_for([1], self._items())
        assert citations[0]["text"] == "First post"
        assert citations[0]["url"] == "https://reddit.com/1"
        assert citations[0]["type"] == "post"

    def test_comments_are_excerpted(self):
        citation = stats.citations_for([2], self._items())[0]
        assert citation["text"].endswith("…")
        assert len(citation["text"]) <= stats.EXCERPT_LENGTH + 1

    def test_drops_out_of_range_wrong_type_and_unlinkable(self):
        assert stats.citations_for([0, 4, "1", None, True, 3], self._items()) == []

    def test_dedupes_and_caps_at_three(self):
        items = [
            {"kind": "post", "title": "p{}".format(i), "url": "https://reddit.com/{}".format(i),
             "date": "", "score": 0, "body": ""}
            for i in range(1, 8)
        ]
        citations = stats.citations_for([1, 1, 2, 3, 4, 5], items)
        assert len(citations) == stats.MAX_CITATIONS_PER_CLAIM
        assert [c["index"] for c in citations] == [1, 2, 3]

    def test_no_indices_gives_no_citations(self):
        assert stats.citations_for([], self._items()) == []
        assert stats.citations_for(None, self._items()) == []


class TestCitationQuality:
    """Two rules decide which candidates survive: one source per thread, and an
    item with nothing to quote only when there is nothing better."""

    @staticmethod
    def _post(index, thread, body="A real body with something to say in it.", comments=4):
        return {
            "kind": "post", "id": "p{}".format(index), "title": "Post {}".format(index),
            "body": body, "comments": comments, "score": 10, "subreddit": "x",
            "date": "2026-07-20",
            "url": "https://reddit.com/r/x/comments/{}/slug/".format(thread),
        }

    @staticmethod
    def _comment(index, thread, body="A reply worth reading.", score=5):
        return {
            "kind": "comment", "id": "c{}".format(index), "title": "", "body": body,
            "score": score, "subreddit": "x", "date": "2026-07-20",
            "url": "https://reddit.com/r/x/comments/{}/slug/c{}/".format(thread, index),
        }

    def test_a_post_and_its_own_comments_count_as_one_source(self):
        items = [self._post(1, "t1"), self._comment(2, "t1"), self._post(3, "t2")]
        citations = stats.citations_for([1, 2, 3], items)
        assert [citation["index"] for citation in citations] == [1, 3]

    def test_two_comments_from_the_same_thread_collapse_to_the_first(self):
        items = [self._comment(1, "t1"), self._comment(2, "t1")]
        assert len(stats.citations_for([1, 2], items)) == 1

    def test_thread_key_reads_the_permalink_and_falls_back_to_the_id(self):
        assert stats.thread_key(self._post(1, "abc123")) == "abc123"
        assert stats.thread_key({"id": "Xyz", "url": "https://example.test/x"}) == "xyz"
        assert stats.thread_key({}) == ""

    def test_a_thin_item_yields_to_a_better_candidate(self):
        thin = {
            "kind": "post", "id": "p1", "title": "Link drop", "body": "", "comments": 0,
            "score": 900, "subreddit": "x", "date": "",
            "url": "https://reddit.com/r/x/comments/t1/slug/",
        }
        items = [thin, self._post(2, "t2"), self._post(3, "t3")]
        citations = stats.citations_for([1, 2, 3], items, limit=2)
        assert [citation["index"] for citation in citations] == [2, 3]

    def test_a_thin_item_is_still_cited_when_it_is_all_there_is(self):
        thin = {
            "kind": "post", "id": "p1", "title": "Link drop", "body": "", "comments": 0,
            "score": 1, "subreddit": "x", "date": "",
            "url": "https://reddit.com/r/x/comments/t1/slug/",
        }
        assert [c["index"] for c in stats.citations_for([1], [thin])] == [1]


class TestCitationExcerpts:
    """The excerpt is the evidence a draft is written from, so it carries the real
    text rather than a label for a link."""

    POST = {
        "kind": "post", "id": "p1", "title": "How do you price a retainer",
        "body": "We quoted 20 hours and delivered 65 last quarter.",
        "comments": 3, "score": 10, "subreddit": "x", "date": "2026-07-20",
        "url": "https://reddit.com/r/x/comments/t1/slug/",
    }

    def test_a_post_excerpt_carries_its_title_and_body(self):
        excerpt = stats.citations_for([1], [self.POST])[0]["excerpt"]
        assert "How do you price a retainer" in excerpt
        assert "delivered 65" in excerpt

    def test_replies_from_the_same_thread_are_folded_in(self):
        comments = [
            {"kind": "comment", "body": "Charge for discovery separately.", "score": 40,
             "url": "https://reddit.com/r/x/comments/t1/slug/c1/"},
            {"kind": "comment", "body": "Cap the revisions in writing.", "score": 20,
             "url": "https://reddit.com/r/x/comments/t1/slug/c2/"},
            {"kind": "comment", "body": "Ignored, lowest score.", "score": 1,
             "url": "https://reddit.com/r/x/comments/t1/slug/c3/"},
            {"kind": "comment", "body": "From a different thread entirely.", "score": 99,
             "url": "https://reddit.com/r/x/comments/t2/slug/c4/"},
        ]
        excerpt = stats.citations_for([1], [self.POST], comments=comments)[0]["excerpt"]
        assert "Charge for discovery separately." in excerpt
        assert "Cap the revisions in writing." in excerpt
        assert "Ignored, lowest score." not in excerpt
        assert "different thread" not in excerpt

    def test_a_comment_excerpt_is_its_own_body_and_nothing_else(self):
        comment = {
            "kind": "comment", "id": "c1", "title": "", "body": "Just my two cents.",
            "score": 3, "subreddit": "x", "date": "",
            "url": "https://reddit.com/r/x/comments/t1/slug/c1/",
        }
        assert stats.citations_for([1], [comment])[0]["excerpt"] == "Just my two cents."

    def test_an_excerpt_falls_back_to_the_title_when_there_is_no_body(self):
        post = dict(self.POST, body="")
        assert stats.citations_for([1], [post])[0]["excerpt"] == self.POST["title"]

    def test_an_excerpt_is_capped_and_whitespace_normalised(self):
        post = dict(self.POST, body="word \n\n  spaced " + "x" * 2000)
        excerpt = stats.citations_for([1], [post])[0]["excerpt"]
        assert len(excerpt) <= stats.EXCERPT_MAX_CHARS
        assert "word spaced" in excerpt
        assert "\n" not in excerpt

    def test_the_text_field_is_unchanged_by_the_excerpt(self):
        citation = stats.citations_for([1], [self.POST])[0]
        assert citation["text"] == self.POST["title"]
        assert citation["excerpt"] != citation["text"]
