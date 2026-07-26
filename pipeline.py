"""Run orchestration: input -> data -> labels -> numbers -> one report bundle.

Kept out of app.py so the Streamlit file stays pure rendering, and so the exact
code path the UI uses can be exercised from a terminal (see scripts/run_report.py).

The bundle this produces is the app's only unit of state: it is what session_state
holds, what sample_report.json contains, and what every exporter reads.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Callable, List, Optional, Sequence

import analysis
import discovery
import reddit_data
import stats

logger = logging.getLogger(__name__)

BUNDLE_VERSION = 3
TIME_WINDOWS = (7, 14, 30)
MIN_QUERY_CHARS = 10
MAX_QUERY_CHARS = 300
MAX_PROFILE_CHARS = 500
# The history label has to fit a sidebar, so the query is cut down to a glance.
MAX_LABEL_QUERY_CHARS = 30


class RunError(Exception):
    """A run that could not produce a report, with a message fit for a user."""


def validate_inputs(query: str, profile: str, days: int) -> dict:
    """Check every field at once and collect all problems.

    Reporting one error at a time makes people submit four times to learn four
    things; this returns everything wrong in one pass, plus the cleaned values.
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

    if days not in TIME_WINDOWS:
        errors.append("Choose a time window of 7, 14 or 30 days.")

    return {
        "errors": errors,
        "query": cleaned_query,
        "profile": cleaned_profile,
        "days": days,
    }


def _noop(_message: str) -> None:
    pass


def _human_list(names: Sequence[str]) -> str:
    """"a, b or c", a list a person would actually read aloud."""
    labels = ["r/{}".format(name) for name in names]
    if len(labels) <= 1:
        return "".join(labels)
    return "{} or {}".format(", ".join(labels[:-1]), labels[-1])


def _resolve_citations(indices: Sequence[int], items: Sequence[dict]) -> List[dict]:
    return stats.citations_for(indices, items)


def run_report(
    query: str,
    profile: str,
    days: int,
    progress: Optional[Callable[[str], None]] = None,
    discover: Optional[Callable] = None,
    fetch_posts: Optional[Callable] = None,
    fetch_comments: Optional[Callable] = None,
    classify: Optional[Callable] = None,
    synthesize: Optional[Callable] = None,
) -> dict:
    """Produce a complete report bundle, or raise RunError with a friendly message.

    The five callables are injection points so the Streamlit layer can supply cached
    wrappers without this module importing streamlit. A ValueError from discovery or
    from the model layer already carries a user-ready message and is left to
    propagate, exactly as the analysis calls' errors are.
    """
    say = progress or _noop
    discover = discover or discovery.discover
    fetch_posts = fetch_posts or reddit_data.fetch_posts
    fetch_comments = fetch_comments or reddit_data.fetch_comments
    classify = classify or analysis.classify_sample
    synthesize = synthesize or analysis.synthesize

    started = time.time()
    logger.info("run start: query=%r days=%s", query, days)

    say("Finding communities talking about this…")
    found_communities = discover(query, profile)
    communities = list(found_communities.get("communities") or [])
    names = [community["name"] for community in communities]
    say("Found {}. Fetching posts…".format(", ".join("r/{}".format(name) for name in names)))

    posts: List[dict] = []
    comments: List[dict] = []
    notices: List[str] = []
    used: List[str] = []

    for name in names:
        # Discovery already established these exist, so there is nothing for
        # validate_subreddit to add; a name it could not check (checked=False) is
        # caught by the fetch errors below.
        try:
            say("Fetching posts from r/{}…".format(name))
            found = fetch_posts(name, days)
            say("Fetching posts from r/{}… ({} so far)".format(name, len(found)))
            say("Fetching comments from r/{}…".format(name))
            found_comments = fetch_comments(name, days)
        except ValueError as exc:
            notices.append("r/{} was skipped: {}".format(name, str(exc)[0].lower() + str(exc)[1:]))
            continue
        except RuntimeError as exc:
            notices.append("r/{} was skipped: {}".format(name, str(exc)[0].lower() + str(exc)[1:]))
            continue

        if not found:
            notices.append(
                "r/{} had no posts in the last {} days, so it was left out.".format(name, days)
            )
            continue

        posts.extend(found)
        comments.extend(found_comments)
        used.append(name)

    if not used:
        # Every notice, not just the first, and the communities by name: the user did
        # not choose them, so a bare "nothing found" tells them nothing about where
        # we looked.
        detail = " ".join(notices)
        raise RunError(
            "{}We could not read anything usable from {}. Try a 30-day window, or "
            "describe what you are looking for in different words.".format(
                detail + " " if detail else "", _human_list(names)
            )
        )

    posts.sort(key=lambda p: p.get("created_utc", 0), reverse=True)
    comments.sort(key=lambda c: c.get("created_utc", 0), reverse=True)

    # The analysis layer decides how big a sample the model plan can take, so the
    # counts reported to the user come back from it rather than being assumed here.
    say("Classifying posts and comments…")
    items, preview_chars, classification = classify(posts, comments, used)
    n_posts_analysed = sum(1 for item in items if item.get("kind") != "comment")
    n_comments_analysed = len(items) - n_posts_analysed
    say("Classified {} posts and {} comments…".format(n_posts_analysed, n_comments_analysed))

    say("Writing your content ideas…")
    synthesis = synthesize(
        items, classification["topics"], query, profile, used, preview_chars
    )

    # ── Everything below is computed, not generated ───────────────────────────
    n_items = len(items)
    sentiment = stats.sentiment_breakdown(classification["item_sentiments"], n_items)
    volumes = stats.topic_volumes(classification["topics"], n_items)
    descriptions = synthesis["topic_descriptions"]

    topics = []
    for volume in volumes:
        topics.append(
            {
                "id": volume["id"],
                "label": volume["label"],
                "count": volume["count"],
                "share": volume["share"],
                "finding": descriptions.get(volume["id"], ""),
                "citations": _resolve_citations(volume["item_indices"], items),
            }
        )

    date_range = stats.actual_date_range(posts)
    bundle = {
        "bundle_version": BUNDLE_VERSION,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "generated_ts": int(time.time()),
        "elapsed_seconds": round(time.time() - started, 1),
        "is_sample": False,
        "model": classification.get("model", analysis.PRIMARY_MODEL),
        "model_label": analysis.model_display_name(
            classification.get("model", analysis.PRIMARY_MODEL)
        ),
        "source": reddit_data.SOURCE_NAME,
        "inputs": {
            "query": query,
            "profile": profile,
            "subreddits": used,
            "days": days,
        },
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
        "posts": posts,
        "comments": comments,
        "items": items,
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
        },
        "analysis": {
            "executive_summary": synthesis["executive_summary"],
            "topics": topics,
            "pain_points": [
                {"text": claim["text"], "citations": _resolve_citations(claim["item_indices"], items)}
                for claim in synthesis["pain_points"]
            ],
            "questions": [
                {"text": claim["text"], "citations": _resolve_citations(claim["item_indices"], items)}
                for claim in synthesis["questions"]
            ],
            "content_ideas": [
                {
                    "title": idea["title"],
                    "hook": idea["hook"],
                    "format": idea["format"],
                    "angle": idea["angle"],
                    "rationale": idea["rationale"],
                    "addresses": idea["addresses"],
                    "citations": _resolve_citations(idea["item_indices"], items),
                }
                for idea in synthesis["content_ideas"]
            ],
            "item_sentiments": classification["item_sentiments"],
        },
        "stats": {
            "sentiment": sentiment,
            "engagement": stats.engagement_totals(posts),
            "date_range": date_range,
            "lag_hours": stats.data_lag_hours(date_range.get("newest_ts", 0)),
            "trend": stats.trend_buckets(posts, days),
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


def run_label(bundle: dict) -> str:
    """Short identifier for the sidebar history list."""
    inputs = bundle.get("inputs", {})
    query = " ".join(str(inputs.get("query") or "Report").split())
    if len(query) > MAX_LABEL_QUERY_CHARS:
        query = query[: MAX_LABEL_QUERY_CHARS - 1].rstrip() + "…"
    return "{} · {}d".format(query, inputs.get("days", ""))
