"""Community discovery: the model proposes subreddits, the archive vets them.

The user no longer picks communities, so something has to. A language model is good
at "where on Reddit do people talk about this" and bad at knowing whether the
community it just named actually exists, because models invent plausible-looking
subreddits constantly. The two jobs are therefore split: one model call proposes
and ranks candidates, then every name is normalised, deduped and checked against
the archive before it can reach the fetch stage.

Like the rest of the app above the data layer, nothing here imports `requests` or
knows an endpoint: lookups go through `reddit_data.lookup_subreddit` and the model
call goes through `analysis.chat_json`, so discovery inherits the same retry,
model-fallback and error-message behaviour as the two main calls.
"""

from __future__ import annotations

import logging
import time
from typing import Callable, List, Optional, Sequence

import analysis
import reddit_data

logger = logging.getLogger(__name__)

# ── Shape of the search ───────────────────────────────────────────────────────
MIN_CANDIDATES = 8
MAX_CANDIDATES = 12
# Raised from 4 with search-first retrieval. Search reads one page per term from a
# community instead of paging it out, so breadth is the better use of the budget
# than depth: eight communities searched costs less than four scraped and covers
# far more of the conversation. The retrieval budget, not this number, is what
# actually caps a run (see pipeline's fetching_time_left).
MAX_COMMUNITIES = 8
MAX_REASON_WORDS = 15
# Search terms proposed per community. Asking for a couple more than
# reddit_data.MAX_SEARCH_TERMS will use gives the sanitiser something to drop
# without leaving a community with no term at all.
MAX_TERMS_ASKED = 3
# Twelve candidates with a short reason each is roughly 600 tokens of JSON. The
# ceiling is set well above that because the failure mode is asymmetric: spare
# allowance costs nothing (only tokens actually emitted are billed), while coming up
# a few tokens short truncates the JSON mid-object and fails the whole run.
DISCOVERY_MAX_TOKENS = 4000

# User-facing messages (complete sentences, no status codes, no URLs).
_NOTHING_FOUND = (
    "We could not find any Reddit communities for that. Try describing what you "
    "are looking for in different words, or use plainer terms."
)


def _sanitize_terms(raw) -> List[str]:
    """The search terms one candidate proposed, or [] if it proposed nothing usable.

    A bare string is read as a single term rather than iterated, which is the one
    shape that would otherwise silently become a list of single characters. Markdown
    is stripped for the same reason it is stripped from `reason`: the model returns
    "**attribution**" often enough, and that matches nothing as a `query=` value.
    Length and count policy belong to the data layer, so `clean_search_terms` has
    the last word.
    """
    if isinstance(raw, str):
        entries = [raw]
    elif isinstance(raw, (list, tuple)):
        entries = list(raw)
    else:
        return []
    return reddit_data.clean_search_terms(
        [analysis.strip_markdown(term) for term in entries if isinstance(term, str)]
    )


def _sanitize_candidates(raw) -> List[dict]:
    """Trust nothing the model said: keep only names that could be real subreddits.

    The model happily returns "r/RealSub", duplicates in different casing, prose in
    the name field and markdown in the reason. `normalize_subreddit` is the same
    gate a hand-typed name passes through, so anything it rejects is dropped rather
    than guessed at.
    """
    raw = raw if isinstance(raw, dict) else {}

    candidates: List[dict] = []
    seen = set()
    for entry in raw.get("candidates") or []:
        if isinstance(entry, str):
            entry = {"name": entry, "reason": ""}
        if not isinstance(entry, dict):
            continue

        try:
            name = reddit_data.normalize_subreddit(entry.get("name"))
        except ValueError:
            logger.info("discovery dropped an unusable candidate name: %r", entry.get("name"))
            continue
        if name.lower() in seen:
            continue

        reason = analysis.strip_markdown(entry.get("reason"))
        words = reason.split()
        if len(words) > MAX_REASON_WORDS:
            reason = " ".join(words[:MAX_REASON_WORDS])

        seen.add(name.lower())
        candidates.append(
            {
                "name": name,
                "reason": reason,
                # Sanitised by the data layer, which owns what a usable `query=`
                # term is, and stripped of markdown first for the same reason the
                # reason field is: the model returns "**attribution**" often
                # enough that sending it as a search term would match nothing.
                "terms": _sanitize_terms(entry.get("terms")),
            }
        )
        if len(candidates) >= MAX_CANDIDATES:
            break

    return candidates


def _discovery_prompt(query: str, profile: str) -> str:
    profile_line = ""
    if profile:
        profile_line = "\nThe person asking describes themselves as: {}\n".format(profile)

    return """You are a Reddit researcher. Someone wants content ideas about this:

{query}
{profile_line}
Name the subreddits where people actually discuss this, ranked most relevant first.

Return ONLY a JSON object with exactly this key:

{{
  "candidates": [{{"name": "...", "reason": "...", "terms": ["...", "..."]}}]
}}

Rules:
- Give {min_candidates} to {max_candidates} candidates, best first. Only the first
  few will be read, so rank carefully.
- "name" is the bare community name only, with no "r/" prefix, no URL and no
  spaces. Only name communities you are confident exist and are active.
- Prefer communities where the topic is the point of the community, not large
  general-interest subreddits where it comes up occasionally.
- "reason" is at most {max_words} words, written for the person asking, saying what
  they will find there. For example: "Freelancers swap pricing and client-management
  advice here."
- "terms" is {max_terms} search terms that will be run against the text of posts in
  THAT community, best first. Write them the way its members would actually write
  them, not the way a marketer would. Every word in a term must appear in a post
  for it to match, so keep each term to one or two words: "attribution" and
  "conversion tracking" find threads, "b2b saas attribution challenges" finds
  nothing. Prefer the words people use when describing a problem.
- Do not include URLs, links, image syntax or markdown formatting in any string.
- Never use an em dash or an en dash in any string you return. Use a comma or
  start a new sentence instead.

No markdown fences, no commentary. JSON only.""".format(
        query=query,
        profile_line=profile_line,
        min_candidates=MIN_CANDIDATES,
        max_candidates=MAX_CANDIDATES,
        max_words=MAX_REASON_WORDS,
        max_terms=MAX_TERMS_ASKED,
    )


def _out_of_time(deadline: Optional[float]) -> bool:
    """True once the caller's clock has run out (absolute time.monotonic())."""
    return deadline is not None and time.monotonic() >= deadline


def discover(
    query: str,
    profile: str = "",
    *,
    chat: Optional[Callable] = None,
    lookup: Optional[Callable] = None,
    deadline: Optional[float] = None,
) -> dict:
    """Pick the communities this run will read, or raise ValueError with a message.

    Returns {"communities": [{"name", "reason", "subscribers", "checked", "terms"}],
             "considered": [name, ...]} where `considered` is every candidate the
    model proposed that survived name sanitisation, so the report can show the
    search that was actually run rather than only its winners.

    `terms` is what search-first retrieval runs against that community's post
    text. It comes from this same call rather than a second one because the terms
    worth searching depend on the community: the words r/PPC uses for a problem
    are not the words r/b2bmarketing uses for it. An empty list is a legitimate
    answer and means the caller should read the community rather than search it.

    `chat` and `lookup` are injection points, exactly like the callables
    `pipeline.run_report` takes, so tests and cached wrappers can substitute
    their own without this module importing either.

    `deadline` is an absolute time.monotonic() value. Past it, the remaining
    candidates are left unchecked rather than spending the run's last seconds on
    lookups for communities it will not have time to read.
    """
    chat = chat or analysis.chat_json
    lookup = lookup or reddit_data.lookup_subreddit

    text = (query or "").strip()
    if not text:
        raise ValueError(_NOTHING_FOUND)

    # Thinking off: naming the subreddits for a topic is recall and ranking, which the
    # model either knows or does not, and deliberation does not improve it. It also
    # keeps reasoning tokens from eating the output allowance this call is sized for.
    raw, model = chat(
        _discovery_prompt(text, (profile or "").strip()),
        DISCOVERY_MAX_TOKENS,
        "discovery",
        thinking=False,
    )
    candidates = _sanitize_candidates(raw)
    if not candidates:
        logger.warning("discovery returned no usable candidates")
        raise ValueError(_NOTHING_FOUND)
    logger.info("discovery proposed %s candidates with %s", len(candidates), model)

    communities: List[dict] = []
    attempted = 0
    unreachable = 0

    # The model ranked these, so its order is kept: the first ones that pass are the
    # ones that get read.
    for candidate in candidates:
        if len(communities) >= MAX_COMMUNITIES:
            break
        if _out_of_time(deadline):
            logger.warning(
                "out of time after %s candidates; keeping the %s already checked",
                attempted, len(communities),
            )
            break
        attempted += 1
        try:
            found = lookup(candidate["name"])
        except RuntimeError as exc:
            unreachable += 1
            logger.warning("could not check r/%s: %s", candidate["name"], exc)
            continue

        if not found:
            logger.info("discovery dropped r/%s: no such community", candidate["name"])
            continue
        if found.get("over18") or not found.get("public"):
            logger.info("discovery dropped r/%s: private or over-18", candidate["name"])
            continue

        communities.append(
            {
                "name": found.get("name") or candidate["name"],
                "reason": candidate["reason"],
                "subscribers": reddit_data._safe_int(found.get("subscribers")),
                "checked": True,
                "terms": list(candidate.get("terms") or []),
            }
        )

    if not communities and attempted and unreachable == attempted:
        # Fail OPEN, the same choice `validate_subreddit` makes: our own lookup being
        # down is not a reason to tell the user their idea has no communities. The
        # fetch stage will report anything that turns out not to exist.
        logger.warning("community lookup unavailable for every candidate; trusting the model")
        communities = [
            {
                "name": candidate["name"],
                "reason": candidate["reason"],
                "subscribers": 0,
                "checked": False,
                "terms": list(candidate.get("terms") or []),
            }
            for candidate in candidates[:MAX_COMMUNITIES]
        ]

    if not communities:
        raise ValueError(_NOTHING_FOUND)

    logger.info(
        "discovery selected %s of %s candidates: %s",
        len(communities), len(candidates), ", ".join(c["name"] for c in communities),
    )
    return {
        "communities": communities,
        "considered": [candidate["name"] for candidate in candidates],
    }


def manual(
    names: Sequence[str],
    *,
    lookup: Optional[Callable] = None,
    deadline: Optional[float] = None,
) -> dict:
    """A discovery result for communities the user picked themselves.

    Same shape as discover(), no model call. Each name is looked up so the report
    can show real subscriber counts, but a failed or empty lookup keeps the name
    rather than dropping it: the user chose it deliberately, and the fetch stage
    already turns a community that does not exist into a plain-language notice.
    """
    lookup = lookup or reddit_data.lookup_subreddit
    communities: List[dict] = []

    for name in names:
        subscribers = 0
        display = name
        checked = False
        found = None
        if _out_of_time(deadline):
            # The name still goes in: the user chose it, and the fetch stage is what
            # actually needs the time. Only the cosmetic lookup is skipped.
            logger.info("out of time; skipping the lookup for manually chosen r/%s", name)
        else:
            try:
                found = lookup(name)
            except RuntimeError as exc:
                logger.warning("could not check manually chosen r/%s: %s", name, exc)
                found = None
        if found:
            display = found.get("name") or name
            subscribers = reddit_data._safe_int(found.get("subscribers"))
            checked = True
        communities.append(
            {
                "name": display,
                "reason": "chosen by you",
                "subscribers": subscribers,
                "checked": checked,
                # Always empty, because this function makes no model call and so has
                # nothing to propose. That is not the same as "never search these":
                # a brief that quotes a phrase supplies its own terms downstream, and
                # the pipeline searches hand-picked communities for it. Present even
                # when empty so both discovery results have one shape and the
                # pipeline never has to ask which kind it holds.
                "terms": [],
            }
        )

    return {"communities": communities, "considered": list(names)}
