"""Run orchestration: input -> data -> labels -> numbers -> one report bundle.

Kept separate from any interface so the exact code path the API uses can also be
exercised from a terminal (see scripts/run_report.py), and so nothing in here has
to know whether a browser, a CLI or a test is watching.

The bundle this produces is the app's only unit of state: it is what the frontend
holds, what sample_report.json contains, and what every exporter reads.
"""

from __future__ import annotations

import inspect
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Callable, List, Optional, Sequence

import analysis
import discovery
import reddit_data
import stats

logger = logging.getLogger(__name__)

BUNDLE_VERSION = 4
MIN_QUERY_CHARS = 10
MAX_QUERY_CHARS = 300
MAX_PROFILE_CHARS = 500
# Advanced-settings choices. The default is the full fetch window; shorter ranges
# exist for people who explicitly want only what is fresh.
TIME_WINDOWS = (7, 14, reddit_data.FETCH_WINDOW_DAYS)
# The output formats the ideas may take, when the user asks for a subset of them.
# An empty selection means the model picks the format that suits each idea.
CONTENT_TYPES = tuple(analysis.CONTENT_TYPE_FORMATS.keys())
# The history label has to fit a sidebar, so the query is cut down to a glance.
MAX_LABEL_QUERY_CHARS = 30
# Bodies kept in the bundle's raw `posts` and `comments` lists. The full text is
# what the analysis and the citation excerpts are built from, and both of those
# happen before this trim; past that point the raw lists only feed source tables
# and CSV exports, where 300 characters is already more than anyone reads. A real
# run used to ship a 1.7 MB bundle almost entirely made of post bodies.
BUNDLE_BODY_CHARS = 300
# How much of the clock to keep back for classification and synthesis once the
# fetching is done. Both are single model calls with retries, and a run that
# spends its last second fetching one more page has nothing to show for it.
DEADLINE_RESERVE_SECONDS = 60

_OUT_OF_TIME = (
    "This run took longer than we can keep it open. Try a shorter time range, or "
    "choose the communities yourself so we can go straight to them."
)
_NO_COMMUNITIES = (
    "We could not find any communities to read for that. Try describing what you "
    "are looking for in different words, or choose the communities yourself."
)


class RunError(Exception):
    """A run that could not produce a report, with a message fit for a user."""


class RunCancelled(Exception):
    """The caller asked for this run to stop, so it stopped.

    Deliberately not a RunError: nobody is waiting for a message, because the only
    thing that sets the cancel event is the client going away. The API layer
    swallows it.
    """


def _accepts(func: Callable, name: str) -> bool:
    """Whether `func` will take a keyword argument called `name`.

    The fetch and discovery callables are injection points, so a caller (or a test)
    is free to pass a two-argument fake that predates the deadline plumbing. Asking
    the signature is how the deadline reaches the real implementations without
    breaking every substitute for them.
    """
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        return False
    for parameter in signature.parameters.values():
        if parameter.kind == inspect.Parameter.VAR_KEYWORD:
            return True
        if parameter.name == name and parameter.kind in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        ):
            return True
    return False


def validate_inputs(
    query: str,
    profile: str,
    days: Optional[int] = None,
    subreddits: Optional[object] = None,
    relevance_filter: Optional[bool] = None,
    content_types: Optional[object] = None,
    content_type: Optional[object] = None,
) -> dict:
    """Check every field at once and collect all problems.

    Reporting one error at a time makes people submit four times to learn four
    things; this returns everything wrong in one pass, plus the cleaned values.
    The last fields are advanced settings and all optional: absent means the
    defaults (full window, automatic discovery, relevance filter on, mixed
    formats).

    `content_types` is the list of formats the ideas should take. `content_type`
    is the single value the form used to send and is still accepted, because old
    links and stored history replay it; it reads as a one-item list.
    """
    errors: List[str] = []

    cleaned_query = (query or "").strip()
    if not cleaned_query:
        errors.append("Tell us what you want content ideas about.")
    elif len(cleaned_query) < MIN_QUERY_CHARS:
        errors.append(
            "Add a little more detail about what you want content ideas about "
            "(at least {} characters).".format(MIN_QUERY_CHARS)
        )
    elif len(cleaned_query) > MAX_QUERY_CHARS:
        errors.append("Shorten that to {} characters or fewer.".format(MAX_QUERY_CHARS))

    cleaned_profile = (profile or "").strip()
    if len(cleaned_profile) > MAX_PROFILE_CHARS:
        errors.append(
            "Shorten the description of you or your brand to {} characters or fewer.".format(
                MAX_PROFILE_CHARS
            )
        )

    cleaned_days = _clean_days(days, errors)
    cleaned_subreddits = _clean_subreddits(subreddits, errors)
    cleaned_relevance = _clean_relevance_filter(relevance_filter, errors)
    cleaned_content_types = _clean_content_types(content_types, content_type, errors)

    return {
        "errors": errors,
        "query": cleaned_query,
        "profile": cleaned_profile,
        "days": cleaned_days,
        "subreddits": cleaned_subreddits,
        "relevance_filter": cleaned_relevance,
        "content_types": cleaned_content_types,
    }


def _time_window_sentence() -> str:
    """"7, 14 or 30 days", written from TIME_WINDOWS so it cannot drift from it."""
    windows = [str(value) for value in TIME_WINDOWS]
    if len(windows) == 1:
        return windows[0] + " days"
    return "{} or {} days".format(", ".join(windows[:-1]), windows[-1])


def _clean_days(days, errors: List[str]) -> int:
    if days is None:
        return reddit_data.FETCH_WINDOW_DAYS

    message = "Choose a time range of {}.".format(_time_window_sentence())
    # bool is an int subclass, so True would otherwise read as the number 1.
    if isinstance(days, bool):
        errors.append(message)
        return reddit_data.FETCH_WINDOW_DAYS

    try:
        value = float(days)
    except (TypeError, ValueError):
        errors.append(message)
        return reddit_data.FETCH_WINDOW_DAYS

    # 7.9 is not "about a week", it is a client sending something we did not offer.
    if value != int(value) or int(value) not in TIME_WINDOWS:
        errors.append(message)
        return reddit_data.FETCH_WINDOW_DAYS
    return int(value)


def _clean_subreddits(subreddits, errors: List[str]) -> List[str]:
    """Accept the comma-separated field, or a JSON list of names, and nothing else."""
    if subreddits is None:
        return []

    if isinstance(subreddits, (list, tuple)):
        parts = []
        for entry in subreddits:
            # A list is the one shape where a stray number or bool is invisible in
            # the rendered value, so it is rejected by type rather than coerced.
            if isinstance(entry, bool) or not isinstance(entry, str):
                errors.append("Enter subreddit names as text, separated by commas.")
                return []
            parts.append(entry)
        raw = ",".join(parts)
    elif isinstance(subreddits, str):
        raw = subreddits
    else:
        errors.append("Enter subreddit names as text, separated by commas.")
        return []

    if not raw.strip():
        return []
    try:
        return reddit_data.parse_subreddit_input(raw)
    except ValueError as exc:
        errors.append(str(exc))
        return []


def _clean_relevance_filter(relevance_filter, errors: List[str]) -> bool:
    """A real boolean, or the two strings a form encoding can legitimately send.

    `bool("false")` is True, which is how a setting the user switched OFF used to
    arrive switched ON. Anything that is not unambiguous is an error rather than a
    guess, because guessing here silently spends a model call the user declined.
    """
    if relevance_filter is None:
        return True
    if isinstance(relevance_filter, bool):
        return relevance_filter
    if isinstance(relevance_filter, str):
        text = relevance_filter.strip().lower()
        if text in ("true", "1", "yes", "on"):
            return True
        if text in ("false", "0", "no", "off", ""):
            return False
        errors.append("The relevance setting must be on or off.")
        return True
    if isinstance(relevance_filter, int):
        return bool(relevance_filter)
    errors.append("The relevance setting must be on or off.")
    return True


def _content_type_sentence() -> str:
    """"text, carousel, video, poll or article", written from the offered set."""
    return "{} or {}".format(", ".join(CONTENT_TYPES[:-1]), CONTENT_TYPES[-1])


def _content_type_shape_error() -> str:
    return (
        "Send the content types as a list of names ({}), or leave it unset for a "
        "mix.".format(_content_type_sentence())
    )


def _content_type_values(raw, errors: List[str]) -> List[str]:
    """The known content types in one field, complaining about anything else."""
    if raw is None:
        return []
    if isinstance(raw, str):
        entries: Sequence = [raw]
    elif isinstance(raw, (list, tuple)):
        entries = list(raw)
    else:
        errors.append(_content_type_shape_error())
        return []

    values: List[str] = []
    for entry in entries:
        # bool is an int subclass, and neither is a content type. Coercing one
        # would turn a client bug into a format choice nobody made.
        if isinstance(entry, bool) or not isinstance(entry, str):
            errors.append(_content_type_shape_error())
            continue
        text = entry.strip().lower()
        if not text:
            continue
        if text not in CONTENT_TYPES:
            errors.append(
                'We do not offer a content type called "{}". Choose from {}.'.format(
                    entry.strip(), _content_type_sentence()
                )
            )
            continue
        values.append(text)
    return values


def _clean_content_types(content_types, content_type, errors: List[str]) -> List[str]:
    """The chosen formats, deduped and sorted; empty means a mix.

    Both fields are read, because a link or a history entry written before the
    setting took several values still sends the single `content_type`. Order
    carries no meaning here, so the answer is sorted and the two fields simply
    add up.
    """
    chosen = set(_content_type_values(content_types, errors))
    chosen.update(_content_type_values(content_type, errors))
    return selected_content_types(chosen)


def selected_content_types(content_types) -> List[str]:
    """The subset a run is pinned to, sorted; empty when the ideas may vary.

    Asking for every format we offer is the same request as asking for none, so
    it comes back empty and nothing downstream has to special-case it.
    """
    chosen = sorted(
        {
            value.strip().lower()
            for value in (content_types or [])
            if isinstance(value, str) and value.strip().lower() in CONTENT_TYPES
        }
    )
    if len(chosen) >= len(CONTENT_TYPES):
        return []
    return chosen


def _noop(_message: str) -> None:
    pass


def _human_list(names: Sequence[str]) -> str:
    """"a, b or c", a list a person would actually read aloud."""
    labels = ["r/{}".format(name) for name in names]
    if len(labels) <= 1:
        return "".join(labels)
    return "{} or {}".format(", ".join(labels[:-1]), labels[-1])


def _lowercase_first(message: str) -> str:
    """"We could not..." -> "we could not...", for the middle of a sentence."""
    text = str(message or "").strip()
    if not text:
        return "no reason was given."
    return text[0].lower() + text[1:]


def _truncate_bodies(records: Sequence[dict], chars: int = BUNDLE_BODY_CHARS) -> List[dict]:
    """Copies with their bodies cut down, for the raw lists the bundle carries."""
    trimmed = []
    for record in records or []:
        record = dict(record or {})
        body = str(record.get("body") or "")
        if len(body) > chars:
            record["body"] = body[: chars - 1].rstrip() + "…"
        trimmed.append(record)
    return trimmed


def _resolve_citations(
    indices: Sequence[int], items: Sequence[dict], comments: Sequence[dict]
) -> List[dict]:
    return stats.citations_for(indices, items, comments=comments)


def run_report(
    query: str,
    profile: str,
    days: int = reddit_data.FETCH_WINDOW_DAYS,
    subreddits: Optional[Sequence[str]] = None,
    relevance_filter: bool = True,
    content_types: Optional[Sequence[str]] = None,
    progress: Optional[Callable[[str], None]] = None,
    discover: Optional[Callable] = None,
    manual: Optional[Callable] = None,
    fetch_posts: Optional[Callable] = None,
    fetch_comments: Optional[Callable] = None,
    classify: Optional[Callable] = None,
    synthesize: Optional[Callable] = None,
    *,
    cancel: Optional[threading.Event] = None,
    deadline: Optional[float] = None,
) -> dict:
    """Produce a complete report bundle, or raise RunError with a friendly message.

    `days`, `subreddits`, `relevance_filter` and `content_types` are the advanced
    settings: a shorter window, a manual list of communities that skips discovery
    entirely, a switch that turns the relevance triage off for a faster,
    engagement-only selection, and the set of output formats the ideas may take
    (empty, or all of them, meaning a mix). The six
    callables are injection points so callers can supply cached wrappers or fakes.
    A ValueError from discovery or from the model layer already carries a
    user-ready message and is left to propagate, exactly as the analysis calls'
    errors are.

    `cancel` and `deadline` are how a caller reclaims a run it can no longer use.
    Both are checked at every stage boundary. A set `cancel` event raises
    RunCancelled, which carries no message because nobody is left to read one. A
    passed `deadline` (an absolute time.monotonic() value) raises RunError with a
    sentence the user can act on. Before that, the last DEADLINE_RESERVE_SECONDS
    are held back: once the clock is inside them, no new community is fetched and
    no further page is requested, and the run goes on to analyse what it has.
    """
    say = progress or _noop
    discover = discover or discovery.discover
    manual = manual or discovery.manual
    fetch_posts = fetch_posts or reddit_data.fetch_posts
    fetch_comments = fetch_comments or reddit_data.fetch_comments
    classify = classify or analysis.classify_sample
    synthesize = synthesize or analysis.synthesize

    chosen_types = selected_content_types(content_types)

    started = time.time()
    logger.info(
        "run start: query=%r days=%s manual_subreddits=%s relevance=%s content_types=%s",
        query, days, list(subreddits or []), relevance_filter, chosen_types or "a mix",
    )

    def check(stage: str) -> None:
        """Stop here if the caller has gone away or the clock has run out."""
        if cancel is not None and cancel.is_set():
            logger.info("run cancelled at %s", stage)
            raise RunCancelled(stage)
        if deadline is not None and time.monotonic() >= deadline:
            logger.warning("run out of time at %s", stage)
            raise RunError(_OUT_OF_TIME)

    def fetching_time_left() -> bool:
        """False once the clock is inside the reserve kept for the model calls."""
        if deadline is None:
            return True
        return time.monotonic() < deadline - DEADLINE_RESERVE_SECONDS

    # The deadline handed down to discovery and to the fetchers is the reserved one,
    # not the hard one: they should stop early enough to leave the analysis its time.
    soft_deadline = None if deadline is None else deadline - DEADLINE_RESERVE_SECONDS

    check("start")
    if subreddits:
        say("Checking the communities you chose…")
        found_communities = _call(manual, subreddits, deadline=soft_deadline)
        discovery_mode = "manual"
    else:
        say("Finding communities talking about this…")
        found_communities = _call(discover, query, profile, deadline=soft_deadline)
        discovery_mode = "model"

    communities = [
        community
        for community in (found_communities.get("communities") or [])
        if isinstance(community, dict) and community.get("name")
    ]
    names = [community["name"] for community in communities]
    if not names:
        # Distinct from the "nothing readable" error below: there was never a
        # community to read, so naming the ones we tried is not an option.
        raise RunError(_NO_COMMUNITIES)
    check("discovery")
    say("Found {}. Fetching posts…".format(", ".join("r/{}".format(name) for name in names)))

    posts: List[dict] = []
    comments: List[dict] = []
    notices: List[str] = []
    used: List[str] = []

    for name in names:
        # Discovery already established these exist, so there is nothing for a
        # separate existence check to add; a name it could not check (checked=False)
        # is caught by the fetch errors below.
        # The reserve is what keeps a run out of the hard deadline: once the clock
        # is inside it, the remaining communities become a notice and the run goes
        # on to analyse what it has. Passing the hard deadline anyway means one
        # community's fetch overran by more than the whole reserve, and there is no
        # longer time to classify or synthesise, so `check` ends the run there.
        check("fetch")
        if used and not fetching_time_left():
            notices.append(
                "We ran out of time before reading r/{}, so this report is built "
                "from the communities above.".format(name)
            )
            logger.warning("skipping r/%s: inside the analysis reserve", name)
            break

        found: List[dict] = []
        found_comments: List[dict] = []
        try:
            say("Fetching posts from r/{}…".format(name))
            found = _call(fetch_posts, name, days, deadline=soft_deadline)
            say("Fetching posts from r/{}… ({} so far)".format(name, len(found)))
        except (ValueError, RuntimeError) as exc:
            notices.append("r/{} was skipped: {}".format(name, _lowercase_first(exc)))
            continue

        if not found:
            notices.append(
                "r/{} had no posts in the last {} days, so it was left out.".format(name, days)
            )
            continue

        try:
            say("Fetching comments from r/{}…".format(name))
            found_comments = _call(fetch_comments, name, days, deadline=soft_deadline)
        except (ValueError, RuntimeError) as exc:
            # Comments are supporting evidence. Throwing away a community's posts
            # because its comments failed loses the part of the fetch that worked.
            logger.warning("comments failed for r/%s, keeping its posts: %s", name, exc)
            notices.append(
                "We could read r/{}'s posts but not its comments, so its replies are "
                "missing from this report.".format(name)
            )
            found_comments = []

        posts.extend(found)
        comments.extend(found_comments)
        used.append(name)

    if not used:
        # Every notice, not just the first, and the communities by name: the user did
        # not choose them, so a bare "nothing found" tells them nothing about where
        # we looked.
        detail = " ".join(notices)
        raise RunError(
            "{}We could not read anything usable from {}. Try describing what you "
            "are looking for in different words.".format(
                detail + " " if detail else "", _human_list(names)
            )
        )

    posts.sort(key=lambda p: p.get("created_utc", 0), reverse=True)
    comments.sort(key=lambda c: c.get("created_utc", 0), reverse=True)

    # The analysis layer decides how big a sample the model plan can take, so the
    # counts reported to the user come back from it rather than being assumed here.
    check("classification")
    say("Selecting the most relevant items…" if relevance_filter else "Selecting items…")
    # An empty query is how the analysis layer is told to skip the relevance
    # triage; the selection metadata is relabelled below so the report says
    # "turned off" rather than "unavailable".
    say("Classifying the sample…")
    items, preview_chars, classification = classify(
        posts, comments, used, query if relevance_filter else "", profile
    )
    n_posts_analysed = sum(1 for item in items if item.get("kind") != "comment")
    n_comments_analysed = len(items) - n_posts_analysed
    say("Classified {} posts and {} comments…".format(n_posts_analysed, n_comments_analysed))

    check("synthesis")
    say("Writing your content ideas…")
    synthesis = synthesize(
        items, classification["topics"], query, profile, used, preview_chars,
        content_types=chosen_types,
    )

    # ── Everything below is computed, not generated ───────────────────────────
    n_items = len(items)
    sentiment = stats.sentiment_breakdown(classification["item_sentiments"], n_items)
    volumes = stats.topic_volumes(classification["topics"], n_items)
    descriptions = synthesis["topic_descriptions"]

    def cite(indices: Sequence[int]) -> List[dict]:
        # Built from the untruncated comment pool, before the bundle's copies are
        # trimmed, so every excerpt is the real text rather than a preview of it.
        return _resolve_citations(indices, items, comments)

    topics = []
    for volume in volumes:
        topics.append(
            {
                "id": volume["id"],
                "label": volume["label"],
                "count": volume["count"],
                "share": volume["share"],
                "finding": descriptions.get(volume["id"], ""),
                "citations": cite(volume["item_indices"]),
            }
        )

    content_ideas = [
        {
            "title": idea["title"],
            "hook": idea["hook"],
            "format": idea["format"],
            "angle": idea["angle"],
            "rationale": idea["rationale"],
            "addresses": idea["addresses"],
            "citations": cite(idea["item_indices"]),
        }
        for idea in synthesis["content_ideas"]
    ]
    pain_points = [
        {"text": claim["text"], "citations": cite(claim["item_indices"])}
        for claim in synthesis["pain_points"]
    ]
    questions = [
        {"text": claim["text"], "citations": cite(claim["item_indices"])}
        for claim in synthesis["questions"]
    ]

    # Every model this run actually used, in the order of preference for labelling:
    # synthesis wrote the words a reader will judge, so it names the report.
    synthesis_model = synthesis.get("model") or ""
    classification_model = classification.get("model") or ""
    models = [model for model in (synthesis_model, classification_model) if model]
    primary_model = models[0] if models else analysis.PRIMARY_MODEL
    unique_models = sorted(set(models)) or [analysis.PRIMARY_MODEL]

    date_range = stats.actual_date_range(posts)
    # `content_types` is written only when the run was pinned to a real subset:
    # a mixed run made no format choice, and recording an empty list as if it
    # had would make every reader test the length before believing it.
    inputs = {
        "query": query,
        "profile": profile,
        "subreddits": used,
        "days": days,
    }
    if chosen_types:
        inputs["content_types"] = chosen_types
    inputs["discovery"] = discovery_mode

    bundle = {
        "bundle_version": BUNDLE_VERSION,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "generated_ts": int(time.time()),
        "elapsed_seconds": round(time.time() - started, 1),
        "is_sample": False,
        "model": primary_model,
        "models": unique_models,
        "model_label": " and ".join(
            analysis.model_display_name(model) for model in unique_models
        ),
        "source": reddit_data.SOURCE_NAME,
        "inputs": inputs,
        "discovery": {
            # Rebuilt rather than passed through: a cached discover() must not have
            # its own result mutated, and `used` is ours to decide, not discovery's.
            "communities": [
                {
                    "name": community["name"],
                    "reason": community.get("reason", ""),
                    "subscribers": community.get("subscribers", 0),
                    "used": community["name"] in used,
                }
                for community in communities
            ],
            "considered": list(found_communities.get("considered") or []),
        },
        "notices": notices,
        "posts": _truncate_bodies(posts),
        "comments": _truncate_bodies(comments),
        "items": _truncate_bodies(items),
        "sample": {
            "posts_fetched": len(posts),
            "comments_fetched": len(comments),
            "posts_analysed": n_posts_analysed,
            "comments_analysed": n_comments_analysed,
            "items_analysed": n_items,
            "posts_truncated": len(posts) > n_posts_analysed,
            "comments_truncated": len(comments) > n_comments_analysed,
            "low_data": n_items < analysis.LOW_DATA_THRESHOLD,
            "capacity_limited": bool(classification.get("capacity_limited")),
            "selection": (
                {
                    "method": "relevance-off",
                    "triaged": 0,
                    "relevance_counts": {"0": 0, "1": 0, "2": 0},
                }
                if not relevance_filter
                else classification.get(
                    "selection",
                    {
                        "method": "deterministic-fallback",
                        "triaged": 0,
                        "relevance_counts": {"0": 0, "1": 0, "2": 0},
                    },
                )
            ),
        },
        "analysis": {
            "report_title": synthesis.get("report_title")
            or analysis.report_title_from_query(query),
            "executive_summary": synthesis["executive_summary"],
            "topics": topics,
            "pain_points": pain_points,
            "questions": questions,
            "content_ideas": content_ideas,
            "item_sentiments": classification["item_sentiments"],
        },
        "stats": {
            "sentiment": sentiment,
            "engagement": stats.engagement_totals(posts, items),
            "date_range": date_range,
            "lag_hours": stats.data_lag_hours(date_range.get("newest_ts", 0)),
            "trend": stats.trend_buckets(posts, max_days=days),
        },
    }

    logger.info(
        "run complete in %.1fs: %s items, %s topics, %s ideas",
        bundle["elapsed_seconds"],
        n_items,
        len(topics),
        len(bundle["analysis"]["content_ideas"]),
    )
    return bundle


def _call(func: Callable, *args, deadline: Optional[float] = None):
    """Call an injected callable, passing the deadline only if it takes one."""
    if deadline is not None and _accepts(func, "deadline"):
        return func(*args, deadline=deadline)
    return func(*args)


def run_label(bundle: dict) -> str:
    """Short identifier for the sidebar history list."""
    inputs = bundle.get("inputs", {})
    query = " ".join(str(inputs.get("query") or "Report").split())
    if len(query) > MAX_LABEL_QUERY_CHARS:
        query = query[: MAX_LABEL_QUERY_CHARS - 1].rstrip() + "…"
    return query
