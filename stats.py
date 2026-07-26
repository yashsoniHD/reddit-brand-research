"""Deterministic report numbers.

Every number a user sees comes from here: pure Python over data we fetched and
over the model's per-item labels. No LLM, no network, no Streamlit, so all of it
is unit-testable and none of it can be hallucinated.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Sequence, Tuple

SENTIMENT_KEYS = ("pos", "neu", "neg")
SENTIMENT_LABELS = {"pos": "Positive", "neu": "Neutral", "neg": "Negative"}
MAX_CITATIONS_PER_CLAIM = 3
EXCERPT_LENGTH = 140
SECONDS_PER_DAY = 86400


# ── Percentages ───────────────────────────────────────────────────────────────
def largest_remainder_percentages(counts: Dict[str, int]) -> Dict[str, int]:
    """Whole-number percentages that always sum to exactly 100.

    Plain rounding of three shares routinely gives 99 or 101, which reads as a bug
    in a report someone is about to send to a client.
    """
    total = sum(max(0, int(v)) for v in counts.values())
    if total <= 0:
        return {key: 0 for key in counts}

    exact = {key: max(0, int(count)) * 100 / total for key, count in counts.items()}
    floors = {key: int(value) for key, value in exact.items()}
    remainder = 100 - sum(floors.values())

    ranked = sorted(exact, key=lambda key: (-(exact[key] - floors[key]), key))
    for key in ranked[:remainder]:
        floors[key] += 1
    return floors


def sentiment_breakdown(item_sentiments: Sequence[dict], n_items: int) -> dict:
    """Counts and percentages built only from items the model actually labelled."""
    counts = {key: 0 for key in SENTIMENT_KEYS}
    seen = set()

    for entry in item_sentiments or []:
        if not isinstance(entry, dict):
            continue
        index = entry.get("i")
        label = entry.get("s")
        if not isinstance(index, int) or not (1 <= index <= n_items) or index in seen:
            continue
        if label not in counts:
            continue
        seen.add(index)
        counts[label] += 1

    classified = sum(counts.values())
    return {
        "counts": counts,
        "percentages": largest_remainder_percentages(counts),
        "classified": classified,
        "unclassified": max(0, n_items - classified),
    }


def sentiment_by_index(item_sentiments: Sequence[dict], n_items: int) -> Dict[int, str]:
    """Index -> sentiment label lookup, ignoring anything malformed."""
    lookup: Dict[int, str] = {}
    for entry in item_sentiments or []:
        if not isinstance(entry, dict):
            continue
        index = entry.get("i")
        label = entry.get("s")
        if isinstance(index, int) and 1 <= index <= n_items and label in SENTIMENT_KEYS:
            lookup.setdefault(index, label)
    return lookup


# ── Topics ────────────────────────────────────────────────────────────────────
def topic_volumes(topics: Sequence[dict], n_items: int) -> List[dict]:
    """Per-topic item count and share of the analysed sample, biggest first."""
    volumes = []
    for topic in topics or []:
        if not isinstance(topic, dict):
            continue
        indices = [
            i for i in (topic.get("item_indices") or [])
            if isinstance(i, int) and 1 <= i <= n_items
        ]
        unique = sorted(set(indices))
        volumes.append(
            {
                "id": topic.get("id"),
                "label": str(topic.get("label", "")),
                "count": len(unique),
                "share": round(100 * len(unique) / n_items, 1) if n_items else 0.0,
                "item_indices": unique,
            }
        )
    volumes.sort(key=lambda t: (-t["count"], t["label"]))
    return volumes


# ── Trend ─────────────────────────────────────────────────────────────────────
def trend_buckets(posts: Sequence[dict], days: int) -> List[Tuple[str, int]]:
    """Posts per day across the whole window, zero-filled, oldest first."""
    days = max(1, int(days))
    today = datetime.now(timezone.utc).date()
    window = [(today - timedelta(days=offset)) for offset in range(days - 1, -1, -1)]
    buckets = {day.strftime("%Y-%m-%d"): 0 for day in window}

    for post in posts or []:
        date = str((post or {}).get("date") or "")
        if date in buckets:
            buckets[date] += 1

    return [(day, buckets[day]) for day in sorted(buckets)]


# ── Totals and ranges ─────────────────────────────────────────────────────────
def engagement_totals(posts: Sequence[dict]) -> dict:
    total_score = 0
    total_comments = 0
    for post in posts or []:
        post = post or {}
        try:
            total_score += int(post.get("score") or 0)
            total_comments += int(post.get("comments") or 0)
        except (TypeError, ValueError):
            continue
    return {"score": total_score, "comments": total_comments}


def actual_date_range(posts: Sequence[dict]) -> dict:
    """Real oldest/newest dates in the sample, plus the newest timestamp.

    The requested window is a ceiling, not a promise. A quiet subreddit may only
    cover three of the seven days asked for, and the report should say so.
    """
    stamps = []
    for post in posts or []:
        try:
            stamp = int((post or {}).get("created_utc") or 0)
        except (TypeError, ValueError):
            continue
        if stamp > 0:
            stamps.append(stamp)

    if not stamps:
        return {
            "oldest": "", "newest": "", "newest_ts": 0,
            "label": "No dated posts", "short_label": "No dated posts",
        }

    oldest_ts, newest_ts = min(stamps), max(stamps)
    oldest = datetime.fromtimestamp(oldest_ts, tz=timezone.utc)
    newest = datetime.fromtimestamp(newest_ts, tz=timezone.utc)
    if oldest.strftime("%b %Y") == newest.strftime("%b %Y"):
        label = "{}-{}".format(oldest.strftime("%d").lstrip("0"), newest.strftime("%d %b %Y"))
        short_label = "{}-{}".format(oldest.strftime("%d").lstrip("0"), newest.strftime("%d %b"))
    else:
        label = "{} to {}".format(oldest.strftime("%d %b"), newest.strftime("%d %b %Y"))
        short_label = "{} to {}".format(oldest.strftime("%d %b"), newest.strftime("%d %b"))

    return {
        "oldest": oldest.strftime("%Y-%m-%d"),
        "newest": newest.strftime("%Y-%m-%d"),
        "newest_ts": newest_ts,
        "newest_iso": newest.strftime("%Y-%m-%d %H:%M UTC"),
        "label": label,
        # Metric tiles are narrow; the year lives in the tooltip and the full report.
        "short_label": short_label,
    }


def data_lag_hours(newest_ts: int) -> float:
    """How far behind live Reddit the newest item in the sample is."""
    if not newest_ts:
        return 0.0
    delta = datetime.now(timezone.utc).timestamp() - int(newest_ts)
    return round(max(0.0, delta) / 3600, 1)


# ── Citations ─────────────────────────────────────────────────────────────────
def _display_text(item: dict) -> str:
    title = (item.get("title") or "").strip()
    if title:
        return title
    body = " ".join((item.get("body") or "").split())
    if len(body) > EXCERPT_LENGTH:
        return body[:EXCERPT_LENGTH].rstrip() + "…"
    return body or "(no text)"


def citations_for(
    indices: Sequence[int],
    items: Sequence[dict],
    limit: int = MAX_CITATIONS_PER_CLAIM,
) -> List[dict]:
    """Resolve model-supplied item indices into real, linkable sources.

    Text and URLs come from our fetched data, never from model output, so a
    citation can never point at something that does not exist.
    """
    citations = []
    seen = set()
    for index in indices or []:
        # bool is an int subclass, and True would silently resolve to item 1.
        if isinstance(index, bool) or not isinstance(index, int):
            continue
        if not (1 <= index <= len(items)):
            continue
        if index in seen:
            continue
        seen.add(index)
        item = items[index - 1] or {}
        if not item.get("url"):
            continue
        citations.append(
            {
                "index": index,
                "text": _display_text(item),
                "url": item.get("url", ""),
                "date": item.get("date", ""),
                "score": item.get("score", 0),
                "type": item.get("kind", "post"),
                "subreddit": item.get("subreddit", ""),
            }
        )
        if len(citations) >= limit:
            break
    return citations
