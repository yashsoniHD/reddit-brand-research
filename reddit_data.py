"""Data layer for Reddit Brand Research.

This is the ONLY module that knows where the Reddit data comes from. It currently
reads the public Arctic Shift archive. To swap the source, reimplement the
public functions below and keep their return shapes identical:

    normalize_subreddit(raw)                                    -> str
    parse_subreddit_input(raw)                                  -> list[str]
    lookup_subreddit(name)                                      -> Optional[dict]
    fetch_posts(subreddit, days, deadline=None, *, phrases=None)    -> list[dict]
    fetch_comments(subreddit, days, limit, deadline=None,
                    *, phrases=None)                             -> list[dict]

Nothing above this layer imports `requests` or knows an endpoint URL.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from typing import List, Optional

import requests

import keywords

logger = logging.getLogger(__name__)

# ── Source configuration ──────────────────────────────────────────────────────
_API_ROOT = "https://arctic-shift.photon-reddit.com/api"
POSTS_ENDPOINT = f"{_API_ROOT}/posts/search"
COMMENTS_ENDPOINT = f"{_API_ROOT}/comments/search"
SUBREDDITS_ENDPOINT = f"{_API_ROOT}/subreddits/search"

SOURCE_NAME = "Arctic Shift archive"

# ── Request tuning ────────────────────────────────────────────────────────────
# The default and the maximum: a run reads the last 30 days unless the caller asks
# for a shorter range in the advanced settings (pipeline.TIME_WINDOWS), and the
# selection layer decides what is worth analysing out of whatever that returns.
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

# Text-search parameter names, verified against the live archive: these are
# exactly the endpoint knowledge this layer's docstring says stays here.
POSTS_TEXT_PARAM = "query"      # matches title and selftext, AND of words
# Verified live 2026-07-29: GET /api/posts/search?query=meta%20ads returns 200
# and matches across both title and selftext with AND-of-words semantics.
# `body=` on posts is rejected with 400 Unknown query parameter: 'body'.
# `body=` on comments is accepted: the probe got a 422 transient timeout back,
# not a 400, which means the parameter itself is recognised.
COMMENTS_TEXT_PARAM = "body"    # posts reject `body=` with 400 Unknown query parameter
MAX_TEXT_PARAM_CHARS = 120
PHRASE_MAX_POST_PAGES = 3
PHRASE_MAX_COMMENT_PAGES = 2

# ── Subreddit name rules ──────────────────────────────────────────────────────
# Matches discovery.MAX_COMMUNITIES: a manual pick may not read more communities
# than automatic discovery would.
MAX_SUBREDDITS = 4
MIN_SUBREDDIT_LENGTH = 3
MAX_SUBREDDIT_LENGTH = 21
LOOKUP_FETCH_LIMIT = 10

_PREFIX_PATTERN = re.compile(
    r"^(https?://)?(www\.|old\.|new\.)?(reddit\.com)?/?(r/)?", re.IGNORECASE
)
_VALID_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
_DELETED_BODIES = {"[removed]", "[deleted]", ""}

# User-facing messages (complete sentences, no status codes, no URLs).
_UNAVAILABLE = "The Reddit data service is temporarily unavailable. Please try again in a moment."


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
def _request_page(
    url: str,
    params: dict,
    timeout: int = REQUEST_TIMEOUT_SECONDS,
    deadline: Optional[float] = None,
) -> requests.Response:
    """Issue a single request, retrying transient failures with short backoff.

    Retries timeouts, connection errors, 5xx, 422 and 429. Returns immediately
    for every other response (including 400/401/403/404) so the caller can turn
    them into a friendly message. Raises requests.RequestException when all
    attempts are exhausted.

    `deadline` is checked before each retry (never before the first attempt,
    since an empty result is worse than a partial one): today the caller's
    deadline is only checked between pages, so one unresponsive community can
    burn MAX_POST_PAGES x MAX_ATTEMPTS x REQUEST_TIMEOUT_SECONDS of the run's
    budget on retries that were never going to finish in time anyway. Text
    queries 422 far more often, which makes that routine rather than
    theoretical.
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
            if _out_of_time(deadline):
                logger.info("stopping retries: out of time")
                break
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


def _safe_int(value, default: int = 0) -> int:
    """Every number in an archive record, coerced without ever raising.

    The archive is a community mirror of Reddit, not a typed API: a score arrives as
    null, a string, a float or missing entirely depending on when the row was
    captured. `int(raw.get("score") or 0)` looked total and was not, and one
    stringly-typed field on one post used to take down a whole fetch.
    """
    if isinstance(value, bool) or value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        pass
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def _safe_float(value, default: float = 0.0) -> float:
    """Same never-raises contract as `_safe_int`, needed for `upvote_ratio`.

    The archive is a community mirror, not a typed API: `upvote_ratio` arrives
    as `None`, a string like "0.93", a plain float, or missing entirely
    depending on when the row was captured.
    """
    if isinstance(value, bool) or value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _is_deleted(text: str) -> bool:
    return text.strip().lower() in _DELETED_BODIES


def _text_params(phrases: Optional[List[str]], field: str) -> dict:
    """Best-effort archive-side prefilter; correctness lives in the post-filter.

    Joins every phrase's words with spaces: the archive tokenises quote
    characters away regardless, so sending them would be pointless. If the
    joined string would exceed MAX_TEXT_PARAM_CHARS, send only the first
    phrase's words instead. That is safe by construction: a narrower-than-
    requested prefilter can only return a superset of what a full prefilter
    would, and `keywords.matches_all` still re-checks every accepted phrase
    against every returned item, so shrinking the prefilter can only ever
    cost extra rows to filter out, never a wrong result.
    """
    if not phrases:
        return {}
    joined = " ".join(word for phrase in phrases for word in phrase.split())
    if len(joined) > MAX_TEXT_PARAM_CHARS:
        joined = " ".join(phrases[0].split())
    return {field: joined} if joined else {}


# ── Lookup ────────────────────────────────────────────────────────────────────
def _search_subreddits(params: dict) -> List[dict]:
    """Raises requests.RequestException / ValueError so callers can fail open."""
    resp = _request_page(
        SUBREDDITS_ENDPOINT, dict(params, limit=LOOKUP_FETCH_LIMIT),
        timeout=VALIDATION_TIMEOUT_SECONDS,
    )
    if resp.status_code >= 400:
        raise requests.HTTPError("lookup unavailable")
    results = resp.json().get("data") or []
    return [entry for entry in results if isinstance(entry, dict)]


def lookup_subreddit(name: str) -> Optional[dict]:
    """Everything discovery needs to judge a candidate community in one lookup.

    The archive answers 200 with an empty list for a name that does not exist, so
    existence has to be checked rather than assumed. Discovery has to tell "this
    community does not exist" apart from "we could not check" (the first means drop
    the candidate, the second means trust the model and let the fetch decide), and
    it also needs the subscriber count and the private/over-18 flags. So this
    returns the metadata, returns None for a name the archive does not know, and
    raises RuntimeError when the lookup service itself failed.
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
        return {
            "name": display,
            "subscribers": max(0, _safe_int(entry.get("subscribers"))),
            "over18": bool(entry.get("over18")),
            # A missing type means the archive did not record one; treat that as
            # public, the same reading `_suggestions_for` takes.
            "public": entry.get("subreddit_type") in (None, "public", "restricted"),
        }

    return None


# ── Fetching ──────────────────────────────────────────────────────────────────
def _out_of_time(deadline: Optional[float]) -> bool:
    """True once the caller's clock has run out.

    The deadline is an absolute time.monotonic() value, the same clock the pipeline
    uses, because a wall clock can step backwards mid-run.
    """
    return deadline is not None and time.monotonic() >= deadline


def fetch_posts(
    subreddit: str,
    days: int = FETCH_WINDOW_DAYS,
    deadline: Optional[float] = None,
    *,
    phrases: Optional[List[str]] = None,
) -> List[dict]:
    """Newest-first posts from the last `days` days, deduped, deleted ones dropped.

    `deadline` stops paging rather than the whole fetch: whatever pages already came
    back are still a usable sample, and four pages of a five page fetch is a far
    better answer than an error page.

    `phrases`, when given, is keyword-only and last so every existing positional
    call site is untouched. It adds a best-effort archive-side prefilter (see
    `_text_params`) and stamps every returned post with `phrase_match`
    (`keywords.matches_all` over its title and body) without dropping a single
    one: the key is absent entirely when no phrases are active, which is what
    lets a later stage fall back to the already-fetched pool for free.
    """
    cutoff = _cutoff_timestamp(days)
    posts: List[dict] = []
    seen_ids = set()
    before = None
    max_pages = PHRASE_MAX_POST_PAGES if phrases else MAX_POST_PAGES
    # Once True, a 400 retry has already dropped the text params for the rest
    # of this fetch (see the retry block below), so later pages go through
    # unfiltered even though `phrases` (used for the phrase_match stamp) never
    # changes.
    text_active = bool(phrases)
    text_params = _text_params(phrases, POSTS_TEXT_PARAM)

    for page in range(max_pages):
        if page > 0 and _out_of_time(deadline):
            logger.info("stopping r/%s after %s pages: out of time", subreddit, page)
            break

        params = {"subreddit": subreddit, "limit": PAGE_SIZE, "after": cutoff, "sort": "desc"}
        if before is not None:
            params["before"] = before
        if text_active and text_params:
            params.update(text_params)

        try:
            resp = _request_page(POSTS_ENDPOINT, params, deadline=deadline)
        except requests.RequestException as exc:
            # Keep partial results rather than throwing away good pages.
            logger.warning("post page %s failed for r/%s: %s", page, subreddit, exc)
            if page == 0:
                raise RuntimeError(_UNAVAILABLE)
            break

        if resp.status_code == 400 and text_active and text_params:
            # `_raise_for_client_error` maps 400 to "we could not find a
            # community called r/x". An unsupported text parameter would
            # otherwise surface as exactly that false claim about a healthy
            # community, so retry once for this same page with the text
            # params removed before that mapping is allowed to speak. The
            # post-filter still runs over the now-unfiltered pages, so a
            # persistently unsupported param just looks like "too few
            # matches" to the caller instead of a run failure.
            retry_params = {k: v for k, v in params.items() if k not in text_params}
            try:
                resp = _request_page(POSTS_ENDPOINT, retry_params, deadline=deadline)
            except requests.RequestException as exc:
                logger.warning("post page %s retry failed for r/%s: %s", page, subreddit, exc)
                if page == 0:
                    raise RuntimeError(_UNAVAILABLE)
                break
            text_active = False

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

            clean_body = "" if _is_deleted(body) else body
            seen_ids.add(post_id)
            item = {
                "kind": "post",
                "id": post_id,
                "subreddit": _clean_text(raw.get("subreddit")) or subreddit,
                "title": title,
                "body": clean_body,
                "score": _safe_int(raw.get("score")),
                "comments": _safe_int(raw.get("num_comments")),
                "url": f"https://reddit.com{permalink}" if permalink else "",
                "created_utc": _safe_int(raw.get("created_utc")),
                "date": _format_date(raw.get("created_utc")),
                # Public metadata the archive already returns and nothing above
                # this layer used until now. `author` is captured verbatim
                # (including the literal string "[deleted]", which is real
                # information: the account is gone) but this layer never
                # renders it; that is a caller-side privacy decision.
                "author": _clean_text(raw.get("author")),
                "author_flair_text": _clean_text(raw.get("author_flair_text")),
                "link_flair_text": _clean_text(raw.get("link_flair_text")),
                "upvote_ratio": min(1.0, max(0.0, _safe_float(raw.get("upvote_ratio")))),
            }
            if phrases:
                item["phrase_match"] = keywords.matches_all(title + "\n" + clean_body, phrases)
            posts.append(item)

        if len(data) < PAGE_SIZE:
            break
        before = data[-1].get("created_utc")
        if not before:
            break

    logger.info("fetched %s posts from r/%s (%s days)", len(posts), subreddit, days)
    return posts


def fetch_comments(
    subreddit: str,
    days: int = FETCH_WINDOW_DAYS,
    limit: int = DEFAULT_COMMENT_LIMIT,
    deadline: Optional[float] = None,
    *,
    phrases: Optional[List[str]] = None,
) -> List[dict]:
    """Newest-first comments from the last `days` days, deduped, deleted ones dropped.

    `phrases`, keyword-only and last for the same reason as `fetch_posts`, adds
    the same best-effort prefilter and `phrase_match` stamp. Matching uses the
    comment body only, since a comment has no title.
    """
    cutoff = _cutoff_timestamp(days)
    comments: List[dict] = []
    seen_ids = set()
    before = None
    max_pages = PHRASE_MAX_COMMENT_PAGES if phrases else MAX_COMMENT_PAGES
    text_active = bool(phrases)
    text_params = _text_params(phrases, COMMENTS_TEXT_PARAM)

    for page in range(max_pages):
        if len(comments) >= limit:
            break
        if page > 0 and _out_of_time(deadline):
            logger.info("stopping r/%s comments after %s pages: out of time", subreddit, page)
            break

        params = {"subreddit": subreddit, "limit": PAGE_SIZE, "after": cutoff, "sort": "desc"}
        if before is not None:
            params["before"] = before
        if text_active and text_params:
            params.update(text_params)

        try:
            resp = _request_page(COMMENTS_ENDPOINT, params, deadline=deadline)
        except requests.RequestException as exc:
            # Comments are supporting evidence: never fail the whole run for them.
            logger.warning("comment page %s failed for r/%s: %s", page, subreddit, exc)
            break

        if resp.status_code == 400 and text_active and text_params:
            # Same reasoning as fetch_posts: retry once for this page without
            # the text params before treating this as a plain failed page.
            retry_params = {k: v for k, v in params.items() if k not in text_params}
            try:
                resp = _request_page(COMMENTS_ENDPOINT, retry_params, deadline=deadline)
            except requests.RequestException as exc:
                logger.warning("comment page %s retry failed for r/%s: %s", page, subreddit, exc)
                break
            text_active = False

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
            item = {
                "kind": "comment",
                # The dedupe key, not the raw id: a comment the archive gave no
                # id for used to be stored with id "", and every such comment
                # then shared one relevance rating and one identity downstream.
                "id": key,
                "subreddit": _clean_text(raw.get("subreddit")) or subreddit,
                "title": "",
                "body": body,
                "score": _safe_int(raw.get("score")),
                "url": f"https://reddit.com{permalink}" if permalink else "",
                "created_utc": _safe_int(raw.get("created_utc")),
                "date": _format_date(raw.get("created_utc")),
                "author": _clean_text(raw.get("author")),
                "author_flair_text": _clean_text(raw.get("author_flair_text")),
                # No link_flair_text and no upvote_ratio: a comment has
                # neither on Reddit, and writing ""/0.0 for them would be
                # fiction rather than a genuinely missing value.
            }
            if phrases:
                item["phrase_match"] = keywords.matches_all(body, phrases)
            comments.append(item)

        if len(data) < PAGE_SIZE:
            break
        before = data[-1].get("created_utc")
        if not before:
            break

    comments.sort(key=lambda c: c.get("created_utc", 0), reverse=True)
    logger.info("fetched %s comments from r/%s (%s days)", len(comments), subreddit, days)
    return comments[:limit]
