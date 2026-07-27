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

import reddit_data
import stats

SENTIMENT_ORDER = ("pos", "neu", "neg")
SENTIMENT_TITLES = {"pos": "Positive", "neu": "Neutral", "neg": "Negative"}
_FILENAME_SAFE = re.compile(r"[^a-z0-9]+")
# What a finished stem has to look like, checked once at the end of
# filename_stem as a second, independent guarantee alongside the per-piece
# scrubbing above it: this is the value a Content-Disposition header trusts
# outright, and a header that reflects an attacker-chosen newline or quote can
# inject a second header or field.
_FILENAME_STEM_SAFE = re.compile(r"^[a-z0-9-]+$")
# Long enough to recognise the run, short enough to survive email clients and
# filesystems that truncate.
MAX_FILENAME_QUERY_CHARS = 40


def filename_stem(bundle: dict, kind: str = "") -> str:
    """`content-ideas-ai-automation-2026-07-26-ideas`, safe on every filesystem
    and header.

    `kind` names which artefact this is ("ideas", "posts", "report") and is
    appended to the stem. Two exports of the same report can share an
    extension, so without it the ideas CSV and the posts CSV download under one
    identical name. It is scrubbed like every other piece, and the finished stem
    is checked against _FILENAME_STEM_SAFE either way, so a caller cannot widen
    the character set the Content-Disposition header ends up trusting.
    """
    query = str(bundle.get("inputs", {}).get("query") or "report").lower()
    slug = _FILENAME_SAFE.sub("-", query[:MAX_FILENAME_QUERY_CHARS]).strip("-")
    raw_date = str(bundle.get("generated_at", ""))[:10] or "report"
    date = _FILENAME_SAFE.sub("-", raw_date.lower()).strip("-") or "report"
    suffix = _FILENAME_SAFE.sub("-", str(kind or "").lower()).strip("-")
    stem = "content-ideas-{}-{}".format(slug or "report", date)
    if suffix:
        stem = "{}-{}".format(stem, suffix)
    if _FILENAME_STEM_SAFE.match(stem):
        return stem
    return "report-{}".format(suffix) if suffix else "report"


# Markdown control characters that let inline text break out of its context:
# `|` breaks a table cell, `\n` forges a new block, and `< > [ ] (` can open a
# link/image/HTML tag. Escaped wherever LLM- or Reddit-sourced text lands.
_MARKDOWN_SPECIAL_CHARS = (("<", "\\<"), (">", "\\>"), ("[", "\\["), ("]", "\\]"), ("(", "\\("))
_BARE_URL = re.compile(r"https?://[^\s)>\]]+")


def _defang_urls(text: str) -> str:
    """Wrap bare URLs in backticks so a Markdown renderer treats them as
    literal text instead of auto-linking them. Headings and claims here are
    LLM- or Reddit-sourced, and an auto-linked URL there is one more surface
    for a spoofed link to look like a real citation."""
    return _BARE_URL.sub(lambda match: "`{}`".format(match.group(0)), text)


def _escape(text) -> str:
    """Neutralise Markdown control characters and defang bare URLs, so text
    that came from an LLM or from Reddit cannot break out of a table cell,
    forge a heading or link, or get auto-linked as if it were a citation."""
    cleaned = str(text or "").replace("\n", " ").strip()
    cleaned = _defang_urls(cleaned)
    cleaned = cleaned.replace("|", "\\|")
    for char, escaped in _MARKDOWN_SPECIAL_CHARS:
        cleaned = cleaned.replace(char, escaped)
    return cleaned


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
    low_data = sample.get("low_data")

    out: List[str] = []
    add = out.append

    add("# Content ideas: {}".format(_escape(inputs.get("query", "Reddit research"))))
    add("")
    add(
        "**Communities:** {} · **Window:** last {} days · "
        "**Actual range:** {}".format(
            subs,
            inputs.get("days", reddit_data.FETCH_WINDOW_DAYS),
            date_range.get("label", "n/a"),
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
            "directional rather than conclusive.".format(
                sample.get("items_analysed", 0)
            )
        )
        add("")

    add("## Executive summary")
    add("")
    add(_escape(analysis.get("executive_summary")) or "No summary was produced for this run.")
    add("")

    add("## Sample")
    add("")
    add("| Measure | Value |")
    add("| --- | --- |")
    add("| Posts analysed | {} |".format(_sample_line(sample, "posts")))
    add("| Comments analysed | {} |".format(_sample_line(sample, "comments")))
    add("| Actual date range | {} |".format(date_range.get("label", "n/a")))
    analysed_engagement = engagement.get("analysed")
    if isinstance(analysed_engagement, dict):
        # stats.engagement.analysed (once present) covers only the items the
        # model actually analysed; the headline figure should match what the
        # rest of the report is about, with the larger fetched-pool total
        # kept as a clearly-labeled secondary figure rather than dropped.
        add(
            "| Total engagement (analysed) | {} upvotes · {} comments |".format(
                _thousands(analysed_engagement.get("upvotes", 0)),
                _thousands(analysed_engagement.get("comments", 0)),
            )
        )
        add(
            "| Total engagement (fetched pool) | {} upvotes · {} comments |".format(
                _thousands(engagement.get("score", 0)), _thousands(engagement.get("comments", 0))
            )
        )
    else:
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
        add("### {}{}".format(_escape(topic.get("label", "Topic")), share))
        add("")
        add("{} items{}".format(topic.get("count", 0), "" if low_data else " in this sample"))
        add("")
        if topic.get("finding"):
            add(_escape(topic["finding"]))
            add("")
        for citation in topic.get("citations", []):
            add("- [{}]({}) · {} · {} upvotes".format(
                _escape(citation.get("text", "")), citation.get("url", ""),
                citation.get("date", ""), citation.get("score", 0),
            ))
        add("")

    add("## Sentiment")
    add("")
    if sentiment.get("classified", 0) == 0:
        add("No items could be classified for tone in this run.")
    elif low_data:
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
            add("- **{}**".format(_escape(entry.get("text", ""))))
            links = _citation_links(entry.get("citations", []))
            if links:
                add("  - Evidence: {}".format(links))
        add("")

    add("## Content ideas")
    add("")
    for index, idea in enumerate(analysis.get("content_ideas", []), 1):
        add("### {}. {}".format(index, _escape(idea.get("title", ""))))
        add("")
        if idea.get("hook"):
            # The hook is the line they actually publish, so it is quoted verbatim
            # rather than paraphrased into a bullet.
            add("> **“{}”**".format(_escape(idea["hook"])))
            add("")
        add("**Format:** {}".format(_escape(idea.get("format", ""))))
        if idea.get("angle"):
            add("")
            add("**Angle:** {}".format(_escape(idea["angle"])))
        if idea.get("addresses"):
            add("")
            add("**Addresses:** {}".format(_escape(idea["addresses"])))
        if idea.get("rationale"):
            add("")
            add("**Why it lands:** {}".format(_escape(idea["rationale"])))
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
    if inputs.get("discovery") == "manual":
        add("- Discovery: you chose these communities directly; they were checked to "
            "exist and be public before being read.")
    else:
        add("- Discovery: the communities were chosen by the model from your query, then "
            "checked to exist and be public before being read.")
    add("- Requested window: last {} days".format(
        inputs.get("days", reddit_data.FETCH_WINDOW_DAYS)))
    add("- Actual coverage: {}".format(date_range.get("label", "n/a")))
    add("- Posts fetched: {} · analysed: {}".format(
        sample.get("posts_fetched", 0), sample.get("posts_analysed", 0)))
    add("- Comments fetched: {} · analysed: {}".format(
        sample.get("comments_fetched", 0), sample.get("comments_analysed", 0)))
    selection = sample.get("selection", {})
    if selection.get("method") == "relevance+engagement":
        add("- Selection: the {} highest-scoring candidates were rated for relevance to "
            "your search by the model, then ranked in code by relevance, community "
            "engagement and recency, with no single community taking more than half "
            "the sample while others had items to offer.".format(selection.get("triaged", 0)))
    elif selection.get("method") == "relevance-off":
        add("- Selection: the relevance filter was turned off for this run, so items "
            "were ranked in code by community engagement and recency, with no single "
            "community taking more than half the sample while others had items to offer.")
    else:
        add("- Selection: items were ranked in code by community engagement and "
            "recency, with no single community taking more than half the sample "
            "while others had items to offer.")
    if sample.get("posts_truncated") or sample.get("comments_truncated"):
        add("- Truncation: the {} top-ranked posts and the {} top-ranked comments were analysed.".format(
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


# CSV formula injection: a cell starting with any of these opens a formula in
# Excel/Sheets/LibreOffice when the file is opened, and Reddit/LLM text is not
# trusted to avoid them. Prefixing with a single quote forces the cell back to
# literal text; this is the standard mitigation (OWASP's CSV Injection guidance
# names this exact character set).
_CSV_FORMULA_PREFIX_CHARS = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe_cell(value):
    text = "" if value is None else str(value)
    if text and text[0] in _CSV_FORMULA_PREFIX_CHARS:
        return "'" + text
    return text


def _csv(header: Sequence[str], rows: Sequence[Sequence]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
    writer.writerow([_csv_safe_cell(cell) for cell in header])
    for row in rows:
        writer.writerow([_csv_safe_cell(cell) for cell in row])
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
            idea.get("format", ""),
            idea.get("angle", ""),
            idea.get("addresses", ""),
            idea.get("rationale", ""),
            " ".join(citation.get("url", "") for citation in citations),
        ])
    return _csv(
        ["title", "hook", "format", "angle", "addresses", "rationale", "evidence_urls"],
        rows,
    )
