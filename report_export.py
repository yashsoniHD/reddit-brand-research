"""Takeaway artefacts: a Markdown report and two CSVs, built from a report bundle.

These are what a user actually sends to a client, so the Markdown carries the same
evidence links and methodology notes as the on-screen report. A report without its
caveats is worse than no report.
"""

from __future__ import annotations

import csv
import io
import re
from typing import Dict, List, Sequence

import reddit_data
import stats

# Imported by name, not as `import analysis`: `report_markdown` binds a local called
# `analysis` (the bundle's analysis block), which would shadow the module inside the
# one function that needs these.
from analysis import channel_labels as _engine_channel_labels, join_labels

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
#
# Backslash is escaped FIRST and is load-bearing: every other entry below
# inserts a literal backslash in front of the character it escapes, so if
# backslash were escaped last, those just-inserted backslashes would get
# doubled instead of the backslash that was actually in the source text. A
# raw "\[x\]\(/evil\)" would then survive as a live `[x](/evil)` link once a
# renderer applies its own backslash-escape rule and halves the backslashes
# back down. Escaping backslash first means every backslash the renderer
# sees, original or inserted, is intentional and halves back to a literal
# character, never to a bare markdown control character.
_MARKDOWN_SPECIAL_CHARS = (
    ("\\", "\\\\"),
    ("|", "\\|"),
    ("<", "\\<"),
    (">", "\\>"),
    ("[", "\\["),
    ("]", "\\]"),
    ("(", "\\("),
)
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
    for char, escaped in _MARKDOWN_SPECIAL_CHARS:
        cleaned = cleaned.replace(char, escaped)
    return cleaned


_REDDIT_URL_PREFIXES = ("https://reddit.com/", "https://www.reddit.com/")
# A denylist of characters illegal in a URL, deliberately not an allowlist of
# permitted ones. Reddit permalinks carry raw Unicode, e.g.
# /comments/x/cuánto_cobrarían_por_este_proyecto/, and an ASCII allowlist
# tested against the 2587 real URLs in sample_report.json rejected 32 of
# them for exactly that reason. Whitespace, the markdown link delimiters,
# quotes, backtick, backslash and control characters are what could break out
# of `(...)` or smuggle a second link target; everything else, Unicode
# included, is left alone.
_UNSAFE_IN_LINK_TARGET = re.compile(r"""[\s()<>"'`\\]|[\x00-\x1f]""")


def _link(label, url) -> str:
    """`[label](url)` for a real Reddit permalink, escaped plain text otherwise."""
    text = _escape(label)
    target = str(url or "")
    if target.startswith(_REDDIT_URL_PREFIXES) and not _UNSAFE_IN_LINK_TARGET.search(target):
        return "[{}]({})".format(text, target)
    return text


def _citation_links(citations: Sequence[dict]) -> str:
    parts = []
    for citation in citations or []:
        label = str(citation.get("text", "") or "")[:90]
        url = citation.get("url", "")
        date = citation.get("date", "")
        if url:
            parts.append("{} ({})".format(_link(label, url), date))
    return "; ".join(parts)


def _window_label(inputs: dict) -> str:
    """The window claim this run can actually keep.

    A search takes the most recent matching threads however old they are, so
    printing "last 7 days" for one would be a false statement about coverage.
    `stats.date_range` still reports what the report really spans, and that is
    printed next to this in every place it appears.
    """
    if (inputs or {}).get("retrieval") == "search":
        return "not limited (search)"
    return "last {} days".format(
        (inputs or {}).get("days", reddit_data.FETCH_WINDOW_DAYS)
    )


def _channel_labels(inputs: dict) -> List[str]:
    """The display names of the channels a run was written for, or [].

    Read through `analysis.channel_labels` rather than from a map of its own, so
    the export cannot drift from the engine's own names for the same things. A
    bundle from before channels existed carries no key and comes back empty.
    """
    return _engine_channel_labels((inputs or {}).get("channels") or [])


def _retrieval_sentence(inputs: dict) -> str:
    """How the posts were found, in one line, for the methodology section."""
    inputs = inputs or {}
    mode = inputs.get("retrieval")
    if mode == "search":
        terms = inputs.get("terms") or []
        searched = " Searched for: {}.".format(", ".join(terms)) if terms else ""
        return (
            "each community was searched for the words your brief is about, and only "
            "the threads that matched were read.{}".format(searched)
        )
    if mode == "search+scrape":
        return (
            "searching those communities for specific words found too little, so they "
            "were read in full instead."
        )
    return "the communities were read in full for the window below."


def report_markdown(bundle: dict, drafts=None) -> str:
    # `drafts` is accepted and ignored for now, so that embedding saved drafts
    # in the export later is a one-line change at the call site rather than a
    # signature change. Not implemented in this pass.
    del drafts
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
        "**Communities:** {} · **Window:** {} · "
        "**Actual range:** {}".format(
            subs,
            _window_label(inputs),
            date_range.get("label", "n/a"),
        )
    )
    header_channels = _channel_labels(inputs)
    if header_channels:
        # Only when the run named channels: a channel-neutral report has nothing to
        # say here, and an "all of them" line would be noise on every other export.
        add("**Written for:** {}".format(", ".join(header_channels)))
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
            add("- {} · {} · {} upvotes".format(
                _link(citation.get("text", ""), citation.get("url", "")),
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
    add("- Retrieval: {}".format(_retrieval_sentence(inputs)))
    channels = _channel_labels(inputs)
    if channels:
        add("- Channels: the ideas were written for {}, so the formats are the ones "
            "{} ship.".format(
                join_labels(channels),
                "that channel can" if len(channels) == 1 else "those channels can",
            ))
    add("- Requested window: {}".format(_window_label(inputs)))
    add("- Actual coverage: {}".format(date_range.get("label", "n/a")))
    add("- Posts fetched: {} · analysed: {}".format(
        sample.get("posts_fetched", 0), sample.get("posts_analysed", 0)))
    add("- Comments fetched: {} · analysed: {}".format(
        sample.get("comments_fetched", 0), sample.get("comments_analysed", 0)))
    selection = sample.get("selection", {})
    requirements = selection.get("requirements") or []
    if selection.get("method") == "relevance+engagement":
        add("- Selection: the {} highest-scoring candidates were rated for relevance to "
            "your search by the model, then ranked in code by relevance, community "
            "engagement and recency, with no single community taking more than half "
            "the sample while others had items to offer.".format(selection.get("triaged", 0)))
    elif selection.get("method") == "relevance+qualifiers":
        add("- Selection: the {} highest-scoring candidates were rated for relevance to "
            "your search by the model and checked against the requirements below, then "
            "ranked in code by relevance, those requirements, community engagement and "
            "recency, with no single community taking more than half the sample while "
            "others had items to offer.".format(selection.get("triaged", 0)))
    elif selection.get("method") == "relevance-off":
        if requirements:
            add("- Selection: the relevance filter was turned off for this run, so items "
                "were ranked in code by community engagement and recency only. Your "
                "brief's requirements about who posted, listed below, were not applied.")
        else:
            add("- Selection: the relevance filter was turned off for this run, so items "
                "were ranked in code by community engagement and recency, with no single "
                "community taking more than half the sample while others had items to offer.")
    else:
        add("- Selection: items were ranked in code by community engagement and "
            "recency, with no single community taking more than half the sample "
            "while others had items to offer.")
    if requirements:
        add("- Your brief asked for more than a topic, so each rated candidate was also "
            "checked against {}:".format("it" if len(requirements) == 1 else "these requirements"))
        for requirement in requirements:
            add("  - {}".format(_requirement_line(requirement)))
        if selection.get("method") == "relevance+qualifiers":
            add("- Threads that showed a requirement were analysed first. Threads that "
                "contradicted one were pushed behind everything else rather than removed: "
                "the call comes from a model reading a short preview, and a wrong call "
                "would throw away a good thread.")
            qualifier_analysed = selection.get("qualifier_analysed")
            if isinstance(qualifier_analysed, dict):
                add("- {}".format(_qualifier_analysed_line(qualifier_analysed)))
            add("- Seniority and funding stage are whatever the author wrote about "
                "themselves, not anything this tool verified: this is self-reported, and "
                "the system is trusting a stranger's claim about their own company.")
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
    add("- Full list of every analysed post and its link: see Source threads below.")
    add("- Model: {}".format(bundle.get("model_label", "")))
    add("- Source: public Reddit data via the {}.".format(bundle.get("source", "archive")))
    add("- Generated: {}".format(bundle.get("generated_at", "")))
    add("")

    # Last on purpose: the module's stated principle is that a report without
    # its caveats is worse than no report, so the appendix goes after the
    # methodology block rather than burying it under 150-plus entries.
    out.extend(_source_threads_section(bundle))

    return "\n".join(out)


# Above the real MAX_POSTS_ANALYSED = 150 ceiling (analysis.py), so a normal
# run never gets truncated here. It exists as a defensive cap because
# POST /api/export runs this builder over a client-supplied bundle with no
# size limit of its own: without it, a hand-crafted bundle could ask for an
# arbitrarily long appendix.
MAX_SOURCE_THREADS = 200


def _item_sentiments(bundle: dict) -> Dict[int, str]:
    """Index -> sentiment label for the analysed sample, shared by the
    appendix and posts_csv so the join lives in one place."""
    return stats.sentiment_by_index(
        bundle.get("analysis", {}).get("item_sentiments", []), len(bundle.get("items", []))
    )


def _sentiment_label(sentiments: Dict[int, str], index) -> str:
    return SENTIMENT_TITLES.get(sentiments.get(index), "Not analysed")


def _source_entry(index: int, item: dict, sentiment: str) -> List[str]:
    """One numbered appendix entry: a linked title line, a metadata line, and
    an optional quoted-body line. Bullets rather than a table row, because 150
    rows of up to 299-character titles force horizontal scroll on GitHub and
    an unwieldy width-constrained block in Notion and Obsidian."""
    title = item.get("title") or "(untitled post)"
    lines = [
        "{}. **{}** · r/{}".format(index, _link(title, item.get("url", "")), item.get("subreddit", "")),
        "   - {} · {} upvotes · {} comments · {}".format(
            item.get("date", "") or "n/a",
            _thousands(item.get("score", 0)),
            _thousands(item.get("comments", 0)),
            sentiment,
        ),
    ]
    body = _escape(item.get("body", ""))
    if body:
        lines.append("   - > {}".format(body))
    return lines


def _source_threads_section(bundle: dict) -> List[str]:
    """Every analysed post, in the order it was ranked, so the whole sample is
    auditable rather than only the items that ended up cited.

    Iterates `bundle["items"]` (the analysed sample), not `bundle["posts"]`
    (the fetched pool): the pool is unbounded and already has a fitting
    medium in posts_csv, while the appendix's own stated purpose is to match
    the report's "posts analysed" count. Comments are skipped: they have no
    title (reddit_data.py sets it to ""), so each entry would be a bare
    truncated blockquote; comments_csv is the fitting medium for those.

    Numbered with `enumerate(items, 1)`, so the list marker is exactly the
    citation index `stats.citations_for` prints elsewhere in the report. This
    depends on `analysis.select_items` placing posts first and contiguous in
    `items`, which is what makes analysed-post indices start at 1 with no
    gaps; verified against the bundled sample.
    """
    items = bundle.get("items", [])
    sentiments = _item_sentiments(bundle)

    entries: List[List[str]] = []
    for index, item in enumerate(items, 1):
        if item.get("kind") == "comment":
            continue
        if len(entries) >= MAX_SOURCE_THREADS:
            break
        entries.append(_source_entry(index, item, _sentiment_label(sentiments, index)))

    out = ["## Source threads", ""]
    if not entries:
        out.append("No posts were analysed in this run.")
        out.append("")
        return out

    out.append(
        "Every post the analysis read, in the order it ranked them. The number is "
        "the citation index used above, so evidence link #12 is entry 12 here. "
        "Bodies are quoted as fetched and cut to a few hundred characters. The "
        "full fetched pools are in the CSV exports."
    )
    out.append("")
    for entry in entries:
        out.extend(entry)
    out.append("")
    return out


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


def _count(requirement: dict, key: str) -> int:
    """One triage count off a requirement, as an int, 0 for anything unusable."""
    try:
        return int(requirement.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _requirement_line(requirement: dict) -> str:
    """One requirement's text plus its three triage counts, phrased with
    `kind` the same way the on-screen report does: "require" reads as
    something the brief asked for, "exclude" as something it ruled out.
    `text` is already a positive sentence either way (a must-not is
    normalised to something like "The author is not a beginner or a solo
    founder"), so it is never re-negated here. Runs through `_escape` because
    the text originates from a model.

    A requirement flagged `unreliable` gets different copy. Printing "107
    contradicted it" for a requirement the model marked as contradicted by
    nearly every thread would be a false claim about real threads in a document
    the user may share, so the line says what actually happened instead."""
    framing = "You asked for this" if requirement.get("kind") == "require" else "You ruled this out"
    if requirement.get("unreliable"):
        rated = sum(_count(requirement, key) for key in ("met", "violated", "unknown"))
        return (
            "{}: {} The model marked {} of the {} threads it rated as contradicting "
            "this, which almost certainly means it could not judge this from the text "
            "rather than that the threads really break it, so it was not used for "
            "ranking.".format(
                framing,
                _escape(requirement.get("text", "")),
                _thousands(requirement.get("violated", 0)),
                _thousands(rated),
            )
        )
    return "{}: {} {} threads showed it, {} contradicted it, and {} did not say.".format(
        framing,
        _escape(requirement.get("text", "")),
        _thousands(requirement.get("met", 0)),
        _thousands(requirement.get("violated", 0)),
        _thousands(requirement.get("unknown", 0)),
    )


def _qualifier_analysed_line(counts: dict) -> str:
    """How the final analysed sample split on the requirements, not the whole
    triaged pool: the number that tells a reader how much of what follows is
    actually confirmed versus merely not contradicted."""
    confirmed = counts.get("confirmed", 0) or 0
    unconfirmed = counts.get("unconfirmed", 0) or 0
    violated = counts.get("violated", 0) or 0
    total = confirmed + unconfirmed + violated
    tail = ""
    if violated:
        tail = ", and {} were kept despite contradicting a requirement because nothing " \
            "better was available".format(_thousands(violated))
    return (
        "Of the {} items actually analysed, {} showed clear evidence for a requirement "
        "and {} never said either way{}.".format(
            _thousands(total), _thousands(confirmed), _thousands(unconfirmed), tail
        )
    )


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
    sentiments = _item_sentiments(bundle)
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
            _sentiment_label(sentiments, index),
            post.get("url", ""),
        ])
    return _csv(
        ["subreddit", "title", "date", "upvotes", "comments", "sentiment", "url"], rows
    )


def comments_csv(bundle: dict) -> str:
    sentiments = _item_sentiments(bundle)
    index_by_id = {}
    for position, item in enumerate(bundle.get("items", []), 1):
        if item.get("kind") == "comment":
            index_by_id[item.get("id")] = position

    rows = []
    for comment in bundle.get("comments", []):
        index = index_by_id.get(comment.get("id"))
        rows.append([
            comment.get("subreddit", ""),
            " ".join((comment.get("body") or "").split()),
            comment.get("date", ""),
            comment.get("score", 0),
            _sentiment_label(sentiments, index),
            comment.get("url", ""),
        ])
    return _csv(["subreddit", "comment", "date", "upvotes", "sentiment", "url"], rows)


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
