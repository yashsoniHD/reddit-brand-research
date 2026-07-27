"""Data layer for Reddit Brand Research.

This is the ONLY module that knows where the Reddit data comes from. It currently
reads the public Arctic Shift archive. To swap the source, reimplement the four
public functions below and keep their return shapes identical:

    normalize_subreddit(raw)            -> str
    parse_subreddit_input(raw)          -> list[str]
    validate_subreddit(name)            -> SubredditCheck
    lookup_subreddit(name)              -> Optional[dict]
    fetch_posts(subreddit, days)        -> list[dict]
    fetch_comments(subreddit, days)     -> list[dict]

Nothing above this layer imports `requests` or knows an endpoint URL.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from typing import List, NamedTuple, Optional

import requests

logger = logging.getLogger(__name__)

# ── Source configuration ──────────────────────────────────────────────────────
_API_ROOT = "https://arctic-shift.photon-reddit.com/api"
POSTS_ENDPOINT = f"{_API_ROOT}/posts/search"
COMMENTS_ENDPOINT = f"{_API_ROOT}/comments/search"
SUBREDDITS_ENDPOINT = f"{_API_ROOT}/subreddits/search"

SOURCE_NAME = "Arctic Shift archive"

# ── Request tuning ────────────────────────────────────────────────────────────
# There is no user-facing time window: every run reads the last 30 days and the
# selection layer decides what is worth analysing from that pool.
FETCH_WINDOW_DAYS = 30
PAGE_SIZE = 100
MAX_POST_PAGES = 5            # 5 x 100 = 500 posts ceiling
MAX_COMMENT_PAGES = 3         # 3 x 100 = 300 comments ceiling
DEFAULT_COMMENT_LIMIT = 300
REQUEST_TIMEOUT_SECONDS = 15
VALIDATION_TIMEOUT_SECONDS = 10
MAX_ATTEMPTS = 3
RETRY_DELAYS_SECONDS = (1, 2)  # waits after attempt 1 and attempt 2
RETRYABLE_STATUS_CODES = (422, 429)
SECONDS_PER_DAY = 86400

# ── Subreddit name rules ──────────────────────────────────────────────────────
# Matches discovery.MAX_COMMUNITIES: a manual pick may not read more communities
# than automatic discovery would.
MAX_SUBREDDITS = 4
MIN_SUBREDDIT_LENGTH = 3
MAX_SUBREDDIT_LENGTH = 21
MAX_SUGGESTIONS = 3
SUGGESTION_FETCH_LIMIT = 10
# A typo past the first few characters ("skincareaddictoin") has no useful prefix, so
# suggestions fall back to a stem of the name.
SUGGESTION_STEM_LENGTH = 6

_PREFIX_PATTERN = re.compile(
    r"^(https?://)?(www\.|old\.|new\.)?(reddit\.com)?/?(r/)?", re.IGNORECASE
)
_VALID_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
_DELETED_BODIES = {"[removed]", "[deleted]", ""}

# User-facing messages (complete sentences, no status codes, no URLs).
_UNAVAILABLE = "The Reddit data service is temporarily unavailable. Please try again in a moment."


class SubredditCheck(NamedTuple):
    """Result of a subreddit existence check.

    exists      True when the community was found (or when the check was skipped).
    name        Canonical casing to use from here on.
    suggestions Prefix matches to offer the user when `exists` is False.
    checked     False when the lookup itself failed and validation was skipped
                (fail-open), so callers can avoid claiming the name is verified.
    """

    exists: bool
    name: str
    suggestions: List[str] = []
    checked: bool = True


# ── Input normalisation ───────────────────────────────────────────────────────
def normalize_subreddit(raw: str) -> str:
    """Turn any way a person might write a subreddit into a bare name.

    Accepts "skincareaddiction", "r/SkincareAddiction", "/R/x",
    "https://www.reddit.com/r/x/comments/...". Raises ValueError with a
    human-readable message when the result cannot be a subreddit name.
    """
    if raw is None:
        raise ValueError("Please enter a subreddit name.")

    text = str(raw).strip()
    if not text:
        raise ValueError("Please enter a subreddit name.")

    text = _PREFIX_PATTERN.sub("", text, count=1)
    text = text.split("/")[0]          # drop anything after the community name
    text = text.split("?")[0].strip().strip("/").strip()

    if not text:
        raise ValueError(f"\"{str(raw).strip()}\" does not contain a subreddit name.")
    if not _VALID_NAME_PATTERN.match(text):
        raise ValueError(
            f"\"{text}\" is not a valid subreddit name. Use just the community "
            "name, for example skincareaddiction."
        )
    if not (MIN_SUBREDDIT_LENGTH <= len(text) <= MAX_SUBREDDIT_LENGTH):
        raise ValueError(
            f"\"{text}\" is not a valid subreddit name. Subreddit names are "
            f"between {MIN_SUBREDDIT_LENGTH} and {MAX_SUBREDDIT_LENGTH} characters long."
        )
    return text


def parse_subreddit_input(raw: str) -> List[str]:
    """Split a comma-separated field into up to MAX_SUBREDDITS clean names."""
    if raw is None or not str(raw).strip():
        raise ValueError("Please enter at least one subreddit.")

    names: List[str] = []
    seen = set()
    for chunk in str(raw).split(","):
        if not chunk.strip():
            continue
        name = normalize_subreddit(chunk)
        if name.lower() not in seen:
            seen.add(name.lower())
            names.append(name)

    if not names:
        raise ValueError("Please enter at least one subreddit.")
    if len(names) > MAX_SUBREDDITS:
        raise ValueError(
            f"Please choose {MAX_SUBREDDITS} communities or fewer. You entered {len(names)}."
        )
    return names


# ── HTTP plumbing ─────────────────────────────────────────────────────────────
def _request_page(url: str, params: dict, timeout: int = REQUEST_TIMEOUT_SECONDS) -> requests.Response:
    """Issue a single request, retrying transient failures with short backoff.

    Retries timeouts, connection errors, 5xx, 422 and 429. Returns immediately
    for every other response (including 400/401/403/404) so the caller can turn
    them into a friendly message. Raises requests.RequestException when all
    attempts are exhausted.
    """
    last_exc: Optional[Exception] = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            resp = requests.get(url, params=params, timeout=timeout)
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_exc = exc
        else:
            if resp.status_code not in RETRYABLE_STATUS_CODES and resp.status_code < 500:
                return resp
            last_exc = requests.HTTPError(f"transient status {resp.status_code}")

        if attempt < MAX_ATTEMPTS - 1:
            time.sleep(RETRY_DELAYS_SECONDS[attempt])

    raise last_exc if last_exc else requests.RequestException("request failed")


def _cutoff_timestamp(days: int) -> int:
    return int(time.time() - days * SECONDS_PER_DAY)


def _format_date(created_utc) -> str:
    try:
        return datetime.fromtimestamp(int(created_utc), tz=timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def _raise_for_client_error(resp: requests.Response, subreddit: str) -> None:
    if resp.status_code in (400, 404):
        raise ValueError(f"We could not find a community called r/{subreddit}.")
    if resp.status_code in (401, 403):
        raise ValueError(f"r/{subreddit} is private or restricted, so it cannot be analysed.")


def _clean_text(value) -> str:
    return (str(value) if value is not None else "").strip()


def _is_deleted(text: str) -> bool:
    return text.strip().lower() in _DELETED_BODIES


# ── Validation ────────────────────────────────────────────────────────────────
def _search_subreddits(params: dict) -> List[dict]:
    """Raises requests.RequestException / ValueError so callers can fail open."""
    resp = _request_page(
        SUBREDDITS_ENDPOINT, dict(params, limit=SUGGESTION_FETCH_LIMIT),
        timeout=VALIDATION_TIMEOUT_SECONDS,
    )
    if resp.status_code >= 400:
        raise requests.HTTPError("lookup unavailable")
    results = resp.json().get("data") or []
    return [entry for entry in results if isinstance(entry, dict)]


def _suggestions_for(name: str) -> List[str]:
    """Popular public communities starting with the name, or with a stem of it.

    `subreddit` is an exact match on this API; `subreddit_prefix` is the prefix
    search. Results arrive ordered by subscriber count, so the first few are the
    ones a user is most likely to have meant.
    """
    queries = [name]
    if len(name) > SUGGESTION_STEM_LENGTH:
        queries.append(name[:SUGGESTION_STEM_LENGTH])

    for query in queries:
        try:
            results = _search_subreddits({"subreddit_prefix": query})
        except (requests.RequestException, ValueError):
            return []
        names = [
            _clean_text(entry.get("display_name"))
            for entry in results
            if entry.get("subreddit_type") in (None, "public", "restricted")
            and not entry.get("over18")
            and _clean_text(entry.get("display_name"))
            and _clean_text(entry.get("display_name")).lower() != name.lower()
        ]
        if names:
            return names[:MAX_SUGGESTIONS]
    return []


def validate_subreddit(name: str) -> SubredditCheck:
    """Check a community exists, because the archive answers 200 + empty for typos.

    Fails OPEN: if the lookup itself breaks, return exists=True/checked=False and
    let the fetch decide, rather than blocking the user on our own outage.
    """
    try:
        results = _search_subreddits({"subreddit": name})
    except (requests.RequestException, ValueError) as exc:
        logger.warning("subreddit validation skipped for %s: %s", name, exc)
        return SubredditCheck(exists=True, name=name, suggestions=[], checked=False)

    for entry in results:
        display = _clean_text(entry.get("display_name"))
        if display and display.lower() == name.lower():
            return SubredditCheck(exists=True, name=display, suggestions=[], checked=True)

    return SubredditCheck(
        exists=False, name=name, suggestions=_suggestions_for(name), checked=True
    )


def lookup_subreddit(name: str) -> Optional[dict]:
    """Everything discovery needs to judge a candidate community in one lookup.

    `validate_subreddit` answers a yes/no question and deliberately fails open, which
    hides the difference between "this community does not exist" and "we could not
    check". Discovery has to tell those apart (the first means drop the candidate, the
    second means trust the model and let the fetch decide) and it also needs the
    subscriber count and the private/over-18 flags. So this returns the metadata,
    returns None for a name the archive does not know, and raises RuntimeError when
    the lookup service itself failed.
    """
    try:
        results = _search_subreddits({"subreddit": name})
    except (requests.RequestException, ValueError) as exc:
        logger.warning("subreddit lookup failed for %s: %s", name, exc)
        raise RuntimeError(_UNAVAILABLE)

    for entry in results:
        display = _clean_text(entry.get("display_name"))
        if not display or display.lower() != name.lower():
            continue
        try:
            subscribers = int(entry.get("subscribers") or 0)
        except (TypeError, ValueError):
            subscribers = 0
        return {
            "name": display,
            "subscribers": max(0, subscribers),
            "over18": bool(entry.get("over18")),
            # A missing type means the archive did not record one; treat that as
            # public, the same reading `_suggestions_for` takes.
            "public": entry.get("subreddit_type") in (None, "public", "restricted"),
        }

    return None


# ── Fetching ──────────────────────────────────────────────────────────────────
def fetch_posts(subreddit: str, days: int = FETCH_WINDOW_DAYS) -> List[dict]:
    """Newest-first posts from the last `days` days, deduped, deleted ones dropped."""
    cutoff = _cutoff_timestamp(days)
    posts: List[dict] = []
    seen_ids = set()
    before = None

    for page in range(MAX_POST_PAGES):
        params = {"subreddit": subreddit, "limit": PAGE_SIZE, "after": cutoff, "sort": "desc"}
        if before is not None:
            params["before"] = before

        try:
            resp = _request_page(POSTS_ENDPOINT, params)
        except requests.RequestException as exc:
            # Keep partial results rather than throwing away good pages.
            logger.warning("post page %s failed for r/%s: %s", page, subreddit, exc)
            if page == 0:
                raise RuntimeError(_UNAVAILABLE)
            break

        _raise_for_client_error(resp, subreddit)
        if resp.status_code >= 400:
            if page == 0:
                raise RuntimeError(_UNAVAILABLE)
            break

        try:
            data = resp.json().get("data") or []
        except ValueError:
            if page == 0:
                raise RuntimeError(_UNAVAILABLE)
            break

        if not data:
            break

        for raw in data:
            if not isinstance(raw, dict):
                continue
            permalink = _clean_text(raw.get("permalink"))
            post_id = _clean_text(raw.get("id")) or permalink
            if not post_id or post_id in seen_ids:
                continue

            title = _clean_text(raw.get("title"))
            body = _clean_text(raw.get("selftext"))
            if _is_deleted(body) and not title:
                continue

            seen_ids.add(post_id)
            posts.append(
                {
                    "kind": "post",
                    "id": post_id,
                    "subreddit": _clean_text(raw.get("subreddit")) or subreddit,
                    "title": title,
                    "body": "" if _is_deleted(body) else body,
                    "score": int(raw.get("score") or 0),
                    "comments": int(raw.get("num_comments") or 0),
                    "url": f"https://reddit.com{permalink}" if permalink else "",
                    "created_utc": int(raw.get("created_utc") or 0),
                    "date": _format_date(raw.get("created_utc")),
                }
            )

        if len(data) < PAGE_SIZE:
            break
        before = data[-1].get("created_utc")
        if not before:
            break

    logger.info("fetched %s posts from r/%s (%s days)", len(posts), subreddit, days)
    return posts


def fetch_comments(
    subreddit: str, days: int = FETCH_WINDOW_DAYS, limit: int = DEFAULT_COMMENT_LIMIT
) -> List[dict]:
    """Newest-first comments from the last `days` days, deduped, deleted ones dropped."""
    cutoff = _cutoff_timestamp(days)
    comments: List[dict] = []
    seen_ids = set()
    before = None

    for page in range(MAX_COMMENT_PAGES):
        if len(comments) >= limit:
            break

        params = {"subreddit": subreddit, "limit": PAGE_SIZE, "after": cutoff, "sort": "desc"}
        if before is not None:
            params["before"] = before

        try:
            resp = _request_page(COMMENTS_ENDPOINT, params)
        except requests.RequestException as exc:
            # Comments are supporting evidence: never fail the whole run for them.
            logger.warning("comment page %s failed for r/%s: %s", page, subreddit, exc)
            break

        if resp.status_code >= 400:
            logger.warning("comment page %s returned %s for r/%s", page, resp.status_code, subreddit)
            break

        try:
            data = resp.json().get("data") or []
        except ValueError:
            break

        if not data:
            break

        for raw in data:
            if not isinstance(raw, dict):
                continue
            comment_id = _clean_text(raw.get("id"))
            permalink = _clean_text(raw.get("permalink"))
            key = comment_id or permalink
            if not key or key in seen_ids:
                continue

            body = _clean_text(raw.get("body"))
            if _is_deleted(body):
                continue

            seen_ids.add(key)
            comments.append(
                {
                    "kind": "comment",
                    "id": comment_id,
                    "subreddit": _clean_text(raw.get("subreddit")) or subreddit,
                    "title": "",
                    "body": body,
                    "score": int(raw.get("score") or 0),
                    "url": f"https://reddit.com{permalink}" if permalink else "",
                    "created_utc": int(raw.get("created_utc") or 0),
                    "date": _format_date(raw.get("created_utc")),
                }
            )

        if len(data) < PAGE_SIZE:
            break
        before = data[-1].get("created_utc")
        if not before:
            break

    comments.sort(key=lambda c: c.get("created_utc", 0), reverse=True)
    logger.info("fetched %s comments from r/%s (%s days)", len(comments), subreddit, days)
    return comments[:limit]
