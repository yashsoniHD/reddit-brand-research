# Arctic Shift API reference

Research notes for the archive behind `reddit_data.py`. Compiled 2026-07-30 from the
official docs plus ~60 live probes against production. Where the two disagree, the
probe result is recorded and the doc claim is marked stale.

- Base: `https://arctic-shift.photon-reddit.com/api`
- Status: `https://status.arctic-shift.photon-reddit.com`
- Docs: <https://github.com/ArthurHeitmann/arctic_shift/blob/master/api/README.md>
- UI: `/search`, `/download-tool`, `/live-charts`
- Maintainer: u/RaiderBDev (Discord `raiderbv`, arctic.shift.contact@gmail.com)
- No auth, no API key, no signup. `Access-Control-Allow-Origin: *`, so it is
  callable straight from a browser. Served by Express behind Cloudflare.
- Stated guarantee: "No uptime or performance guarantees :)"

## Response envelope

Every response, success or failure, is `{"data": ..., "error": ...}`.

```json
{"data": [ {...}, {...} ], "error": null}
{"data": null, "error": "'query' query parameter requires one of: author, subreddit"}
```

`data` is a list for search endpoints, a list of `{kind, data}` nodes for
`/comments/tree`, and a list of `{date, value}` rows for `/time_series`.

## Endpoint surface

### Posts and comments

| Endpoint | Purpose |
| --- | --- |
| `/posts/search`, `/comments/search` | The workhorses. Filter + paginate. |
| `/posts/ids`, `/comments/ids` | Batch lookup by ID, max 500 per call. |
| `/comments/tree` | Nested comment tree for one post. |
| `/posts/search/aggregate`, `/comments/search/aggregate` | Group by date, author or subreddit. |

Shared search parameters:

| Param | Type | Default | Notes |
| --- | --- | --- | --- |
| `author` | string | | `u/` prefix ignored |
| `subreddit` | string | | `r/` prefix ignored |
| `author_flair_text` | string | | keyword search |
| `after`, `before` | Date | | on `created_utc`, **both exclusive** |
| `limit` | 1-100 or `auto` | 25 | `auto` returns 100-1000 |
| `sort` | `asc` \| `desc` | | by `created_utc` only |
| `md2html` | bool | false | adds `selftext_html` / `body_html` |
| `fields` | csv | | projection; see the gotcha below |

Posts only: `title`, `selftext`, `query` (title + selftext), `link_flair_text`,
`url` (prefix match), `url_exact`, `over_18`, `spoiler`, `crosspost_parent_id`.

Comments only: `body`, `link_id` (post ID), `parent_id` (comment ID; empty string
means top-level only).

`/comments/tree` takes `link_id` (required), `parent_id`, `limit` (1-25000,
default 50), `start_breadth`, `start_depth`, `md2html`. Over the limit it emits
`{"kind": "more"}` nodes with a children array, the same shape as Reddit's own
listing. Probe: a post with `num_comments=31` gave 32 rows from
`/comments/search?link_id=...` but 19 nodes from `/comments/tree` at
`limit=9999` - the tree counts differently, so prefer `search` for a flat sample.

Aggregations take `aggregate` (`created_utc` | `author` | `subreddit`),
`frequency` (required with `created_utc`), `limit`, `min_count`, `sort`, plus every
search filter. **In practice these 422 often** - 2 of 2 probes failed
(`aggregate=author` over 30 days, `aggregate=created_utc` over 24 months). Treat
them as opportunistic.

### Subreddits

| Endpoint | Notes |
| --- | --- |
| `/subreddits/search` | `subreddit`, `subreddit_prefix`, `min_subscribers`, `max_subscribers`, `over18`, `after`, `before`, `limit` (1-1000), `sort`, `sort_type` (`created_utc` \| `subscribers` \| `subreddit`) |
| `/subreddits/ids` | batch by ID |
| `/subreddits/rules` | `subreddits` csv, max 1000 |
| `/subreddits/wikis` | `paths` csv (max 100) or `subreddit` + `limit` |
| `/subreddits/wikis/list` | `subreddit`, returns page paths |

### Users

| Endpoint | Notes |
| --- | --- |
| `/users/search` | aggregate profile data only: `author`, `author_prefix`, `min_num_posts`, `min_num_comments`, `active_since`, `min_karma`, `sort_type` (`author` \| `total_karma`) |
| `/users/ids` | batch by ID |
| `/users/interactions/users` | who a user interacts with, counts |
| `/users/interactions/users/list` | the individual interactions |
| `/users/interactions/subreddits` | where a user is active, with `weight_posts` / `weight_comments` |
| `/users/aggregate_flairs` | that user's flair per subreddit |

`/users/search` and `/subreddits/search` are refreshed infrequently; user IDs can
be `null` for inactive accounts.

### Misc

- `/short_links?paths=/r/x/s/AbC123` resolves Reddit share links. Case-sensitive, max 1000.
- `/time_series?key=...&precision=year|quarter|month|week|day|hour|minute`, with
  optional `after` / `before`. Keys: `global/{posts,comments}/{count,sum_score,sum_retrieved_after_seconds}`,
  `r/<sub>/{posts,comments}/{count,sum_score}`, `r/<sub>/subscribers`.

## Value formats

- **ID** - base36, bare or prefixed. `1v984ek` and `t3_1v984ek` both work on `link_id`.
- **Boolean** - `true/1/yes/y`, `false/0/no/n`.
- **Date** - epoch seconds, epoch millis, ISO 8601 (`2020-01-01`,
  `2020-01-01 00:00:00`, `2020-01-01T00:00:00.000Z`), or an offset from now:
  `1year`, `3m`, `2d`, `1hour`, `5min`, `10s`. The relative form is the cheapest
  way to express our rolling window.

## Coverage and freshness

| Fact | Value |
| --- | --- |
| Earliest post | 2005-06-23 (r/reddit.com) |
| Earliest comment | 2005-12-12 |
| `/time_series` monthly rows | 248, through the current month |
| Live ingest lag | **seconds**. r/AskReddit's newest comment was 24s old; `retrieved_on - created_utc` is 13-21s |
| Recent global volume | ~350M comments/month, ~430M posts in 2025 |
| Monthly dumps | GitHub releases, ~10th of the following month (`2026_06` published 2026-07-12), torrents via Academic Torrents |
| Dump formats | `.zst`, `.zst_blocks`, `.jsonl`/ndjson, `.json`; Python 3.10+ helper scripts in the repo |

Two ingest generations, distinguishable by `retrieved_on - created_utc`:

- **~20s** - Arctic Shift's own live ingest (roughly 2023 onward).
- **~70 days** - inherited from the Pushshift era. r/marketing posts from 2019
  showed a 5,951,114s delta; r/AskReddit 2019 showed 6,314,334s.

`retrieved_on` is never rewritten, so it marks provenance, not freshness.

## Records are captured at post time

This is the archive's whole value and its main trap. A post is captured ~20
seconds after creation, so `selftext` / `body` is the **original text**, preserved
even after the author deletes it or a mod removes it on live Reddit. But it also
means vote and reply counts are captured before they exist.

Probed score distributions in r/marketing and r/AskReddit:

| Age | Max score | Median | % with score <= 1 |
| --- | --- | --- | --- |
| < 2h | 1 | 1 | 100% |
| ~2d | 158 | 1 | 57% |
| ~30d (r/AskReddit) | 88 | 1 | 66% |
| 2019 (r/AskReddit) | 63 | 2 | 42% |

So a backfill pass **does** run - a 2-day-old post can carry score 158 - but it
leaves a long tail pinned at 0/1, and it does not touch `retrieved_on`. The docs
claim "after roughly 36 hours, all data is updated and should then be the same as
that released in .zst dumps"; the probes show that is optimistic for the tail.

**Consequence for ranking:** `score` and `num_comments` are near-useless for
anything under ~48h old and are a weak signal even at 30 days. `_meta` on
`/subreddits/search` and the `/time_series` volume keys are more reliable
popularity proxies than per-item score.

## Verified gotchas

### `before` and `after` are both exclusive

```
page 1 (desc, limit=5): 1vals5p@1785393596, 1val24n@1785391258, 1vakx6d@1785390819, 1vaksmw@1785390408, 1vaci11@1785367195
before=1785390819    -> 1vaksmw@1785390408, ... (1vakx6d absent)
after=1785367195,asc -> 1vaksmw@1785390408, ... (1vaci11 absent)
```

Our cursor pattern (`before = data[-1]["created_utc"]`, `reddit_data.py:452`)
therefore never duplicates. It does silently **skip any sibling sharing the
cursor item's exact second** - rare per-subreddit, but real on high-volume
communities.

### Keyword search requires a scope, and the error names the options

```
/posts/search?query=meta+ads       -> 400 "'query' query parameter requires one of: author, subreddit"
/posts/search?title=ads            -> 400 "'title' query parameter requires one of: author, subreddit"
/comments/search?body=meta+ads     -> 400 "'body' query parameter requires one of: author, subreddit, link_id, parent_id"
```

Note `link_id` and `parent_id` are valid scopes for `body` - that is the
precision path (see below). We always send `subreddit`, so we never hit the 400
for that reason; a 400 on a text query means something else.

### Comment `body` search is effectively unusable at subreddit scope

6 of 6 probes returned 422, across two subreddits, limits 10/100, and a 7-day
window. One took 16.97s before failing. Meanwhile the same subreddit's comments
fetch **without** `body` succeeds in ~1s.

The docs explain why: post keyword search is a simple fast index, comment search
is PostgreSQL FTS via `websearch_to_tsquery` and is slow.

Our `COMMENTS_TEXT_PARAM = "body"` prefilter (`reddit_data.py:63`) therefore
burns `MAX_ATTEMPTS` requests plus `RETRY_DELAYS_SECONDS` on nearly every phrase
run before the 400-retry path drops the param and we fall back to unfiltered
pages. Correctness is fine - `keywords.matches_all` re-checks everything - but the
cost is ~3 wasted requests and ~3s per comment page.

**The fix is `link_id`.** Fetch posts (where `query=` works), then fetch each
matching thread's comments by `link_id` with no text param:

```
/comments/search?link_id=1v984ek&limit=100   -> 200, 32 comments, 1.58s
/comments/search?link_id=1v984ek&body=the    -> 422
```

Precise, fast, and it returns comments the subreddit-wide window would have
missed. Adding `body` to a `link_id` query reintroduces the 422, so filter
client-side.

### 422 is load shedding, not a bad query

`{"error": "Timeout. Maybe slow down a bit"}`, usually returned in 0.7-1.2s , 
far too fast to be an elapsed timeout. It is non-deterministic:

```
subreddit=marketing&query=meta ads&limit=2&after=30d  -> 422, 422, 422
   ... a few minutes later, same request           -> 200, 200, 200
limit sweep, same query:  2:200  10:200  25:422  50:200  75:200  100:200
```

There is no limit threshold and no query-shape rule to discover here. Retry with
backoff, which is what `RETRYABLE_STATUS_CODES` already does. Narrowing with
`after` / `before` / `subreddit` / `author` helps on average, per the docs, but
will not make an expensive text query deterministic.

**Retries stack, so a text search needs a wall-clock bound.** A 422 usually
returns in ~1s, but the retry after it can be a slow success, and the pattern
compounds: one community measured 49.9s end to end as 422 (1.0s), backoff, 422,
longer backoff, then a ~13s success. Per-attempt timeouts do not bound that,
only a budget for the whole search does. `reddit_data.SEARCH_BUDGET_SECONDS`
exists for this, and with it eight communities searched serially came in at
76.6s for 700 posts, none over its share.

### `fields` rejects `permalink`

```
/posts/search?fields=id,title,score,created_utc,permalink -> 400 "'permalink' is not a valid field"
```

The default response is the **full Reddit API object** - ~110 keys on posts
(`all_awardings` … `wls`), ~65 on comments. `fields` projects down from that, but
its whitelist is narrower than the payload: `permalink`, `upvote_ratio`,
`subreddit_type`, `edited`, `ups` and the flair colour fields are all absent from
it. Since `reddit_data.py` builds item URLs from `permalink` and reads
`upvote_ratio`, we cannot adopt `fields` without reconstructing
`https://reddit.com/r/{subreddit}/comments/{id}/` ourselves.

`limit=auto` on r/marketing returned 233 posts in a 787KB body in 1.83s, so the
payload is worth trimming eventually - just not via `fields` as-is.

### `_meta` on subreddits is richer than we use

```json
"_meta": {
  "earliest_comment": 1208773010, "earliest_post": 1206296475,
  "num_comments": 1192449, "num_comments_updated_at": 1785110400,
  "num_posts": 278837,   "num_posts_updated_at": 1782728981
}
```

`lookup_subreddit` currently reads only `subscribers`, `over18` and
`subreddit_type`. `_meta.num_posts` / `num_comments` are a far better activity
signal than subscriber count for deciding whether a community will actually yield
a sample - subscriber count says nothing about posting rate. The rest of the
record also carries `restrict_posting`, `quarantine`, `submission_type` and
`accounts_active`.

## Rate limiting

Every response carries `X-RateLimit-Reset` (seconds until the window resets) and
`X-RateLimit-Reset-At` (epoch millis, aligned to minute boundaries - so windows
are per-minute). No remaining-quota header is exposed.

**25 back-to-back requests with zero delay produced no 429.** Limits are
computed dynamically from server load and query cost, so this is a floor, not a
ceiling - an expensive text query will exhaust budget far faster than
`/posts/search?limit=1`. On 429 the two reset headers tell you how long to wait.

The docs' guidance stands: "If you're a normal user and only make a couple
requests per second, you have nothing to worry about … If you want to process
massive amounts of data, use the monthly dumps instead."

## Full-text search syntax

Marked out of date in the docs, and subject to change.

| Form | Meaning |
| --- | --- |
| `Word1 Word2` | both words, any order |
| `"Word1 Word2"` | phrase, possibly with words between |
| `Word1 OR Word2` | either |
| `Word1 -Word2` | has Word1, excludes Word2 |

Posts use a simple fast keyword index; comments use PostgreSQL
`websearch_to_tsquery`. Quote characters are tokenised away on the post index,
which is why `_text_params` strips them rather than forwarding them. `OR` and
negation were accepted on `/posts/search` in probing, but given the
non-deterministic 422s we could not confirm the semantics are honoured rather
than merely parsed - client-side `keywords.matches_all` remains the source of
truth.

## Data removal

Removal requests go through a Google Form linked from the repo README; presence
can be checked by searching a username in the web UI.
