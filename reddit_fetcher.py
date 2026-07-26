import requests
import time
from datetime import datetime, timezone

BASE_URL = "https://arctic-shift.photon-reddit.com/api/posts/search"

MAX_ATTEMPTS = 3
RETRY_DELAYS = [1, 2]  # seconds to wait after attempt 1 and attempt 2
RETRYABLE_STATUS_CODES = (422,)


def _request_page(params: dict) -> requests.Response:
    """Issue a single page request, retrying on transient failures.

    Retries (with short backoff) on timeouts, connection errors, 5xx, and 422.
    Raises requests.RequestException if all attempts are exhausted.
    Returns immediately (no retry) on any other response, including 4xx
    errors like 400/404/403/401 which are handled by the caller.
    """
    last_exc = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            resp = requests.get(BASE_URL, params=params, timeout=10)
        except (requests.Timeout, requests.ConnectionError) as e:
            last_exc = e
        else:
            if resp.status_code not in RETRYABLE_STATUS_CODES and resp.status_code < 500:
                return resp
            last_exc = requests.HTTPError(f"{resp.status_code} error from Reddit data service")

        if attempt < MAX_ATTEMPTS - 1:
            time.sleep(RETRY_DELAYS[attempt])

    raise last_exc


def fetch_posts(subreddit: str, days: int = 7) -> list[dict]:
    cutoff = int(time.time() - days * 86400)
    posts = []
    before = None  # paginate backwards by passing oldest post's timestamp

    for page in range(5):  # max 5 pages = 500 posts
        params = {
            "subreddit": subreddit,
            "limit": 100,
            "after": cutoff,
            "sort": "desc",
        }
        if before:
            params["before"] = before

        try:
            resp = _request_page(params)
        except requests.RequestException:
            # Transient failure that survived retries. Keep whatever we've
            # already fetched rather than discarding it, unless this was
            # the very first page (in which case we have nothing to return).
            if page == 0:
                raise RuntimeError("Reddit data service is temporarily unavailable. Please try again.")
            break

        if resp.status_code in (400, 404):
            raise ValueError(f"Subreddit r/{subreddit} not found or has no recent posts.")
        if resp.status_code in (403, 401):
            raise ValueError(f"r/{subreddit} is private or restricted.")
        try:
            resp.raise_for_status()
        except requests.RequestException:
            if page == 0:
                raise RuntimeError("Reddit data service is temporarily unavailable. Please try again.")
            break

        data = resp.json().get("data", [])
        if not data:
            break

        for p in data:
            posts.append({
                "title": p.get("title", ""),
                "body": p.get("selftext", ""),
                "score": p.get("score", 0),
                "comments": p.get("num_comments", 0),
                "url": f"https://reddit.com{p.get('permalink', '')}",
                "date": datetime.fromtimestamp(p["created_utc"], tz=timezone.utc).strftime("%Y-%m-%d"),
            })

        if len(data) < 100:
            break  # no more pages

        before = data[-1]["created_utc"]  # oldest post timestamp → next page

    return posts
