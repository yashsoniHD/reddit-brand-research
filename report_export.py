"""Takeaway artefacts: a Markdown report and two CSVs, built from a report bundle.

These are what a user actually sends to a client, so the Markdown carries the same
evidence links and methodology notes as the on-screen report. A report without its
caveats is worse than no report.
"""

from __future__ import annotations

import csv
import io
import re
from typing import List, Sequence

import stats

SENTIMENT_ORDER = ("pos", "neu", "neg")
SENTIMENT_TITLES = {"pos": "Positive", "neu": "Neutral", "neg": "Negative"}
PLATFORM_TITLES = {"linkedin": "LinkedIn", "instagram": "Instagram"}
_FILENAME_SAFE = re.compile(r"[^a-z0-9]+")
# Long enough to recognise the run, short enough to survive email clients and
# filesystems that truncate.
MAX_FILENAME_QUERY_CHARS = 40


def filename_stem(bundle: dict) -> str:
    """`content-ideas-ai-automation-2026-07-26`, safe on every filesystem."""
    query = str(bundle.get("inputs", {}).get("query") or "report").lower()
    slug = _FILENAME_SAFE.sub("-", query[:MAX_FILENAME_QUERY_CHARS]).strip("-")
    date = str(bundle.get("generated_at", ""))[:10] or "report"
    return "content-ideas-{}-{}".format(slug or "report", date)


def _escape(text) -> str:
    """Neutralise pipes so a title cannot break out of a Markdown table cell."""
    return str(text or "").replace("|", "\\|").replace("\n", " ").strip()


def _citation_links(citations: Sequence[dict]) -> str:
    parts = []
    for citation in citations or []:
        label = _escape(citation.get("text", ""))[:90]
        url = citation.get("url", "")
        date = citation.get("date", "")
        if url:
            parts.append("[{}]({}) ({})".format(label, url, date))
    return "; ".join(parts)


def report_markdown(bundle: dict) -> str:
    inputs = bundle.get("inputs", {})
    sample = bundle.get("sample", {})
    analysis = bundle.get("analysis", {})
    computed = bundle.get("stats", {})
    sentiment = computed.get("sentiment", {})
    percentages = sentiment.get("percentages", {})
    counts = sentiment.get("counts", {})
    date_range = computed.get("date_range", {})
    engagement = computed.get("engagement", {})
    subs = ", ".join("r/{}".format(name) for name in inputs.get("subreddits", []))
    platform = inputs.get("platform_label", "")
    low_data = sample.get("low_data")

    out: List[str] = []
    add = out.append

    add("# Content ideas: {}".format(_escape(inputs.get("query", "Reddit research"))))
    add("")
    add(
        "**Platform:** {} · **Communities:** {} · **Window:** last {} days · "
        "**Actual range:** {}".format(
            platform, subs, inputs.get("days", ""), date_range.get("label", "n/a")
        )
    )
    add("**Generated:** {} · **Analysis:** {}".format(
        bundle.get("generated_at", ""), bundle.get("model_label", "")
    ))
    if bundle.get("is_sample"):
        add("")
        add("> This is a bundled sample report, generated from real Reddit data on the date above.")
    add("")

    for notice in bundle.get("notices", []):
        add("> Note: {}".format(notice))
    if bundle.get("notices"):
        add("")

    if low_data:
        add(
            "> Only {} items were found in this window, so these results are "
            "directional rather than conclusive. A 30-day window will give a firmer read.".format(
                sample.get("items_analysed", 0)
            )
        )
        add("")

    add("## Executive summary")
    add("")
    add(analysis.get("executive_summary") or "No summary was produced for this run.")
    add("")

    add("## Sample")
    add("")
    add("| Measure | Value |")
    add("| --- | --- |")
    add("| Posts analysed | {} |".format(_sample_line(sample, "posts")))
    add("| Comments analysed | {} |".format(_sample_line(sample, "comments")))
    add("| Actual date range | {} |".format(date_range.get("label", "n/a")))
    add(
        "| Total engagement | {} upvotes · {} comments |".format(
            _thousands(engagement.get("score", 0)), _thousands(engagement.get("comments", 0))
        )
    )
    add("")

    add("## Communities analysed")
    add("")
    communities = bundle.get("discovery", {}).get("communities", [])
    if not communities:
        add("No communities were recorded for this run.")
        add("")
    for community in communities:
        parts = []
        if community.get("reason"):
            parts.append(_escape(community["reason"]))
        subscribers = community.get("subscribers") or 0
        if subscribers:
            parts.append("{} members".format(_thousands(subscribers)))
        parts.append(
            "contributed data to this report" if community.get("used")
            else "no usable posts in this window"
        )
        add("- **r/{}**: {}".format(community.get("name", ""), " · ".join(parts)))
    if communities:
        add("")

    add("## Trending topics")
    add("")
    topics = analysis.get("topics", [])
    if not topics:
        add("No recurring topics were identified in this sample.")
        add("")
    for topic in topics:
        share = "" if low_data else ", {}% of the conversation".format(_round_share(topic.get("share", 0)))
        add("### {}{}".format(topic.get("label", "Topic"), share))
        add("")
        add("{} items{}".format(topic.get("count", 0), "" if low_data else " in this sample"))
        add("")
        if topic.get("finding"):
            add(topic["finding"])
            add("")
        for citation in topic.get("citations", []):
            add("- [{}]({}) · {} · {} upvotes".format(
                _escape(citation.get("text", "")), citation.get("url", ""),
                citation.get("date", ""), citation.get("score", 0),
            ))
        add("")

    add("## Sentiment")
    add("")
    if low_data:
        add("Counts across {} classified items: {} positive, {} neutral, {} negative.".format(
            sentiment.get("classified", 0), counts.get("pos", 0), counts.get("neu", 0), counts.get("neg", 0)
        ))
    else:
        add(" · ".join(
            "**{}** {}% ({} items)".format(SENTIMENT_TITLES[key], percentages.get(key, 0), counts.get(key, 0))
            for key in SENTIMENT_ORDER
        ))
    add("")
    add(
        "Classified per item by the model across {} posts and {} comments; "
        "percentages are computed from those classifications, not estimated.".format(
            sample.get("posts_analysed", 0), sample.get("comments_analysed", 0)
        )
    )
    add("")

    for heading, key in (("Pain points", "pain_points"), ("Questions the community is asking", "questions")):
        add("## {}".format(heading))
        add("")
        entries = analysis.get(key, [])
        if not entries:
            add("None identified in this sample.")
            add("")
        for entry in entries:
            add("- **{}**".format(entry.get("text", "")))
            links = _citation_links(entry.get("citations", []))
            if links:
                add("  - Evidence: {}".format(links))
        add("")

    add("## Content ideas")
    add("")
    for index, idea in enumerate(analysis.get("content_ideas", []), 1):
        add("### {}. {}".format(index, idea.get("title", "")))
        add("")
        if idea.get("hook"):
            # The hook is the line they actually publish, so it is quoted verbatim
            # rather than paraphrased into a bullet.
            add("> **“{}”**".format(_escape(idea["hook"])))
            add("")
        add("**Platform:** {} · **Format:** {}".format(
            PLATFORM_TITLES.get(idea.get("platform"), idea.get("platform", "")),
            idea.get("format", ""),
        ))
        if idea.get("angle"):
            add("")
            add("**Angle:** {}".format(idea["angle"]))
        if idea.get("addresses"):
            add("")
            add("**Addresses:** {}".format(idea["addresses"]))
        if idea.get("rationale"):
            add("")
            add("**Why it lands:** {}".format(idea["rationale"]))
        links = _citation_links(idea.get("citations", []))
        if links:
            add("")
            add("**Evidence:** {}".format(links))
        add("")

    add("## Activity")
    add("")
    add("| Date | Posts |")
    add("| --- | --- |")
    for date, count in computed.get("trend", []):
        add("| {} | {} |".format(date, count))
    add("")

    add("## Methodology")
    add("")
    # Named differently from the "Communities analysed" section above on purpose:
    # that section lists everything discovery picked, this lists what produced data.
    add("- Communities read: {}".format(subs or "none"))
    add("- Discovery: the communities were chosen by the model from your query, then "
        "checked to exist and be public before being read.")
    add("- Requested window: last {} days".format(inputs.get("days", "")))
    add("- Actual coverage: {}".format(date_range.get("label", "n/a")))
    add("- Posts fetched: {} · analysed: {}".format(
        sample.get("posts_fetched", 0), sample.get("posts_analysed", 0)))
    add("- Comments fetched: {} · analysed: {}".format(
        sample.get("comments_fetched", 0), sample.get("comments_analysed", 0)))
    if sample.get("posts_truncated") or sample.get("comments_truncated"):
        add("- Truncation: the {} most recent posts and the top {} comments by score were analysed.".format(
            sample.get("posts_analysed", 0), sample.get("comments_analysed", 0)))
    if sample.get("capacity_limited"):
        # Same disclosure the on-screen report carries: the cap came from the
        # analysis tier in use, not from a shortage of community activity.
        add("- Sample size was limited by the analysis capacity tier in use, not by the "
            "amount of available discussion.")
    add("- Excluded: posts and comments removed or deleted by their authors or moderators.")
    add("- Newest item in sample: {} (roughly {} hours behind live Reddit at generation time).".format(
        date_range.get("newest_iso", "n/a"), computed.get("lag_hours", 0)))
    add("- Topic volumes and sentiment percentages are computed from per-item model "
        "classifications, not estimated by the model.")
    add("- Every citation link resolves to the archived source item, not to model output.")
    add("- Model: {}".format(bundle.get("model_label", "")))
    add("- Source: public Reddit data via the {}.".format(bundle.get("source", "archive")))
    add("- Generated: {}".format(bundle.get("generated_at", "")))
    add("")

    return "\n".join(out)


def _sample_line(sample: dict, kind: str) -> str:
    analysed = sample.get("{}_analysed".format(kind), 0)
    fetched = sample.get("{}_fetched".format(kind), 0)
    if fetched > analysed:
        return "{} of {} fetched".format(analysed, fetched)
    return str(analysed)


def _round_share(value) -> str:
    try:
        return "{:g}".format(round(float(value), 1))
    except (TypeError, ValueError):
        return "0"


def _thousands(value) -> str:
    try:
        return "{:,}".format(int(value))
    except (TypeError, ValueError):
        return "0"


def _csv(header: Sequence[str], rows: Sequence[Sequence]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue()


def posts_csv(bundle: dict) -> str:
    sentiments = stats.sentiment_by_index(
        bundle.get("analysis", {}).get("item_sentiments", []), len(bundle.get("items", []))
    )
    index_by_id = {}
    for position, item in enumerate(bundle.get("items", []), 1):
        if item.get("kind") != "comment":
            index_by_id[item.get("id")] = position

    rows = []
    for post in bundle.get("posts", []):
        index = index_by_id.get(post.get("id"))
        rows.append([
            post.get("subreddit", ""),
            post.get("title", ""),
            post.get("date", ""),
            post.get("score", 0),
            post.get("comments", 0),
            SENTIMENT_TITLES.get(sentiments.get(index), "Not analysed"),
            post.get("url", ""),
        ])
    return _csv(
        ["subreddit", "title", "date", "upvotes", "comments", "sentiment", "url"], rows
    )


def comments_csv(bundle: dict) -> str:
    rows = []
    for comment in bundle.get("comments", []):
        rows.append([
            comment.get("subreddit", ""),
            " ".join((comment.get("body") or "").split()),
            comment.get("date", ""),
            comment.get("score", 0),
            comment.get("url", ""),
        ])
    return _csv(["subreddit", "comment", "date", "upvotes", "url"], rows)


def ideas_csv(bundle: dict) -> str:
    rows = []
    for idea in bundle.get("analysis", {}).get("content_ideas", []):
        citations = idea.get("citations", [])
        rows.append([
            idea.get("title", ""),
            idea.get("hook", ""),
            idea.get("platform", ""),
            idea.get("format", ""),
            idea.get("angle", ""),
            idea.get("addresses", ""),
            idea.get("rationale", ""),
            " ".join(citation.get("url", "") for citation in citations),
        ])
    return _csv(
        ["title", "hook", "platform", "format", "angle", "addresses", "rationale", "evidence_urls"],
        rows,
    )
