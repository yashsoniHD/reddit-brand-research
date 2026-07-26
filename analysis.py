"""LLM layer: two Gemini calls, then everything the model says gets sanitised.

The split matters. Call 1 asks the model only to *label* items (which topic, which
sentiment) because that is what language models are good at. Call 2 asks it to
*write* about those labels. Every count, percentage and citation is computed in
stats.py from the labels, never taken from the model's prose.

The model runs on Vertex AI through the google-genai SDK, authenticated with
Application Default Credentials, so there is no API key to leak or rotate: whoever
ran `gcloud auth application-default login` on this machine is who calls the model.

`chat_json` is exported so discovery.py can make its own call through the same
retry, model-fallback and error-message machinery instead of reinventing it.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, List, Optional, Sequence, Tuple

from dotenv import load_dotenv
from google import genai
from google.genai import errors as genai_errors

load_dotenv()

logger = logging.getLogger(__name__)

# ── Model configuration ───────────────────────────────────────────────────────
PRIMARY_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
FALLBACK_MODEL = "gemini-2.5-flash-lite"
MODEL_DISPLAY_NAMES = {
    "gemini-2.5-flash": "Gemini 2.5 Flash on Vertex AI",
    "gemini-2.5-flash-lite": "Gemini 2.5 Flash Lite on Vertex AI",
}

DEFAULT_LOCATION = "global"
# A safety rail rather than the working limit: _classification_output_tokens sizes
# each call to its sample, and at the top of SAMPLE_LADDER (300 items) it asks for
# well under this. Set high enough that the per-sample figure is always what binds.
CLASSIFY_MAX_TOKENS = 32000
# Gemini counts reasoning tokens against max_output_tokens, and synthesis is the call
# that keeps thinking switched on, so this ceiling has to cover the model's reasoning
# as well as the JSON it finally writes, which Gemini pretty-prints. Generous on
# purpose: only tokens actually emitted are billed, while a ceiling that comes up
# short truncates the JSON and fails the run outright.
SYNTHESIS_MAX_TOKENS = 24000
TEMPERATURE = 0
MAX_ATTEMPTS = 3
RETRY_DELAYS_SECONDS = (1, 2)
MAX_RATE_LIMIT_WAIT_SECONDS = 25
# Enough of a bad payload to recognise the malformation in a log without dumping a
# whole report's worth of model output into it.
BAD_PAYLOAD_LOG_CHARS = 200

# ── Token budget ──────────────────────────────────────────────────────────────
# Gemini's context window is far larger than any sample this app builds, so the
# budget is a guard rail rather than a hard ceiling: it keeps a 150-post prompt from
# becoming slow and expensive, and it gives fit_sample a number to aim at. Vertex
# rejects a genuinely oversized request rather than a merely large one, and the
# refit ladder handles that case if the estimate here ever turns out optimistic.
DEFAULT_TOKEN_BUDGET = int(os.environ.get("GEMINI_TOKEN_BUDGET", "200000"))
BUDGET_SAFETY = 0.9
CLASSIFY_BUDGET_SHARE = 0.55
SYNTHESIS_BUDGET_SHARE = 0.45
CHARS_PER_TOKEN = 4
_token_limit: Optional[int] = None

# ── Sample shaping ────────────────────────────────────────────────────────────
MAX_POSTS_ANALYSED = 150
MAX_COMMENTS_ANALYSED = 150
BODY_PREVIEW_CHARS = 200
# Progressively smaller (posts, comments, preview chars) sample shapes. The first
# shape that fits the token budget wins, so a generous key uses the first rung.
SAMPLE_LADDER = (
    (150, 150, 200),
    (130, 130, 180),
    (110, 110, 160),
    (90, 90, 150),
    (70, 70, 140),
    (55, 55, 120),
    (40, 40, 110),
    (30, 30, 100),
    (20, 20, 90),
)
MIN_TOPICS = 4
MAX_TOPICS = 6
MAX_PAIN_POINTS = 5
MAX_QUESTIONS = 5
MIN_CONTENT_IDEAS = 8
MAX_CONTENT_IDEAS = 12
# A hook is the first line of a post or script: past ~120 characters it stops being
# a hook and starts being the post.
MAX_HOOK_CHARS = 120
LOW_DATA_THRESHOLD = 10

# ── Platforms ─────────────────────────────────────────────────────────────────
# A run targets one platform or both; a single idea always belongs to exactly one,
# because the format and the hook only make sense for one feed at a time.
IDEA_PLATFORMS = ("linkedin", "instagram")
PLATFORM_LABELS = {"linkedin": "LinkedIn", "instagram": "Instagram", "both": "LinkedIn + Instagram"}
PLATFORM_FORMATS = {
    "linkedin": ("Text post", "Carousel (PDF)", "Poll", "Long-form article", "Video script"),
    "instagram": ("Reel script", "Carousel", "Story series", "Caption + image idea"),
}

# ── User-facing error messages ────────────────────────────────────────────────
_BUSY = "The analysis service is busy right now. Please try again in a minute."
_BAD_KEY = (
    "The analysis service could not authenticate. Run "
    "'gcloud auth application-default login' and set GOOGLE_CLOUD_PROJECT to your "
    "Google Cloud project, then try again."
)
_NO_PROJECT = (
    "No Google Cloud project is configured. Set GOOGLE_CLOUD_PROJECT in your .env "
    "file to the project that has Vertex AI enabled, then try again."
)
# A disabled API and a missing sign-in both arrive as 403 PERMISSION_DENIED, and the
# fix for one is useless for the other, so they get separate sentences.
_API_DISABLED = (
    "Vertex AI is not enabled on this Google Cloud project. Run "
    "'gcloud services enable aiplatform.googleapis.com' for your project, wait a "
    "moment for it to take effect, then try again."
)
_INCOMPLETE = "The analysis came back incomplete. Please retry."
_UNAVAILABLE = "The analysis service is temporarily unavailable. Please try again in a moment."

_MARKDOWN_LINK_PATTERN = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
# Em (U+2014) and en (U+2013) dashes, written as escapes so this file stays free of
# the characters it exists to remove. A spaced dash reads as a clause break, so a
# comma keeps the sentence running; a tight one is usually a range or a compound
# word, where a hyphen is the right replacement.
_SPACED_DASH_PATTERN = re.compile(r"\s+[\u2014\u2013]\s+")
_BARE_DASH_PATTERN = re.compile(r"[\u2014\u2013]")
_QUOTA_PATTERN = re.compile(
    r"resource[_ ]exhausted|quota|rate.?limit|too many requests", re.IGNORECASE
)
_RESET_HINT_PATTERN = re.compile(r"try again in\s+((?:\d+h)?(?:\d+m)?(?:[\d.]+s)?)", re.IGNORECASE)
_TRAILING_COMMA_PATTERN = re.compile(r",\s*([}\]])")
_client = None


class PayloadTooLarge(Exception):
    """The request was larger than the provider will accept in one call.

    Not user-facing: the caller shaves the budget, refits the sample and retries.
    """


class _UnparseableResponse(Exception):
    """The call succeeded but the body was not a JSON object we could use.

    Never escapes this module. It exists so the retry loop can tell "the model wrote
    something malformed" apart from "the provider errored", which lead to different
    messages once the attempts run out.
    """


def model_display_name(model: str) -> str:
    return MODEL_DISPLAY_NAMES.get(model, model)


# ── Token budgeting ───────────────────────────────────────────────────────────
def estimate_tokens(text: str) -> int:
    """Cheap upper-ish estimate. Good enough to size a sample, no tokeniser needed."""
    return len(text or "") // CHARS_PER_TOKEN + 1


def token_budget() -> int:
    """Usable tokens per request, after the refit ladder has had any say."""
    return int((_token_limit or DEFAULT_TOKEN_BUDGET) * BUDGET_SAFETY)


def ensure_token_limit() -> int:
    """Kept as a no-op stub so callers written against the old provider still work.

    Groq published a per-key token ceiling that was worth probing for before sizing a
    sample. Vertex does not, so there is nothing to learn and nothing to spend a
    round trip on; the configured budget is the answer.
    """
    return _token_limit or DEFAULT_TOKEN_BUDGET


def _retry_wait_seconds(exc: Exception, attempt: int) -> float:
    """Respect the provider's own reset hint, capped so nobody waits a minute."""
    default = RETRY_DELAYS_SECONDS[min(attempt, len(RETRY_DELAYS_SECONDS) - 1)]
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if not headers:
        return default
    for key in ("retry-after", "x-ratelimit-reset-tokens"):
        try:
            raw = headers.get(key)
        except AttributeError:
            return default
        if not raw:
            continue
        try:
            seconds = float(str(raw).rstrip("s"))
        except ValueError:
            continue
        return max(default, min(seconds + 0.5, MAX_RATE_LIMIT_WAIT_SECONDS))
    return default


# ── Vertex AI client ──────────────────────────────────────────────────────────
def _project() -> str:
    """The Google Cloud project to bill and route the request to.

    Credentials come from Application Default Credentials, which the SDK finds on its
    own, but the project cannot be guessed reliably: ADC may carry no quota project,
    and picking the wrong one fails deep inside the SDK with an opaque message. Being
    explicit here turns that into one sentence the user can act on.
    """
    project = (os.environ.get("GOOGLE_CLOUD_PROJECT") or "").strip()
    if not project:
        raise ValueError(_NO_PROJECT)
    return project


def _location() -> str:
    return (os.environ.get("GOOGLE_CLOUD_LOCATION") or "").strip() or DEFAULT_LOCATION


def _get_client():
    global _client
    if _client is None:
        # No SDK-level retries: this module does its own with a capped wait, and
        # silent multi-second retries underneath make a run look hung.
        _client = genai.Client(
            vertexai=True,
            project=_project(),
            location=_location(),
            http_options={"retry_options": {"attempts": 1}},
        )
    return _client


def _generation_config(max_tokens: int, thinking: bool) -> dict:
    """JSON mode, deterministic, with thinking left to the caller.

    Gemini spends reasoning tokens out of `max_output_tokens`, so thinking and the
    output ceiling are one decision, not two: a call that thinks needs headroom for
    the thinking on top of its answer, or it truncates mid-JSON. Each caller owns both
    numbers together rather than inheriting a default it did not size for.
    """
    config = {
        "response_mime_type": "application/json",
        "temperature": TEMPERATURE,
        "max_output_tokens": max_tokens,
    }
    if not thinking:
        config["thinking_config"] = {"thinking_budget": 0}
    return config


def _status_of(exc: Exception) -> str:
    return str(getattr(exc, "status", "") or "")


def _code_of(exc: Exception) -> Optional[int]:
    code = getattr(exc, "code", None)
    return code if isinstance(code, int) else None


def _is_api_disabled(exc: Exception) -> bool:
    text = "{} {}".format(_status_of(exc), exc).lower()
    if "aiplatform" not in text and "vertex ai api" not in text:
        return False
    return any(
        marker in text
        for marker in ("has not been used", "is disabled", "not enabled", "service_disabled")
    )


def _is_auth_error(exc: Exception) -> bool:
    """Anything that means "we are not allowed to call this", credentials or IAM."""
    if _code_of(exc) in (401, 403):
        return True
    text = "{} {}".format(_status_of(exc), exc).lower()
    return any(
        marker in text
        for marker in (
            "unauthenticated",
            "permission_denied",
            "permission denied",
            "default credentials",
            "could not automatically determine credentials",
            "reauthentication",
            "invalid_grant",
        )
    )


def _is_quota_error(exc: Exception) -> bool:
    if _code_of(exc) == 429:
        return True
    return bool(_QUOTA_PATTERN.search("{} {}".format(_status_of(exc), exc)))


def _is_model_error(exc: Exception) -> bool:
    text = "{} {}".format(_status_of(exc), exc).lower()
    if "not_found" in text and "model" in text:
        return True
    return any(
        marker in text
        for marker in (
            "model not found",
            "was not found",
            "is not found",
            "is not supported",
            "not allowed to use",
            "publisher model",
        )
    )


def _is_payload_too_large(exc: Exception) -> bool:
    code = _code_of(exc)
    text = "{} {}".format(_status_of(exc), exc).lower()
    if code == 413 or "request payload size exceeds the limit" in text:
        return True
    return any(
        marker in text
        for marker in (
            "request too large",
            "reduce your message size",
            "input token count",
            "exceeds the maximum number of tokens",
            "token count exceeds",
        )
    )


def _finish_reason(response: Any) -> str:
    """Why the model stopped, as a string that is safe to log and to match on."""
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return "NO_CANDIDATES"
    return str(getattr(candidates[0], "finish_reason", "") or "UNKNOWN")


def _response_text(response: Any, label: str, finish: str) -> str:
    """The model's text, or an empty string when the call produced nothing usable.

    A response with no candidates (blocked prompt) and one truncated at the token
    ceiling both arrive as valid objects with no parseable JSON in them, so both are
    funnelled into the same retry path rather than raising an attribute error.
    Truncation is named in the log because from the outside it looks identical to the
    model returning nonsense, and the fix is completely different.
    """
    if "MAX_TOKENS" in finish:
        logger.warning(
            "%s hit its output token ceiling and was truncated; raise its max_tokens "
            "or turn thinking off for it",
            label,
        )
    if not getattr(response, "candidates", None):
        return ""
    return getattr(response, "text", None) or ""


def _parse_json_object(text: str) -> Optional[dict]:
    """The response as a dict, or None when it is not usable as one.

    Strict parsing first. A trailing comma before a closing brace or bracket is the one
    malformation JSON-mode models produce often enough to be worth handling, and it is
    only ever consulted after strict parsing has already failed, so this cannot change
    the meaning of a document `json.loads` would have accepted. Everything else (a
    truncated array, an unquoted key, prose wrapped around the JSON, a top-level list)
    is reported as unusable, because guessing at those is how a sanitiser starts
    inventing data.
    """
    attempts = [text]
    repaired = _TRAILING_COMMA_PATTERN.sub(r"\1", text)
    if repaired != text:
        attempts.append(repaired)

    for candidate in attempts:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        # Valid JSON of the wrong shape is not something a repair can fix, but a retry
        # might, so it is reported the same way as a parse failure.
        return parsed if isinstance(parsed, dict) else None
    return None


def _chat_json(
    prompt: str, max_tokens: int, label: str, thinking: bool = True
) -> Tuple[dict, str]:
    """One JSON-mode generation with retries, model fallback and clean errors.

    Returns (parsed_json, model_used). `thinking` is the caller's call: leave it on for
    work that genuinely reasons, turn it off for recall and labelling, and size
    `max_tokens` to match, because reasoning tokens come out of the same allowance.
    """
    client = _get_client()
    model = PRIMARY_MODEL
    tried_fallback = False
    started = time.time()
    last_exc: Optional[Exception] = None

    for attempt in range(MAX_ATTEMPTS):
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=_generation_config(max_tokens, thinking=thinking),
            )
        except Exception as exc:
            last_exc = exc
            if _is_api_disabled(exc):
                logger.error("%s blocked: Vertex AI is not enabled: %s", label, exc)
                raise ValueError(_API_DISABLED)
            if _is_auth_error(exc):
                logger.error("%s auth failure: %s", label, exc)
                raise ValueError(_BAD_KEY)
            if _is_payload_too_large(exc):
                logger.warning("%s payload above the request ceiling; refitting", label)
                raise PayloadTooLarge(str(exc))
            if _is_model_error(exc) and not tried_fallback:
                logger.warning("%s model %s unavailable, falling back", label, model)
                model, tried_fallback = FALLBACK_MODEL, True
                continue
            if _is_quota_error(exc):
                logger.warning("%s rate limited (attempt %s)", label, attempt + 1)
            elif isinstance(exc, genai_errors.ClientError):
                # A 4xx that is not quota, not auth and not the model is a request we
                # got wrong; retrying sends the same bad request again.
                logger.error("%s request rejected: %s", label, exc)
                raise ValueError(_UNAVAILABLE)
            else:
                logger.warning("%s transient API error (attempt %s): %s", label, attempt + 1, exc)
        else:
            finish = _finish_reason(response)
            content = _response_text(response, label, finish)
            parsed = _parse_json_object(content)
            if parsed is not None:
                logger.info("%s completed with %s in %.1fs", label, model, time.time() - started)
                return parsed, model
            # A malformed body is a flaky response, not a broken request. Even at
            # temperature 0 the model occasionally emits a trailing comma or an
            # unquoted key, and the previous version threw away an otherwise good
            # 80-second run over one of them. Retrying costs one call and almost
            # always heals it.
            preview = content[:BAD_PAYLOAD_LOG_CHARS]
            last_exc = _UnparseableResponse(
                "finish_reason={} payload={!r}".format(finish, preview)
            )
            logger.warning(
                "%s returned no usable JSON object (attempt %s of %s), finish_reason=%s, "
                "payload starts: %r",
                label, attempt + 1, MAX_ATTEMPTS, finish, preview,
            )

        if attempt < MAX_ATTEMPTS - 1:
            time.sleep(_retry_wait_seconds(last_exc, attempt))

    logger.error("%s exhausted retries: %s", label, last_exc)
    # Order matters: an unparseable payload can contain any words at all, including a
    # Reddit post about hitting a quota, so its own type is checked before the message
    # of the last failure is pattern-matched for anything.
    if isinstance(last_exc, _UnparseableResponse):
        raise ValueError(_INCOMPLETE)
    if last_exc is not None and _is_quota_error(last_exc):
        raise ValueError(quota_message(last_exc) or _BUSY)
    raise ValueError(_UNAVAILABLE)


# Public alias. Discovery needs exactly this behaviour (retries, model fallback,
# user-ready error messages) and a second copy of it would drift from this one the
# first time either changed. The first three arguments are positional and fixed;
# `thinking` is keyword-friendly with a safe default so callers that predate it are
# unaffected.
chat_json = _chat_json


def quota_message(exc: Exception) -> Optional[str]:
    """A quota rejection phrased for someone who cannot read a status code.

    Vertex enforces requests and tokens per minute rather than a daily cap, so this is
    almost always worth retrying shortly; the provider's own reset estimate is passed
    along when it gives one.
    """
    text = str(exc)
    if not _QUOTA_PATTERN.search(text):
        return None
    message = "The analysis service has reached its rate limit."
    match = _RESET_HINT_PATTERN.search(text)
    if match:
        hint = re.sub(r"([\d.]+)s$", "", match.group(1)).strip() or match.group(1)
        message += " It resets in about {}.".format(hint)
        return message + " Please try again after that."
    return message + " Please try again in a minute."


# ── Item selection and prompt shaping ─────────────────────────────────────────
def select_items(
    posts: Sequence[dict],
    comments: Sequence[dict],
    max_posts: int = MAX_POSTS_ANALYSED,
    max_comments: int = MAX_COMMENTS_ANALYSED,
) -> List[dict]:
    """Build the indexed analysis sample: newest posts first, then top comments.

    Index order is the contract between the model, stats.py and every citation, so
    this function is the single source of truth for it.
    """
    chosen_posts = list(posts or [])[:max_posts]
    ranked_comments = sorted(
        list(comments or []), key=lambda c: (c or {}).get("score", 0), reverse=True
    )[:max_comments]
    return chosen_posts + ranked_comments


def _preview(text: str, chars: int = BODY_PREVIEW_CHARS) -> str:
    flat = " ".join(str(text or "").split())
    return flat[:chars]


def format_items(
    items: Sequence[dict],
    preview_chars: int = BODY_PREVIEW_CHARS,
    indices: Optional[Sequence[int]] = None,
) -> str:
    """Numbered compact list, the shared coordinate system for both calls.

    Numbering always reflects each item's true position in the sample, even when
    only a subset is listed, so an index the model returns always resolves to the
    item the model was actually shown.
    """
    wanted = set(indices) if indices is not None else None
    lines = []
    for position, item in enumerate(items or [], 1):
        if wanted is not None and position not in wanted:
            continue
        item = item or {}
        if item.get("kind") == "comment":
            lines.append("{}. [comment] {}".format(position, _preview(item.get("body"), preview_chars)))
        else:
            body = _preview(item.get("body"), preview_chars)
            line = "{}. [post] {}".format(position, _preview(item.get("title"), 300))
            if body:
                line += "\n   {}".format(body)
            lines.append(line)
    return "\n".join(lines)


def _classification_output_tokens(n_items: int) -> int:
    """Room for one sentiment object per item plus the topic block.

    Measured against Gemini at 31.9 output tokens per labelled item over a 300-item
    sample: it pretty-prints JSON, so each `{"i": n, "t": n, "s": "neu"}` costs six
    indented lines rather than one compact object. The earlier figure of 20 came from
    a provider that emitted compact JSON, and carrying it over truncated a full sample
    mid-array. The allowance here is deliberately half again above the measurement,
    because unused output allowance is free and coming up short is a failed run.
    """
    return min(CLASSIFY_MAX_TOKENS, 45 * max(1, n_items) + 1200)


def fit_sample(
    posts: Sequence[dict],
    comments: Sequence[dict],
    subreddits: Sequence[str],
    budget: Optional[int] = None,
) -> Tuple[List[dict], int]:
    """Largest sample shape whose classification call fits the token budget.

    Returns (items, preview_chars). Falls back to the smallest rung rather than
    giving up, because a small honest sample still beats an error page.
    """
    allowance = int((budget if budget is not None else token_budget()) * CLASSIFY_BUDGET_SHARE)

    for max_posts, max_comments, preview_chars in SAMPLE_LADDER:
        items = select_items(posts, comments, max_posts, max_comments)
        if not items:
            continue
        estimate = estimate_tokens(
            _classification_prompt(items, subreddits, preview_chars)
        ) + _classification_output_tokens(len(items))
        if estimate <= allowance:
            logger.info(
                "sample fitted: %s items (%s chars preview), ~%s tokens of %s allowance",
                len(items), preview_chars, estimate, allowance,
            )
            return items, preview_chars

    max_posts, max_comments, preview_chars = SAMPLE_LADDER[-1]
    items = select_items(posts, comments, max_posts, max_comments)
    logger.warning("sample floor reached: %s items", len(items))
    return items, preview_chars


# ── Sanitisation ──────────────────────────────────────────────────────────────
def strip_markdown(value: Any) -> str:
    """Flatten model text so it cannot inject images or links into the report.

    An LLM summarising Reddit will happily echo `![](http://…)` from a post body;
    rendered unescaped in Streamlit that becomes remote content in our UI.

    Em and en dashes are normalised out here too. The prompts forbid them, but a
    prompt rule is a request and this is the enforcement: every string the model
    produces passes through this function, so a slip cannot reach a hook, an export
    or the screen.
    """
    text = "" if value is None else str(value)
    text = _MARKDOWN_LINK_PATTERN.sub(r"\1", text)
    text = _SPACED_DASH_PATTERN.sub(", ", text)
    text = _BARE_DASH_PATTERN.sub("-", text)
    return " ".join(text.split()).strip()


def _valid_indices(raw: Any, n_items: int) -> List[int]:
    if not isinstance(raw, (list, tuple)):
        return []
    seen = set()
    out = []
    for value in raw:
        # bool is an int subclass; True as an index is meaningless here.
        if isinstance(value, bool) or not isinstance(value, int):
            continue
        if 1 <= value <= n_items and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def sanitize_classification(raw: Any, n_items: int) -> dict:
    """Coerce call 1's output into a shape the rest of the app can trust.

    Accepts both label formats: per-item topic assignments (`items: [{i, t, s}]`,
    which is what the prompt asks for and what makes topic volumes a true share of
    the sample) and per-topic index lists (`topics: [{item_indices: [...]}]`).
    Whatever the model sends back, the returned shape is the same.
    """
    raw = raw if isinstance(raw, dict) else {}

    topics = []
    declared_indices = {}
    for position, topic in enumerate(raw.get("topics") or [], 1):
        if not isinstance(topic, dict):
            continue
        label = strip_markdown(topic.get("label"))
        if not label:
            continue
        topic_id = topic.get("id")
        topic_id = topic_id if isinstance(topic_id, int) and not isinstance(topic_id, bool) else position
        if topic_id in declared_indices:
            continue
        declared_indices[topic_id] = _valid_indices(topic.get("item_indices"), n_items)
        topics.append({"id": topic_id, "label": label})
    topics = topics[:MAX_TOPICS]
    known_ids = set(topic["id"] for topic in topics)

    sentiments = []
    assigned = {topic_id: [] for topic_id in known_ids}
    seen_indices = set()

    # The prompt asks for "items"; older/looser responses use "item_sentiments".
    entries = raw.get("items")
    if not isinstance(entries, list) or not entries:
        entries = raw.get("item_sentiments")

    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        index = entry.get("i")
        if isinstance(index, bool) or not isinstance(index, int):
            continue
        if not (1 <= index <= n_items) or index in seen_indices:
            continue
        seen_indices.add(index)

        label = str(entry.get("s") or "").strip().lower()[:3]
        if label in ("pos", "neu", "neg"):
            sentiments.append({"i": index, "s": label})

        topic_id = entry.get("t")
        if isinstance(topic_id, bool):
            continue
        if isinstance(topic_id, str) and topic_id.strip().lstrip("-").isdigit():
            topic_id = int(topic_id.strip())
        if isinstance(topic_id, int) and topic_id in assigned:
            assigned[topic_id].append(index)

    resolved = []
    for topic in topics:
        indices = assigned.get(topic["id"]) or declared_indices.get(topic["id"]) or []
        if not indices:
            # A topic with no resolvable evidence cannot be cited, so it is dropped.
            continue
        resolved.append({"id": topic["id"], "label": topic["label"], "item_indices": sorted(set(indices))})

    return {"topics": resolved, "item_sentiments": sentiments}


def _sanitize_claims(raw: Any, n_items: int, cap: int) -> List[dict]:
    claims = []
    for entry in raw or []:
        if isinstance(entry, str):
            entry = {"text": entry, "item_indices": []}
        if not isinstance(entry, dict):
            continue
        text = strip_markdown(entry.get("text"))
        if not text:
            continue
        claims.append({"text": text, "item_indices": _valid_indices(entry.get("item_indices"), n_items)})
    return claims[:cap]


def _match_topic_descriptions(raw_entries: Any, topics: Sequence[dict]) -> dict:
    """Bind each finding to a topic by its echoed label, not by the id it claims.

    Models reliably mis-key findings when asked for `{id, finding}`: a run of this
    pipeline returned the procrastination finding under the productivity-tools id and
    vice versa, which silently mislabels the most quoted part of the report. The
    prompt therefore requires the label to be echoed back verbatim, and the label,
    being self-describing content rather than an arbitrary integer, decides where
    the finding lands. The claimed id is only a fallback for an unrecognised label.
    """
    by_label = {}
    known_ids = set()
    for topic in topics or []:
        if not isinstance(topic, dict):
            continue
        topic_id = topic.get("id")
        if not isinstance(topic_id, int) or isinstance(topic_id, bool):
            continue
        known_ids.add(topic_id)
        label = strip_markdown(topic.get("label")).lower()
        if label:
            by_label.setdefault(label, topic_id)

    descriptions = {}
    for entry in raw_entries or []:
        if not isinstance(entry, dict):
            continue
        finding = strip_markdown(entry.get("finding"))
        if not finding:
            continue

        label = strip_markdown(entry.get("label")).lower()
        target = by_label.get(label)
        if target is None:
            # Unrecognised or missing label: fall back to the claimed id, but only
            # if that id is a topic we actually have.
            claimed = entry.get("id")
            if isinstance(claimed, bool) or not isinstance(claimed, int):
                continue
            if known_ids and claimed not in known_ids:
                continue
            target = claimed
        descriptions.setdefault(target, finding)
    return descriptions


def _idea_platform(value: Any, run_platform: str) -> str:
    """Which feed an idea belongs to. Always one of IDEA_PLATFORMS.

    When the run targets a single platform the model gets no vote: every idea was
    written for that feed, so a stray "instagram" in a LinkedIn run is a mistake,
    not a choice. Only a `both` run reads the model's answer, and an unreadable one
    falls back to the first platform rather than leaving the field unrenderable.
    """
    if run_platform in IDEA_PLATFORMS:
        return run_platform
    text = "".join(ch for ch in strip_markdown(value).lower() if ch.isalnum())
    for platform in IDEA_PLATFORMS:
        if text == platform:
            return platform
    return IDEA_PLATFORMS[0]


def sanitize_synthesis(
    raw: Any, n_items: int, topics: Sequence[dict] = (), platform: str = ""
) -> dict:
    """Coerce call 2's output; drop anything that cannot be rendered honestly.

    `platform` is the run's platform key, used to settle each idea's own platform
    field, so the returned ideas are always renderable even when the model omits it.
    """
    raw = raw if isinstance(raw, dict) else {}
    descriptions = _match_topic_descriptions(raw.get("topic_descriptions"), topics)

    ideas = []
    for entry in raw.get("content_ideas") or []:
        if not isinstance(entry, dict):
            continue
        title = strip_markdown(entry.get("title"))
        if not title:
            continue
        ideas.append(
            {
                "title": title,
                # A missing hook is not worth dropping a whole idea over; the report
                # renders it only when there is one.
                "hook": strip_markdown(entry.get("hook"))[:MAX_HOOK_CHARS].strip(),
                "platform": _idea_platform(entry.get("platform"), platform),
                "format": strip_markdown(entry.get("format")) or "Content",
                "angle": strip_markdown(entry.get("angle")),
                "rationale": strip_markdown(entry.get("rationale")),
                "addresses": strip_markdown(entry.get("addresses")),
                "item_indices": _valid_indices(entry.get("item_indices"), n_items),
            }
        )

    return {
        "executive_summary": strip_markdown(raw.get("executive_summary")),
        "topic_descriptions": descriptions,
        "pain_points": _sanitize_claims(raw.get("pain_points"), n_items, MAX_PAIN_POINTS),
        "questions": _sanitize_claims(raw.get("questions"), n_items, MAX_QUESTIONS),
        "content_ideas": ideas[:MAX_CONTENT_IDEAS],
    }


# ── Prompts ───────────────────────────────────────────────────────────────────
def _classification_prompt(
    items: Sequence[dict], subreddits: Sequence[str], preview_chars: int = BODY_PREVIEW_CHARS
) -> str:
    n_posts = sum(1 for item in items if item.get("kind") != "comment")
    n_comments = len(items) - n_posts
    communities = ", ".join("r/{}".format(name) for name in subreddits)

    return """You are a research analyst labelling Reddit content. You never invent content.

Below are {n} numbered items from {communities}: {n_posts} posts then {n_comments} comments.

{listing}

Return ONLY a JSON object with exactly these keys:

{{
  "topics": [{{"id": 1, "label": "..."}}],
  "items": [{{"i": 1, "t": 2, "s": "pos"}}]
}}

Rules for "topics":
- Identify {min_topics} to {max_topics} topics that genuinely recur in these items.
- "label" must state a SPECIFIC finding in 3-9 words, phrased as what people are
  actually saying or struggling with.
- NEVER use generic category headers such as "General discussion", "Advice",
  "Questions", "Tips", "Misc" or "Community".
- NEVER include a community name ({communities}) or the words "discussion" or
  "general" in a label.
- Give each topic a small integer "id" starting at 1.
- Never use an em dash or an en dash in any string you return. Use a comma or
  start a new sentence instead.

Rules for "items". This is the important part:
- Include EVERY item number from 1 to {n}, exactly once. Do not stop early, do not
  summarise, do not skip items that seem unimportant.
- "t" is the id of the ONE topic that item belongs to. Use 0 if the item genuinely
  fits none of the topics. Assign every item you reasonably can, because topic
  sizes are counted directly from these assignments.
- "s" is "pos" (positive/satisfied/enthusiastic), "neu" (neutral/informational/
  asking) or "neg" (negative/frustrated/critical). Judge the author's tone, not the
  subject matter. Do not default to "neu": a frustrated question is "neg" and a
  grateful answer is "pos".

No markdown fences, no commentary. JSON only.""".format(
        n=len(items),
        communities=communities,
        n_posts=n_posts,
        n_comments=n_comments,
        listing=format_items(items, preview_chars),
        min_topics=MIN_TOPICS,
        max_topics=MAX_TOPICS,
    )


def _synthesis_listing_indices(
    items: Sequence[dict], topics: Sequence[dict], allowance: int, preview_chars: int
) -> List[int]:
    """Which items to show call 2, in priority order, trimmed to the budget.

    Topic evidence comes first (it is what the findings must describe), then the
    highest-scoring remaining items, so the ideas are grounded in what the
    community actually upvoted.
    """
    ordered: List[int] = []
    seen = set()
    for topic in topics or []:
        for index in (topic.get("item_indices") or [])[:12]:
            if index not in seen:
                seen.add(index)
                ordered.append(index)
    for index in sorted(
        range(1, len(items) + 1), key=lambda i: -((items[i - 1] or {}).get("score") or 0)
    ):
        if index not in seen:
            seen.add(index)
            ordered.append(index)

    while ordered:
        estimate = estimate_tokens(format_items(items, preview_chars, ordered))
        if estimate <= allowance:
            break
        ordered = ordered[: max(5, int(len(ordered) * 0.8))]
        if len(ordered) <= 5:
            break
    return sorted(ordered)


def platform_label(platform: str) -> str:
    return PLATFORM_LABELS.get(platform, PLATFORM_LABELS["both"])


def _format_menu(platform: str) -> str:
    """The formats the model may choose from, per platform it is writing for.

    Left to itself the model returns "Blog post" and "Newsletter" for an Instagram
    run, which is useless to someone who only publishes Reels. Listing the native
    formats explicitly is what keeps the ideas publishable as written.
    """
    keys = IDEA_PLATFORMS if platform not in IDEA_PLATFORMS else (platform,)
    return "\n".join(
        "  {} → {}".format(PLATFORM_LABELS[key], ", ".join(PLATFORM_FORMATS[key]))
        for key in keys
    )


def _platform_rule(platform: str) -> str:
    if platform in IDEA_PLATFORMS:
        return (
            '"platform" is "{}" for every idea, because that is the only feed they '
            "publish on.".format(platform)
        )
    return (
        '"platform" is "linkedin" or "instagram". Choose whichever feed the idea '
        "belongs on, and cover both across the set."
    )


def _synthesis_prompt(
    items: Sequence[dict],
    topics: Sequence[dict],
    query: str,
    profile: str,
    platform: str,
    subreddits: Sequence[str],
    preview_chars: int = BODY_PREVIEW_CHARS,
) -> str:
    topic_lines = []
    for topic in topics:
        indices = topic.get("item_indices", [])[:12]
        excerpts = []
        for index in indices[:6]:
            item = items[index - 1]
            excerpts.append(
                "     [{}] {}".format(index, _preview(item.get("title") or item.get("body"), 160))
            )
        topic_lines.append(
            "  Topic {}: {} ({} items: {})\n{}".format(
                topic.get("id"),
                topic.get("label"),
                len(topic.get("item_indices", [])),
                ", ".join(str(i) for i in indices),
                "\n".join(excerpts),
            )
        )

    listing_budget = max(
        600, int(token_budget() * SYNTHESIS_BUDGET_SHARE) - SYNTHESIS_MAX_TOKENS - 900
    )
    listing_indices = _synthesis_listing_indices(items, topics, listing_budget, preview_chars)

    profile_line = ""
    if profile:
        profile_line = "\nThey describe themselves as: {}\n".format(profile)

    return """You are a content strategist for someone growing an audience on {label}.

They are researching: {query}
{profile_line}
The research covers {communities} over {n} recent items (posts and comments) from
those communities. A previous pass grouped them into these topics:

{topics}

Here are the numbered source items you may cite. Each number is that item's
position in the research sample:

{listing}

Return ONLY a JSON object with exactly these keys:

{{
  "executive_summary": "...",
  "topic_descriptions": [{{"id": 1, "label": "<exact label>", "finding": "..."}}],
  "pain_points": [{{"text": "...", "item_indices": [4, 9]}}],
  "questions": [{{"text": "...", "item_indices": [4, 9]}}],
  "content_ideas": [{{"title": "...", "hook": "...", "platform": "...",
                      "format": "...", "angle": "...", "rationale": "...",
                      "addresses": "...", "item_indices": [4]}}]
}}

Rules:
- "executive_summary": 2-3 sentences naming the single most important takeaway for
  their content strategy, what this evidence says they should be posting about.
  Lead with the insight, not with "This report shows".
- "topic_descriptions": exactly one entry per topic listed above. Copy that topic's
  "id" AND its "label" verbatim, character for character, then write the "finding"
  for THAT topic. The label you copy must be the label of the topic your finding
  describes. Check each one before moving on, because a finding attached to the
  wrong topic makes the whole report wrong. "finding" is 1-2 sentences stating what
  the evidence SHOWS. Never restate the label. Never say "this topic is about".
- "pain_points": the {max_pain} most concrete frustrations, in the community's own
  terms. Not categories, specific problems.
- "questions": the {max_q} questions people are actually asking, phrased as questions.
- "content_ideas": {min_ideas} to {max_ideas} posts they could publish.
- "hook" is the literal first line of the post or script, at most {max_hook}
  characters, written to stop the scroll. Not a description of the hook, the words
  themselves. Use the community's own language, never "In today's post".
- {platform_rule}
- "format" must be one of these, matching that idea's platform:
{formats}
- "angle" is the specific take in one sentence. "addresses" names the pain point or
  topic it answers. "rationale" is one sentence on why this audience would engage.
- Every "item_indices" must contain 1-3 item numbers taken from the numbered list
  above, chosen because they directly support the point. Never invent a number
  that is not shown above.
- Do not include URLs, links, image syntax or markdown formatting in any string.
- Never use an em dash or an en dash in any string you return. Use a comma or
  start a new sentence instead.

No markdown fences, no commentary. JSON only.""".format(
        label=platform_label(platform),
        query=query,
        profile_line=profile_line,
        communities=", ".join("r/{}".format(name) for name in subreddits),
        n=len(items),
        topics="\n".join(topic_lines) or "  (none identified)",
        listing=format_items(items, preview_chars, listing_indices),
        max_pain=MAX_PAIN_POINTS,
        max_q=MAX_QUESTIONS,
        min_ideas=MIN_CONTENT_IDEAS,
        max_ideas=MAX_CONTENT_IDEAS,
        max_hook=MAX_HOOK_CHARS,
        platform_rule=_platform_rule(platform),
        formats=_format_menu(platform),
    )


MAX_REFIT_ATTEMPTS = 3


def is_capacity_limited(
    items: Sequence[dict],
    preview_chars: int,
    posts: Sequence[dict],
    comments: Sequence[dict],
) -> bool:
    """True when the token budget, not the available data, decided the sample size.

    Lets the UI tell the difference between "that is all the community posted" and
    "your plan capped what we could read", which are very different messages.
    """
    top_posts, top_comments, top_preview = SAMPLE_LADDER[0]
    n_posts = sum(1 for item in items if (item or {}).get("kind") != "comment")
    n_comments = len(items) - n_posts
    return (
        preview_chars < top_preview
        or n_posts < min(top_posts, len(posts or []))
        or n_comments < min(top_comments, len(comments or []))
    )


# ── Public calls ──────────────────────────────────────────────────────────────
def classify_items(
    items: Sequence[dict],
    subreddits: Sequence[str],
    preview_chars: int = BODY_PREVIEW_CHARS,
) -> dict:
    """Call 1: group items into topics and label every item's sentiment."""
    if not items:
        raise ValueError("There was nothing to analyse in this sample.")
    logger.info("classifying %s items", len(items))
    raw, model = _chat_json(
        _classification_prompt(items, subreddits, preview_chars),
        _classification_output_tokens(len(items)),
        "classification",
        # Pure labelling: the shape of every answer is dictated by the prompt, so
        # reasoning tokens buy nothing and roughly double latency and cost.
        thinking=False,
    )
    result = sanitize_classification(raw, len(items))
    result["model"] = model
    logger.info(
        "classification: %s topics, %s/%s items labelled",
        len(result["topics"]),
        len(result["item_sentiments"]),
        len(items),
    )
    return result


def classify_sample(
    posts: Sequence[dict], comments: Sequence[dict], subreddits: Sequence[str]
) -> Tuple[List[dict], int, dict]:
    """Fit a sample to the account's token budget, classify it, shrink and retry if rejected.

    Returns (items, preview_chars, classification). The items list is authoritative
    from here on: every index in the classification refers to it.
    """
    if not posts and not comments:
        raise ValueError("There was nothing to analyse in this sample.")

    global _token_limit
    for attempt in range(MAX_REFIT_ATTEMPTS):
        items, preview_chars = fit_sample(posts, comments, subreddits)
        try:
            classification = classify_items(items, subreddits, preview_chars)
            classification["capacity_limited"] = is_capacity_limited(items, preview_chars, posts, comments)
            return items, preview_chars, classification
        except PayloadTooLarge:
            if attempt == MAX_REFIT_ATTEMPTS - 1:
                raise ValueError(
                    "This sample is larger than the analysis service can take right "
                    "now. Try a shorter time window."
                )
            # A rejection means the provider counts this prompt as bigger than our
            # estimate did, so the budget is shaved and the ladder tried again a rung
            # or two lower. It survives the process, so a second run starts smaller.
            _token_limit = int((_token_limit or DEFAULT_TOKEN_BUDGET) * 0.75)
            logger.warning("refitting sample against a %s token budget", _token_limit)

    raise ValueError(_UNAVAILABLE)


def synthesize(
    items: Sequence[dict],
    topics: Sequence[dict],
    query: str,
    profile: str,
    platform: str,
    subreddits: Sequence[str],
    preview_chars: int = BODY_PREVIEW_CHARS,
) -> dict:
    """Call 2: turn the labels into platform-native content ideas with evidence."""
    logger.info("synthesising %s ideas for %s", platform, query)
    try:
        raw, model = _chat_json(
            _synthesis_prompt(items, topics, query, profile, platform, subreddits, preview_chars),
            SYNTHESIS_MAX_TOKENS,
            "synthesis",
        )
    except PayloadTooLarge:
        logger.warning("synthesis payload rejected; retrying with a tighter listing")
        # Only the listing shrinks. The output ceiling stays where it is: the request
        # was rejected for what we sent, and cutting the model's room to answer would
        # trade a rejection for a truncated, unparseable one.
        raw, model = _chat_json(
            _synthesis_prompt(items, topics, query, profile, platform, subreddits, 90),
            SYNTHESIS_MAX_TOKENS,
            "synthesis",
        )
    result = sanitize_synthesis(raw, len(items), topics, platform)
    result["model"] = model
    matched = len(result["topic_descriptions"])
    if topics and matched < len(topics):
        logger.warning("only %s of %s topics came back with a finding", matched, len(topics))
    logger.info(
        "synthesis: %s topic findings, %s pain points, %s questions, %s ideas",
        matched,
        len(result["pain_points"]),
        len(result["questions"]),
        len(result["content_ideas"]),
    )
    return result
