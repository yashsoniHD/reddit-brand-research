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
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import List, Optional, Sequence

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
# The ceiling on how far back a read can reach, and the longest range the advanced
# settings offer (pipeline.TIME_WINDOWS). It is no longer the default: that is
# pipeline.DEFAULT_DAYS, which is 7. Note this bounds `fetch_posts` only.
# `search_posts` is unbounded by design, because the most recent matches for a
# term are the right answer however old they turn out to be.
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
#
# Comments have no equivalent constant here. `body=` at subreddit scope is
# recognised (a 422, not a 400) but 6 of 6 live probes across two subreddits
# came back 422 anyway (docs/ARCTIC_SHIFT_API.md), and 422 is retryable, so
# sending it used to burn MAX_ATTEMPTS requests and their backoff before
# fetch_comments gave up with zero comments. There is nothing to gain from
# sending it, so `fetch_comments` never does: it fetches unfiltered pages from
# the first request and leaves matching to `keywords.matches_all` client-side.
MAX_TEXT_PARAM_CHARS = 120
PHRASE_MAX_POST_PAGES = 3
# Equal to MAX_COMMENT_PAGES, not lower: unlike posts, comments never get an
# archive-side text prefilter (see the note above), so phrase mode has to sift
# every comment client-side with keywords.matches_all. A lower ceiling here
# used to hand the mode that most needs raw material less of it than the
# unfiltered mode gets, which is backwards; there is nothing about phrase mode
# that costs more per page; the only extra cost is CPU-cheap client-side
# matching after the fetch.
PHRASE_MAX_COMMENT_PAGES = MAX_COMMENT_PAGES

# ── Search tuning ─────────────────────────────────────────────────────────────
# What this archive refuses is a BURST of text searches, not concurrency itself.
# The 2026-07-30 probe that produced the old "strictly serial" rule fired its
# requests simultaneously: six at once came back six 429s, three at once drew
# three, and the conclusion drawn was that `query=` cannot be parallelised at all.
#
# Re-probed 2026-08-02 with the starts spaced out instead of simultaneous, which
# is the variable the first probe never separated from concurrency:
#
#   2 workers, no spacing     2 requests, both 429 within 1.0s
#   2 workers, 1.0s spacing   4 requests, 3 OK, 22.8s
#   3 workers, 1.5s spacing   8 requests, 7 OK, 32.4s, no 429 at all
#   3 workers, 1.5s spacing   16 requests, 9 OK, 52.4s, the last 7 all 429
#   3 workers, 1.5s spacing   8 more, 45s later, 8 OK, 29.2s
#
# So there are two separate limits, and only one of them is about concurrency:
# starts closer together than about a second are refused outright, and a sustained
# run of more than roughly nine searches drains a budget that refills within a
# minute. Both are respected here rather than avoided by going serial: requests
# are issued SEARCH_WORKERS at a time with at least SEARCH_MIN_SPACING_SECONDS
# between any two starts, and the caller keeps the total per run under
# SEARCH_REQUEST_BUDGET (see pipeline.search).
#
# What this buys: the same eight communities that took 135.3s to search serially
# take about 32s this way, on a run that measured 219s end to end.
SEARCH_PAGE_SIZE = 100
# Terms per community per run. Each extra term is another request out of the
# per-run budget, which is why pipeline spends them breadth-first: every
# community's first term before any community's second.
MAX_SEARCH_TERMS = 2
# How many text searches may be in flight, and how far apart any two of them may
# start. Both numbers come from the probe table above: three workers cleared a
# full wave with no 429s, and a spacing below a second is what draws them.
SEARCH_WORKERS = 3
SEARCH_MIN_SPACING_SECONDS = 1.5
# Longer than REQUEST_TIMEOUT_SECONDS on purpose: an unbounded text query was
# measured at up to 17.8s, which a 15 second timeout would abandon just before it
# answered.
SEARCH_TIMEOUT_SECONDS = 20
# The wall clock one community's search may take, retries included. Without it a
# single community can eat the whole retrieval budget: measured live, one community
# took 49.9s as a 422 at 1.0s, a backoff, another 422, a longer backoff and finally
# a slow success. Eight communities have to fit inside the run's retrieval budget,
# so each gets a share and a community that cannot answer in its share is left to
# the fallback that reads communities in full.
SEARCH_BUDGET_SECONDS = 25
# Below this there is no point issuing the request at all; the budget is spent.
MIN_SEARCH_TIMEOUT_SECONDS = 4
# Fetching comments per post IS safe to parallelise: 6 posts, 203 comments, 2.6s
# at 3 workers, no 429. `link_id` accepts one id only (a comma-separated list is
# rejected with 400), so there is no batching to exploit here, only concurrency.
COMMENT_FETCH_WORKERS = 3
MAX_COMMENT_POSTS = 25
# Comments get a start pacer too, gentler than search's. That 2.6s measurement
# was taken in isolation; in a real run these 25 requests arrive immediately
# after a wave of searches, and the budget they are drawing on is shared. Sent
# unpaced in that position, 5 of 25 posts lost their replies to exhausted 429
# retries (637 comments in a measured run before, 360 after). Re-probed at half
# a second between starts: 24 of 24 posts answered, 433 comments, no refusals, in
# 13.8s, which is what the unpaced burst cost anyway once its retries were paid
# for. So this buys back the evidence rather than costing wall clock.
COMMENT_MIN_SPACING_SECONDS = 0.5

# ── Subreddit name rules ──────────────────────────────────────────────────────
# Matches discovery.MAX_COMMUNITIES: a manual pick may not read more communities
# than automatic discovery would. Kept as a literal (not imported from discovery)
# because discovery imports this module, not the other way around; a test asserts
# the two stay equal so they can't silently drift again.
MAX_SUBREDDITS = 8
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


# ── Search pacing ─────────────────────────────────────────────────────────────
# The gate that keeps two text searches from starting together. Process-wide and
# not per-call on purpose: the limit being respected belongs to the archive and
# to this deployment's address, not to any one run, so two runs sharing a worker
# instance have to share the gate as well or they would each independently
# believe they were spacing their requests correctly.
#
# Deliberately a floor on start times rather than a token bucket: what the probes
# refused was requests that STARTED together, and a start-time floor is the
# smallest thing that expresses exactly that. Threads block here rather than
# retrying, so a paced request costs its wait once instead of a 429 plus a
# backoff.
class _StartPacer:
    """Hands out permission to start, never closer together than `spacing`.

    `clock` and `sleep` are injectable so the pacing can be tested for what it
    actually does (how long it holds a caller) rather than by waiting out real
    seconds, and so the suite can run with a zero-spacing pacer without that
    being the only thing the tests ever see. A spacing of 0 makes `wait` a no-op
    beyond taking the lock.
    """

    def __init__(self, spacing: float, clock=time.monotonic, sleep=time.sleep):
        self._spacing = spacing
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._next_allowed = 0.0

    def wait(self) -> None:
        # The sleep happens outside the lock, so waiting threads queue on the
        # clock rather than on each other: holding it while sleeping would
        # serialise the whole pool behind one sleeper and turn the spacing into
        # a per-request delay for everybody.
        while True:
            with self._lock:
                now = self._clock()
                if now >= self._next_allowed:
                    self._next_allowed = now + self._spacing
                    return
                sleep_for = self._next_allowed - now
            self._sleep(sleep_for)


_search_pacer = _StartPacer(SEARCH_MIN_SPACING_SECONDS)
# A separate pacer, not a shared one: these two stages never overlap inside a run
# (the comment fetch starts once searching is done), and one gate at the search
# spacing would stretch 25 comment requests to nearly 40 seconds for a limit that
# tolerates them four times closer together.
_comment_pacer = _StartPacer(COMMENT_MIN_SPACING_SECONDS)


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


def _post_item(raw: dict, subreddit: str, phrases: Optional[List[str]]) -> Optional[dict]:
    """One archive post record as this layer's post shape, or None to skip it.

    Extracted from `fetch_posts` so `search_posts` normalises identically rather
    than growing a second copy that drifts. Returns None for a record with no
    usable identity, and for one whose body was already deleted at capture time
    and carried no title, which is the only case where nothing readable is left.
    """
    permalink = _clean_text(raw.get("permalink"))
    post_id = _clean_text(raw.get("id")) or permalink
    if not post_id:
        return None

    title = _clean_text(raw.get("title"))
    body = _clean_text(raw.get("selftext"))
    if _is_deleted(body) and not title:
        return None

    clean_body = "" if _is_deleted(body) else body
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
    return item


def _comment_item(raw: dict, subreddit: str, phrases: Optional[List[str]]) -> Optional[dict]:
    """One archive comment record as this layer's comment shape, or None to skip it.

    `subreddit` is only a fallback: a record fetched by `link_id` has no
    subreddit in the request, so the value comes off the record itself.
    """
    comment_id = _clean_text(raw.get("id"))
    permalink = _clean_text(raw.get("permalink"))
    key = comment_id or permalink
    if not key:
        return None

    body = _clean_text(raw.get("body"))
    if _is_deleted(body):
        return None

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
        # The thread this reply belongs to, which is what makes a comment
        # fetched by `link_id` traceable back to the post it is evidence for.
        "link_id": _clean_text(raw.get("link_id")),
        # No link_flair_text and no upvote_ratio: a comment has
        # neither on Reddit, and writing ""/0.0 for them would be
        # fiction rather than a genuinely missing value.
    }
    if phrases:
        item["phrase_match"] = keywords.matches_all(body, phrases)
    return item


def search_term_for_phrases(phrases: Optional[Sequence[str]]) -> str:
    """The one archive-side query string that stands in for a set of phrases.

    Joins every phrase's words with spaces: the archive tokenises quote
    characters away regardless, so sending them would be pointless. If the
    joined string would exceed MAX_TEXT_PARAM_CHARS, send only the first
    phrase's words instead. That is safe by construction: a narrower-than-
    requested prefilter can only return a superset of what a full prefilter
    would, and `keywords.matches_all` still re-checks every accepted phrase
    against every returned item, so shrinking the prefilter can only ever
    cost extra rows to filter out, never a wrong result.

    One string rather than one per phrase, because `query=` is an AND of its
    words and the post-filter is an AND of its phrases. Searching each phrase
    separately would union them, and half the results would satisfy only one of
    the phrases the reader asked for.
    """
    if not phrases:
        return ""
    joined = " ".join(word for phrase in phrases for word in phrase.split())
    if len(joined) > MAX_TEXT_PARAM_CHARS:
        joined = " ".join(str(phrases[0]).split())
    return joined


def _text_params(phrases: Optional[List[str]], field: str) -> dict:
    """Best-effort archive-side prefilter; correctness lives in the post-filter."""
    joined = search_term_for_phrases(phrases)
    return {field: joined} if joined else {}


# ── Lookup cache ──────────────────────────────────────────────────────────────
# `lookup_subreddit` is pure metadata (does the community exist, is it private,
# how many subscribers), never post or comment content, so caching it briefly is
# safe. Discovery now checks up to MAX_CANDIDATES candidates concurrently on
# every run and a manual pick re-checks whatever the reader typed, so the same
# name recurs within and across requests often enough that a short cache saves
# real round trips. Fifteen minutes is short enough that a community banned or
# created since is stale for one run at most, and long enough to absorb the
# repeats a single request produces.
LOOKUP_CACHE_TTL_SECONDS = 15 * 60
# A generous ceiling, not a tuned one: this process never runs enough distinct
# discovery queries between restarts for the cache to grow large on its own, so
# the cap exists purely to stop it growing without bound, not to bound typical
# usage. Single-flight (coalescing concurrent lookups of the same not-yet-cached
# name) is deliberately not attempted here: the duplicate-request cost it would
# save is small and already bounded by LOOKUP_WORKERS, and a wrong-but-simple
# cache is safer to ship than a subtly wrong locking scheme.
LOOKUP_CACHE_MAX_ENTRIES = 512
_CACHE_MISS = object()
_lookup_cache: dict = {}
_lookup_cache_lock = threading.Lock()


def _cache_get(name: str):
    """The cached `lookup_subreddit` result for `name`, or `_CACHE_MISS`.

    A sentinel rather than `None` marks a miss, because `None` is itself a valid
    cached answer: it means the archive does not know this name.

    Returns a shallow copy when the cached value is a dict, never the object
    itself: `lookup_subreddit`'s result is otherwise the exact dict every future
    call to this name returns, and a caller mutating what it thinks is its own
    result would silently poison the cache for everyone after it.
    """
    with _lookup_cache_lock:
        entry = _lookup_cache.get(name.lower())
    if entry is None:
        return _CACHE_MISS
    stamped_at, value = entry
    if time.monotonic() - stamped_at >= LOOKUP_CACHE_TTL_SECONDS:
        return _CACHE_MISS
    return dict(value) if isinstance(value, dict) else value


def _cache_set(name: str, value) -> None:
    """Stash `value` for `name`, then purge what has expired and cap the size.

    Both happen here, on every write, rather than on a timer or a background
    thread: a write is exactly when the cache is already being touched, and
    lookups happen in bursts (discovery checks up to MAX_CANDIDATES names
    concurrently), so there is no meaningful gap to schedule cleanup into
    instead. Without this, entries that expire between one run and the next
    would sit in the dict forever, since nothing ever deletes an expired key on
    its own, only refuses to return it.
    """
    now = time.monotonic()
    with _lookup_cache_lock:
        expired = [
            key for key, (stamped_at, _) in _lookup_cache.items()
            if now - stamped_at >= LOOKUP_CACHE_TTL_SECONDS
        ]
        for key in expired:
            del _lookup_cache[key]

        _lookup_cache[name.lower()] = (now, value)

        while len(_lookup_cache) > LOOKUP_CACHE_MAX_ENTRIES:
            oldest_key = min(_lookup_cache, key=lambda key: _lookup_cache[key][0])
            del _lookup_cache[oldest_key]


# ── Lookup ────────────────────────────────────────────────────────────────────
def _search_subreddits(params: dict, deadline: Optional[float] = None) -> List[dict]:
    """Raises requests.RequestException / ValueError so callers can fail open."""
    resp = _request_page(
        SUBREDDITS_ENDPOINT, dict(params, limit=LOOKUP_FETCH_LIMIT),
        timeout=VALIDATION_TIMEOUT_SECONDS,
        deadline=deadline,
    )
    if resp.status_code >= 400:
        raise requests.HTTPError("lookup unavailable")
    results = resp.json().get("data") or []
    return [entry for entry in results if isinstance(entry, dict)]


def lookup_subreddit(name: str, deadline: Optional[float] = None) -> Optional[dict]:
    """Everything discovery needs to judge a candidate community in one lookup.

    The archive answers 200 with an empty list for a name that does not exist, so
    existence has to be checked rather than assumed. Discovery has to tell "this
    community does not exist" apart from "we could not check" (the first means drop
    the candidate, the second means trust the model and let the fetch decide), and
    it also needs the subscriber count and the private/over-18 flags. So this
    returns the metadata, returns None for a name the archive does not know, and
    raises RuntimeError when the lookup service itself failed.

    Cached for LOOKUP_CACHE_TTL_SECONDS (see the cache block above); a service
    failure is never cached, so an outage gets retried on the very next call
    instead of being stuck "unavailable" for the rest of the window.

    `deadline`, an absolute time.monotonic() value exactly like `fetch_posts` and
    `fetch_comments` take (see `_out_of_time`), is passed all the way down to
    `_request_page` so a lookup issued near the end of a run's time budget cuts
    its own retries short instead of spending up to MAX_ATTEMPTS attempts and
    their backoff on a candidate the run no longer has time to read. Only
    retries are shortened this way, never the first attempt (see
    `_request_page`'s own docstring): an empty result is worse than a late one.
    """
    cached = _cache_get(name)
    if cached is not _CACHE_MISS:
        return cached

    try:
        results = _search_subreddits({"subreddit": name}, deadline=deadline)
    except (requests.RequestException, ValueError) as exc:
        logger.warning("subreddit lookup failed for %s: %s", name, exc)
        raise RuntimeError(_UNAVAILABLE)

    result = None
    for entry in results:
        display = _clean_text(entry.get("display_name"))
        if not display or display.lower() != name.lower():
            continue
        result = {
            "name": display,
            "subscribers": max(0, _safe_int(entry.get("subscribers"))),
            "over18": bool(entry.get("over18")),
            # A missing type means the archive did not record one; treat that as
            # public, the same reading `_suggestions_for` takes.
            "public": entry.get("subreddit_type") in (None, "public", "restricted"),
        }
        break

    _cache_set(name, result)
    # A copy, for the same reason `_cache_get` hands one out: `result` is the
    # exact object just stored in the cache, and returning it directly here
    # would let this very first, cache-populating call poison the cache just
    # as easily as a later cache hit would (see `_cache_get`'s docstring).
    return dict(result) if isinstance(result, dict) else result


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
            item = _post_item(raw, subreddit, phrases)
            if item is None or item["id"] in seen_ids:
                continue
            seen_ids.add(item["id"])
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

    `phrases`, keyword-only and last for the same reason as `fetch_posts`, stamps
    every comment with `phrase_match` (`keywords.matches_all` over the comment
    body, since a comment has no title) without narrowing what comes back.

    Unlike `fetch_posts`, this never sends an archive-side text prefilter.
    Comment search rejects `body=` at subreddit scope with a 422 on
    effectively every attempt (6 of 6 live probes, docs/ARCTIC_SHIFT_API.md),
    and 422 is retryable, so sending it used to cost MAX_ATTEMPTS requests and
    their backoff for a page that was always going to come back unfiltered
    anyway. Fetching unfiltered from the first request gets to the same place
    for a fraction of the cost; correctness does not depend on the prefilter,
    since `keywords.matches_all` re-checks every phrase against every returned
    comment regardless.
    """
    cutoff = _cutoff_timestamp(days)
    comments: List[dict] = []
    seen_ids = set()
    before = None
    max_pages = PHRASE_MAX_COMMENT_PAGES if phrases else MAX_COMMENT_PAGES

    for page in range(max_pages):
        if len(comments) >= limit:
            break
        if page > 0 and _out_of_time(deadline):
            logger.info("stopping r/%s comments after %s pages: out of time", subreddit, page)
            break

        params = {"subreddit": subreddit, "limit": PAGE_SIZE, "after": cutoff, "sort": "desc"}
        if before is not None:
            params["before"] = before

        try:
            resp = _request_page(COMMENTS_ENDPOINT, params, deadline=deadline)
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
            item = _comment_item(raw, subreddit, phrases)
            if item is None or item["id"] in seen_ids:
                continue
            seen_ids.add(item["id"])
            comments.append(item)

        if len(data) < PAGE_SIZE:
            break
        before = data[-1].get("created_utc")
        if not before:
            break

    comments.sort(key=lambda c: c.get("created_utc", 0), reverse=True)
    logger.info("fetched %s comments from r/%s (%s days)", len(comments), subreddit, days)
    return comments[:limit]


# ── Searching ─────────────────────────────────────────────────────────────────
def clean_search_terms(terms: Optional[Sequence[str]]) -> List[str]:
    """Up to MAX_SEARCH_TERMS usable terms, whitespace-collapsed and deduped.

    Public because discovery proposes the terms and the pipeline reports them, so
    both need the same answer about what a usable term is. Non-strings and blanks
    are dropped rather than coerced: a term is what goes into `query=`, and
    stringifying whatever the model returned would send nonsense to the archive.
    """
    cleaned: List[str] = []
    seen = set()
    for term in terms or []:
        if not isinstance(term, str):
            continue
        collapsed = " ".join(term.split())
        if not collapsed or collapsed.lower() in seen:
            continue
        seen.add(collapsed.lower())
        cleaned.append(collapsed)
        if len(cleaned) >= MAX_SEARCH_TERMS:
            break
    return cleaned


def search_posts(
    subreddit: str,
    terms: Sequence[str],
    limit: int = SEARCH_PAGE_SIZE,
    deadline: Optional[float] = None,
    *,
    phrases: Optional[List[str]] = None,
) -> List[dict]:
    """Posts in r/`subreddit` whose title or body matches any of `terms`.

    Deliberately unbounded in time. `sort=desc` with no `after` returns the most
    recent matches, so the window adjusts itself to how common the term is: a busy
    term came back spanning 20 days, a rare one 19 months, and both are the right
    answer for the term that produced them. A caller that wants a window should
    use `fetch_posts` instead, because that is the mode a window belongs to.

    One request per term, and every request waits for a start slot from the
    process-wide search pacer first, so several of these may run at once (the
    caller decides how many) without two of them ever starting together. See the
    SEARCH_PAGE_SIZE block above for what the archive actually refuses and the
    probe table behind these numbers. Returns [] rather than raising when the
    archive will not answer, so a caller can fall back to `fetch_posts` for this
    community; a run-ending failure would throw away the communities that did
    answer.

    Terms are OR-ed by issuing one request each and merging, not by sending
    "a OR b": the posts index accepts that syntax and does not honour it (probed
    2026-07-30, an OR of two terms that each match returns zero rows).

    Bounded by SEARCH_BUDGET_SECONDS whatever `deadline` says, because the caller
    has several communities to get through and no single one may spend the lot.
    The first term is always attempted, since an empty answer is worse than a
    partial one.
    """
    usable = clean_search_terms(terms)
    if not usable:
        return []

    # This community's own share of the clock, retries included, never past the
    # caller's deadline. Both the retry gate inside `_request_page` and each
    # attempt's timeout are sized from it, which is what turns "one slow community
    # can spend the whole retrieval budget" into a bounded cost.
    own_deadline = time.monotonic() + SEARCH_BUDGET_SECONDS
    if deadline is not None:
        own_deadline = min(deadline, own_deadline)

    posts: List[dict] = []
    seen_ids = set()

    for index, term in enumerate(usable):
        remaining = own_deadline - time.monotonic()
        if index > 0 and remaining < MIN_SEARCH_TIMEOUT_SECONDS:
            logger.info(
                "stopping search of r/%s after %s terms: %.1fs left",
                subreddit, index, remaining,
            )
            break

        params = {
            "subreddit": subreddit,
            "query": term,
            "limit": limit,
            "sort": "desc",
        }
        # Wait for a start slot before the clock is read for the timeout, so a
        # request that queued behind two others is not also handed a timeout
        # shortened by the time it spent queuing. The wait is bounded by the
        # pacing itself (SEARCH_MIN_SPACING_SECONDS per request ahead of this
        # one), which is why it is not itself deadline-checked: overrunning by a
        # second or two cannot happen here, and `own_deadline` below still cuts
        # off the request that follows.
        _search_pacer.wait()
        remaining = own_deadline - time.monotonic()
        try:
            resp = _request_page(
                POSTS_ENDPOINT, params,
                timeout=max(MIN_SEARCH_TIMEOUT_SECONDS,
                            min(SEARCH_TIMEOUT_SECONDS, remaining)),
                deadline=own_deadline,
            )
        except requests.RequestException as exc:
            logger.warning("search %r failed for r/%s: %s", term, subreddit, exc)
            continue

        if resp.status_code >= 400:
            # Not mapped through `_raise_for_client_error`: `query=` is only
            # accepted alongside a subreddit or author, so a 400 here is far more
            # likely to be about the term than about the community, and claiming
            # "we could not find a community called r/x" for a healthy community
            # is the exact false statement that mapping exists to avoid.
            logger.warning("search %r returned %s for r/%s", term, resp.status_code, subreddit)
            continue

        try:
            data = resp.json().get("data") or []
        except ValueError:
            logger.warning("search %r returned unreadable JSON for r/%s", term, subreddit)
            continue

        for raw in data:
            if not isinstance(raw, dict):
                continue
            item = _post_item(raw, subreddit, phrases)
            if item is None or item["id"] in seen_ids:
                continue
            seen_ids.add(item["id"])
            posts.append(item)

    posts.sort(key=lambda p: p.get("created_utc", 0), reverse=True)
    logger.info(
        "searched r/%s for %s: %s posts", subreddit, ", ".join(usable), len(posts)
    )
    return posts


def fetch_comments_for_posts(
    post_ids: Sequence[str],
    limit_per_post: int = PAGE_SIZE,
    deadline: Optional[float] = None,
    *,
    phrases: Optional[List[str]] = None,
) -> List[dict]:
    """Replies to the given posts, newest first, deduped, deleted ones dropped.

    The precise counterpart to `fetch_comments`: rather than sweeping a
    community's whole recent comment stream and hoping some of it belongs to the
    posts being analysed, this asks for the replies to those exact posts. Every
    comment it returns is evidence for a thread in the report.

    Parallel at COMMENT_FETCH_WORKERS, with starts spaced by the comment pacer:
    these run immediately after a wave of searches and draw on the same budget,
    so an unpaced burst here is what costs a run its replies (see the
    COMMENT_MIN_SPACING_SECONDS block). No `body=` filter is ever sent alongside
    `link_id`: that combination reintroduces the timeout that scoping by post
    exists to avoid.
    """
    ids = []
    seen_requested = set()
    for post_id in post_ids or []:
        key = _clean_text(post_id)
        if not key or key in seen_requested:
            continue
        seen_requested.add(key)
        ids.append(key)
        if len(ids) >= MAX_COMMENT_POSTS:
            break

    if not ids:
        return []

    def one(post_id: str) -> List[dict]:
        if _out_of_time(deadline):
            return []
        # Checked before the pacer as well as after: a request that has queued
        # behind two dozen others is exactly the one most likely to have run out
        # of time while waiting, and issuing it anyway would spend a request the
        # run can no longer use.
        _comment_pacer.wait()
        if _out_of_time(deadline):
            return []
        params = {"link_id": post_id, "limit": limit_per_post}
        try:
            resp = _request_page(COMMENTS_ENDPOINT, params, deadline=deadline)
        except requests.RequestException as exc:
            logger.warning("comments for %s failed: %s", post_id, exc)
            return []
        if resp.status_code >= 400:
            logger.warning("comments for %s returned %s", post_id, resp.status_code)
            return []
        try:
            data = resp.json().get("data") or []
        except ValueError:
            return []
        found = []
        for raw in data:
            if not isinstance(raw, dict):
                continue
            # No fallback subreddit: a link_id request never named one, so the
            # record's own value is the only truthful source.
            item = _comment_item(raw, "", phrases)
            if item is not None:
                found.append(item)
        return found

    comments: List[dict] = []
    seen_ids = set()
    with ThreadPoolExecutor(max_workers=COMMENT_FETCH_WORKERS) as pool:
        for found in pool.map(one, ids):
            for item in found:
                if item["id"] in seen_ids:
                    continue
                seen_ids.add(item["id"])
                comments.append(item)

    comments.sort(key=lambda c: c.get("created_utc", 0), reverse=True)
    logger.info("fetched %s comments across %s posts", len(comments), len(ids))
    return comments
