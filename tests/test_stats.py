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
        assert len(buckets) == 30
        assert buckets[-1][1] == 1  # the newest post survives
        assert sum(count for _, count in buckets) == 1  # the 40-day-old one falls outside

    def test_no_posts_means_no_trend(self):
        assert stats.trend_buckets([]) == []

    def test_malformed_dates_are_ignored(self):
        assert stats.trend_buckets([{"date": "not-a-date"}, {"date": ""}]) == []


class TestEngagementAndDates:
    def test_totals_survive_bad_values(self):
        posts = [_post(1700000000, score=10, comments=2), {"score": None, "comments": "x"}]
        assert stats.engagement_totals(posts) == {"score": 10, "comments": 2}

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
