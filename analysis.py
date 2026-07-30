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

import inspect
import json
import logging
import math
import os
import re
import time
from typing import Any, Callable, Dict, List, NamedTuple, Optional, Sequence, Tuple

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
# Raised from 0.10 when the triage gained a second, longer preview for qualifier
# briefs. The shares do not partition one request's spend: triage, classification
# and synthesis are separate HTTP calls, each sized against the whole budget, so a
# share is a per-call guard rail against one oversized request rather than a slice
# of a shared pot. At 0.10 a 600 character preview dropped the batch from 120 to
# about 76, which took an 800 item pool from 7 sequential calls to 11, roughly 60
# to 100 extra seconds that the run's soft deadline does not have. At 0.15 the
# batch is back at its 120 ceiling and the pool is 7 calls again. The shares now
# sum to 1.05, which is deliberate and harmless for that reason.
TRIAGE_BUDGET_SHARE = 0.15
CLASSIFY_BUDGET_SHARE = 0.50
SYNTHESIS_BUDGET_SHARE = 0.40
CHARS_PER_TOKEN = 4

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
# A claim shorter than this is a category, not a finding: "Pricing", "Tools",
# "Time". The report is worth reading only when every line says something.
MIN_CLAIM_CHARS = 25
# The ideas are the product, so the run asks for as many as the budget allows.
# Arithmetic, in the style of _classification_output_tokens: a pretty-printed idea
# object (six string fields, one small index array) measures around 250 output
# tokens, so 15 ideas cost ~3,750. Add the rest of the payload (6 topic findings at
# ~80, 5 pain points and 5 questions at ~60, a summary at ~120, a title at ~20) for
# roughly 5,000 output tokens against SYNTHESIS_MAX_TOKENS of 24,000, which leaves
# about 19,000 for reasoning. Unused output reservation is free, so 15 is safe.
MIN_CONTENT_IDEAS = 12
MAX_CONTENT_IDEAS = 15
# What the sanitiser will accept back, as opposed to what the prompt asks for. A
# model that returns 16 usable ideas should not have four thrown away.
SANITIZE_MAX_CONTENT_IDEAS = 20
SANITIZE_MIN_CONTENT_IDEAS = 8
# A report title has to fit a heading and a browser tab.
MAX_REPORT_TITLE_CHARS = 70
FALLBACK_TITLE_CHARS = 60
# A hook is the first line of a post or script: past ~120 characters it stops being
# a hook and starts being the post.
MAX_HOOK_CHARS = 120
# Two ideas whose hooks open on the same 40 characters read as one idea printed
# twice, however differently they finish. The prompt bans it; this is what
# enforces it when the model does it anyway.
HOOK_STEM_CHARS = 40
# A run of the user's own self-description, repeated verbatim inside a hook, is
# the model reading the brief back rather than quoting the community. Both
# thresholds have to be met, so a hook and a profile that happen to share one
# long word or one short phrase are left alone: the point is to catch
# "I run a Notion template shop for freelance designers." as an opening line,
# not to police vocabulary.
PROFILE_ECHO_MIN_CHARS = 12
PROFILE_ECHO_MIN_WORDS = 3
# The other half of the same guard. A word the user capitalised in the middle of
# their own sentence is a name: their brand, their product, or the tool they sell
# around. Those are exactly the words that must never turn up inside a hook, and
# a live run put "My Notion template ... keeps me sane" in one. Sentence-initial
# words are ignored, because a capital there means nothing.
BRAND_TOKEN_MIN_CHARS = 4
LOW_DATA_THRESHOLD = 10

# ── Selection tuning ──────────────────────────────────────────────────────────
# How the best posts and comments are chosen from the fetched pool. A cheap triage
# call rates each candidate's relevance to the query; Python then ranks by
# relevance x engagement x recency, so the model judges language and the code
# decides the sample, in that order and never the other way around.
RECENCY_HALF_LIFE_DAYS = 8
# 0 gates an item out of the main tier entirely; 1 keeps the maybe-relevant
# majority competitive without letting it beat a clearly on-topic item.
RELEVANCE_WEIGHTS = {2: 1.0, 1: 0.55, 0: 0.0}
# An item the triage never saw is not the same as one the model looked at and
# called adjacent. It sits in its own tier below every rated item, because an
# unrated item beside a rated-1 item silently claims a judgement nobody made.
UNTRIAGED_WEIGHT = 0.35
# A zero-engagement item is quiet, not worthless: without a floor, a brand-new
# on-topic post scores 0 and loses to any off-window viral one.
ENGAGEMENT_FLOOR = 0.25
# No subreddit may hold more than this share of any prefix of the ranked list,
# while another community still has eligible items to offer.
FAIRNESS_SHARE = 0.5
# Below this much real text (URLs stripped) a post body is a link drop; a title
# this short cannot carry the idea on its own either.
SUBSTANCE_MIN_BODY_CHARS = 15
SUBSTANCE_MIN_TITLE_CHARS = 40
# How much of the fetched pool the triage call covers. A full run can fetch up to
# 500 posts and 300 comments per community across 4 communities, so covering the
# whole pool would mean roughly 27 sequential model calls and several minutes of
# latency, which no serverless request budget survives. So the pool is capped at
# its strongest candidates by base score, and everything the triage never saw
# ranks in its own UNTRIAGED_WEIGHT tier rather than being quietly treated as if a
# model had rated it 1.
TRIAGE_MAX_POSTS = 400
TRIAGE_MAX_COMMENTS = 400
# 100 characters is a headline and half a sentence, which is not enough text to
# judge relevance on; 300 reaches the point of most Reddit posts. The batch size
# comes down to match so a batch still fits TRIAGE_BUDGET_SHARE of the budget.
TRIAGE_PREVIEW_CHARS = 300
# Only for a brief that carries requirements about who posted. 300 characters is
# enough to tell what a thread is about and nowhere near enough to tell who wrote
# it: the sentence that says "we just closed our Series B" is usually two or three
# paragraphs in. 800 is the hard ceiling past which the marginal value collapses,
# because seniority signals live in the opening context paragraph. Topical runs
# keep the 300 above, so they keep today's batch size and call count exactly.
TRIAGE_QUALIFIER_PREVIEW_CHARS = 600
# Author and link flair, prepended to the preview line. In startup and marketing
# subreddits flair is frequently the single most reliable seniority signal there
# is, and it costs about 20 characters. It is truncated INSIDE the preview budget
# rather than added on top of it, so the preview length stays the honest cap the
# batch arithmetic is computed from.
TRIAGE_FLAIR_CHARS = 40
# Never above 120. Long numbered listings lose index alignment near the tail, and
# at the qualifier output reservation below, 200 items would reserve 12,300 tokens
# against TRIAGE_MAX_TOKENS and truncate the answer mid-array.
TRIAGE_BATCH_SIZE = 120
# Measured the same way as _classification_output_tokens: Gemini pretty-prints, so
# each {"i": n, "r": n} costs four indented lines, and the sibling classification
# call measures 45 tokens per labelled item. The earlier figure of 30 was carried
# over from nothing in particular.
TRIAGE_OUTPUT_TOKENS_PER_ITEM = 45
# A qualifier verdict adds one more pretty-printed line per item ("q": "yu"), so
# the reservation goes up with it. 60 x 120 items is 7,500 against the 12,000
# ceiling below, which is why the batch size must not grow past 120.
TRIAGE_QUALIFIER_OUTPUT_TOKENS_PER_ITEM = 60
TRIAGE_MAX_TOKENS = 12000

# ── Degenerate qualifier verdicts ─────────────────────────────────────────────
# A requirement the model marks as CONTRADICTED by more than this share of the
# items it rated is treated as unjudgeable: its counts are still reported, and it
# is left out of the ranking decision entirely.
#
# Why it exists: a live run on the brief "detailed posts about running Meta ads for
# B2B SaaS, not from newbies or solo founders, ideally from funded startups series
# a b c" came back with 107 of 110 items marked as contradicting "The author is
# from a funded startup, Series A, B or C", while the two negatively phrased
# requirements in the same batch behaved correctly. A post that never mentions the
# author's company had been read as failing "the author IS funded". The triage
# prompt is the primary fix; this is the guard for the next time a batch comes back
# mislabelled, because that failure is silent: every item demoted puts every item
# in one tier, so the ranking does nothing while the report tells the reader that
# 107 real threads contradicted what they asked for.
#
# Why more than half: silence is the norm here, so a correct spread of verdicts is
# mostly "u". An "n" on the majority of a pool means the model found positive
# evidence of incompatibility in the majority of 600 character previews, which is
# far likelier to be a misread requirement than a property of the corpus. It is
# also the point where the tier stops doing anything: once most of the pool is
# demoted, backfill IS the sample, so the demotion can no longer separate the good
# items from the rest. Firing the guard slightly too eagerly costs a filter that
# had already stopped discriminating; firing it too late leaves a false claim about
# real threads in a document the user may share, which is the worse error.
DEGENERATE_VIOLATION_SHARE = 0.5
# Ratios over a handful of items are noise: 3 of 4 says nothing about whether a
# requirement was misread, and a pool that small has no meaningful tiering to
# protect anyway.
DEGENERATE_MIN_RATED = 8

# ── Brief decomposition ───────────────────────────────────────────────────────
# A brief can carry constraints about WHO posted a thread ("only from someone at a
# funded startup", "not newbies") alongside its topic. One cheap call splits those
# out before the triage runs, so the triage can rate the topic and check the
# constraints as two separate judgements instead of collapsing both into one score.
#
# Three is the ceiling because a fourth constraint is nearly always a restatement
# of one of the first three, and every extra one costs a character per item across
# the whole triage pool. 120 characters is a sentence; past that the model is
# writing a paragraph the triage then has to weigh against a 600 character preview.
MAX_REQUIREMENTS = 3
MAX_REQUIREMENT_CHARS = 120
# The topic is what gets searched for and what the report says the brief was read
# as, so it has to fit a line.
MAX_BRIEF_TOPIC_CHARS = 100
# A topic string plus at most three short sentences is well under 200 tokens of
# JSON. Generous on the same asymmetry as everywhere else here: unused allowance
# is free, and a truncated answer means the constraints are silently dropped.
DECOMPOSE_MAX_TOKENS = 1000


class Requirement(NamedTuple):
    """One constraint the brief states about who posted or what kind of thread.

    `text` is always phrased POSITIVELY: a brief that rules newbies out becomes
    "The author is not a beginner or a solo founder." That normalisation is the
    reason there is exactly one downstream rule (an item either meets a
    requirement, breaks it, or does not say) instead of an inverted-logic branch
    per kind that would be wrong half the time.

    `kind` survives only so the report can say "you asked for" versus "you ruled
    out". Nothing in the ranking reads it.
    """

    text: str
    kind: str


class Brief(NamedTuple):
    """A research brief read as a topic plus the constraints it states."""

    topic: str
    requirements: Tuple[Requirement, ...]


class Verdict(NamedTuple):
    """One triage answer: the topic rating, and one character per requirement.

    `qualifiers` is one character per requirement in the brief's own order: "y"
    meets it, "n" breaks it, "u" does not say. Empty when the brief states no
    requirements, which is the common case and the one that keeps today's contract.

    Topic and qualifiers stay separate rather than collapsing into one score,
    because the ranking has to be able to weigh an on-topic-but-silent item
    against an off-topic-but-qualified one, and the report has to be able to
    attribute a demotion to the requirement that caused it.
    """

    topic: int
    qualifiers: str

# ── Formats ───────────────────────────────────────────────────────────────────
# The product writes for one professional feed, so there is no platform to choose and
# nothing about a platform in the output. Naming the formats explicitly is still what
# keeps ideas publishable: left to itself the model returns "Blog post" and
# "Newsletter", which nobody can publish as written.
IDEA_FORMATS = ("Text post", "Carousel (PDF)", "Poll", "Long-form article", "Video script")
# When the user picks content types, every idea comes back in one of those formats.
# These keys are the wire values pipeline.validate_inputs accepts.
CONTENT_TYPE_FORMATS = {
    "text": "Text post",
    "carousel": "Carousel (PDF)",
    "video": "Video script",
    "poll": "Poll",
    "article": "Long-form article",
}
# Both spellings of every format the user can ask for, so an idea the model
# labelled "carousel" is recognised as the "Carousel (PDF)" that was requested
# rather than coerced away from the format it already has.
_FORMAT_ALIASES = {}
for _key, _label in CONTENT_TYPE_FORMATS.items():
    _FORMAT_ALIASES[_key] = _label
    _FORMAT_ALIASES[_label.lower()] = _label


def chosen_formats(content_types: Optional[Sequence[str]]) -> List[str]:
    """The idea-format labels behind a content-type selection, in menu order.

    Unknown values are dropped rather than refused: validation is the input
    layer's job, and an unrecognised type here should leave the ideas alone
    instead of pinning them to something nobody asked for.
    """
    wanted = {
        str(value).strip().lower()
        for value in (content_types or [])
        if str(value).strip()
    }
    return [
        label for key, label in CONTENT_TYPE_FORMATS.items() if key in wanted
    ]

# ── Draft posts ───────────────────────────────────────────────────────────────
# Long enough to say something, short enough to survive the feed's "see more" fold.
DRAFT_MIN_WORDS = 120
DRAFT_MAX_WORDS = 220
DRAFT_MAX_HASHTAGS = 3
# Enough evidence to give the post real substance, capped because the draft should
# read as one person's point of view, not a literature review.
DRAFT_MAX_EVIDENCE = 6
# Matches the citation excerpts the report now carries, so a draft sees the whole
# excerpt it was given rather than the first two thirds of it.
DRAFT_EVIDENCE_CHARS = 600
# A carousel, a script and an article are not the same artefact as a text post, so
# the draft call writes each one in its own shape and to its own length.
DRAFT_FORMAT_RULES = {
    "Carousel (PDF)": (
        "Write this as slide-by-slide carousel copy. Start every slide on its own "
        'line with "Slide 1:", "Slide 2:" and so on, 6 to 9 slides in total. Slide 1 '
        "is the hook and nothing else. Each middle slide carries one idea in at most "
        "25 words. The last slide asks for a reply."
    ),
    "Video script": (
        "Write this as a short video script. Open with the hook as spoken words, then "
        'label each beat on its own line ("Beat 1:", "Beat 2:" and so on), 4 to 6 '
        "beats. Write what is said, not camera directions. End on a spoken question."
    ),
    "Long-form article": (
        "Write this as a structured long-form article. Open with the hook, then 4 to 6 "
        "short sections, each introduced by a plain one-line heading with no markdown "
        "or numbering. Close with a short section that invites a reply."
    ),
    "Poll": (
        "Write this as a poll post: open with the hook, then two or three sentences of "
        "context, then the poll question on its own line, then two to four options, each "
        'on its own line prefixed with "Option:". Close with one line asking people to '
        "vote and to say in the comments why they chose what they chose."
    ),
    "Text post": (
        "Write this as a plain feed post: short paragraphs of one to three sentences, "
        "separated by a blank line, because a wall of text does not get read."
    ),
}
# (minimum, maximum) words per format. A carousel and a script are counted in
# slides and beats, so their word bands are wider than a single text post's.
DRAFT_FORMAT_WORDS = {
    "Carousel (PDF)": (110, 260),
    "Video script": (150, 300),
    "Long-form article": (350, 650),
    "Poll": (80, 180),
    "Text post": (DRAFT_MIN_WORDS, DRAFT_MAX_WORDS),
}
# Thinking stays on for drafting, so this covers the model's reasoning as well as a
# 220-word post.
DRAFT_MAX_TOKENS = 8000

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
_DRAFT_FAILED = "We could not draft that post. Please try again."
# A safety block is a property of the text we sent, not a flaky call, so retrying
# it three times spends 90 seconds arriving at the same refusal.
_CONTENT_BLOCKED = (
    "Some of the discussion in these communities could not be analysed. Try "
    "different communities."
)

_MARKDOWN_LINK_PATTERN = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
# Paired markdown emphasis only. A lone asterisk is far more likely to be "$5 * 3"
# than a formatting slip, and stripping those would corrupt real content.
_EMPHASIS_PATTERN = re.compile(r"(\*\*|__|\*|_)(?=\S)(.+?)(?<=\S)\1")
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
# Nothing legitimate in a hook, a finding or a topic label contains a link, and a
# model echoing one out of a Reddit body is how a report ends up advertising
# somebody else's site. Full URLs go first, then bare domains, matched against a
# TLD list so "Node.js", "v3.5" and "i.e." survive untouched.
_BARE_URL_PATTERN = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_BARE_DOMAIN_PATTERN = re.compile(
    r"\b[a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)*"
    r"\.(?:com|net|org|io|co|ai|dev|app|so|xyz|me|us|uk|edu|gov|info|blog|shop|store|news)"
    r"\b(?:/\S*)?",
    re.IGNORECASE,
)
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
    """Usable tokens per request.

    A single value for the whole process. When one run's prompt is rejected as too
    large, that run shaves its own copy of this number and refits (see
    classify_sample); it does not shrink the budget for every other run in the
    worker, which is what a module-level "learned limit" used to do. Two concurrent
    reports would race on it, and one oversized sample would permanently shrink
    every later report in the same process.
    """
    return int(DEFAULT_TOKEN_BUDGET * BUDGET_SAFETY)


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


_BLOCKING_FINISH_REASONS = ("SAFETY", "PROHIBITED_CONTENT", "RECITATION", "BLOCKLIST", "SPII")


def _block_reason(response: Any, finish: str) -> str:
    """Why a content filter stopped this call, or "" when nothing did.

    Two places carry it. `finish_reason` covers a candidate the filter killed after
    generation started, and `prompt_feedback.block_reason` covers a prompt the
    filter refused before it started. Either way the model will refuse the same text
    on every attempt, so this is the one failure in this function that must not be
    retried: three attempts spend a minute reaching the same answer, and the user
    ends up reading "temporarily unavailable" about something permanent.
    """
    for marker in _BLOCKING_FINISH_REASONS:
        if marker in finish.upper():
            return marker
    feedback = getattr(response, "prompt_feedback", None)
    if feedback is None:
        return ""
    reason = str(getattr(feedback, "block_reason", "") or "").strip()
    # The SDK reports "BLOCK_REASON_UNSPECIFIED" for a prompt nothing blocked.
    if not reason or reason.upper().endswith("UNSPECIFIED") or reason == "0":
        return ""
    return reason


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
    # The flag actually sent on the next attempt. Kept apart from the `thinking`
    # parameter (never mutated) because Gemini bills reasoning tokens out of the same
    # max_output_tokens as the answer: reasoning and answer share one allowance. When
    # a response comes back unparseable while thinking was on, that usually means the
    # allowance went to reasoning and left nothing for the JSON, so the retry below
    # turns thinking off to hand the full ceiling to the answer instead.
    effective_thinking = thinking

    for attempt in range(MAX_ATTEMPTS):
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=_generation_config(max_tokens, thinking=effective_thinking),
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
            blocked = _block_reason(response, finish)
            if blocked:
                logger.error("%s was stopped by a content filter (%s)", label, blocked)
                raise ValueError(_CONTENT_BLOCKED)
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
            if effective_thinking:
                # Reasoning and the answer are drawn from the same max_output_tokens,
                # so an unparseable payload with thinking on is not just "try again":
                # it is a sign the reasoning ate the allowance and left the JSON
                # truncated. Turning thinking off gives the retry the full ceiling for
                # the answer alone, on top of the ordinary heals-a-flaky-response retry.
                logger.warning(
                    "%s truncated while thinking; turning thinking off for the "
                    "remaining attempts so the answer has the full token allowance",
                    label,
                )
                effective_thinking = False
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


# ── Selection: scoring ────────────────────────────────────────────────────────
# Everything in this section is deterministic. The only model involvement is the
# relevance rating fed in from rate_relevance, and even that arrives as plain
# integers the code is free to distrust.


def _engagement_raw(item: dict) -> float:
    """Log-damped engagement, so 4000 upvotes beats 400 without drowning it."""
    item = item or {}
    score = max(0, int(item.get("score") or 0))
    if item.get("kind") == "comment":
        return math.log1p(score)
    return math.log1p(score + max(0, int(item.get("comments") or 0)))


def _recency_decay(created_utc: Any, now: float) -> float:
    """Halves every RECENCY_HALF_LIFE_DAYS. Undated items sit in the middle:
    they cannot claim to be fresh, and they should not be buried as ancient."""
    try:
        created = int(created_utc or 0)
    except (TypeError, ValueError):
        created = 0
    if created <= 0:
        return 0.5
    age_days = max(0.0, (now - created) / 86400.0)
    return 0.5 ** (age_days / RECENCY_HALF_LIFE_DAYS)


def _has_substance(post: dict) -> bool:
    """A link drop with a throwaway title cannot seed a content idea."""
    post = post or {}
    body = _BARE_URL_PATTERN.sub("", str(post.get("body") or "")).strip()
    if len(body) >= SUBSTANCE_MIN_BODY_CHARS:
        return True
    return len(str(post.get("title") or "").strip()) >= SUBSTANCE_MIN_TITLE_CHARS


def _base_scores(items: Sequence[dict], now: float) -> List[float]:
    """Engagement x recency per item, engagement normalised within each subreddit.

    Normalising per community is what lets a 40-upvote post from a small subreddit
    compete with a 4000-upvote one from a huge subreddit: each is measured against
    its own community's ceiling, not the loudest community's.
    """
    raw = [_engagement_raw(item) for item in items]
    ceiling: Dict[str, float] = {}
    for item, value in zip(items, raw):
        subreddit = str((item or {}).get("subreddit") or "")
        ceiling[subreddit] = max(ceiling.get(subreddit, 0.0), value)

    scores = []
    for item, value in zip(items, raw):
        subreddit = str((item or {}).get("subreddit") or "")
        top = ceiling.get(subreddit, 0.0)
        # An all-zero community gets neutral footing rather than nothing; the
        # fairness cap stops it dominating on that footing.
        norm = (value / top) if top > 0 else 0.5
        engagement = ENGAGEMENT_FLOOR + (1 - ENGAGEMENT_FLOOR) * norm
        scores.append(engagement * _recency_decay((item or {}).get("created_utc"), now))
    return scores


def _fair_order(*tiers: Sequence[Tuple[float, dict]]) -> List[dict]:
    """Order best-first so that every prefix of the whole result respects the cap.

    Greedy: always take the best-scored head of a community that is still under
    the cap for the next prefix; when every community with items left is capped
    (only possible when one community is all that remains), take the best anyway.
    Prefix safety is the point: fit_sample slices this list at whatever rung the
    token budget allows, and the slice has to be as fair as the whole.

    Tiers are emitted in order (no item from a later tier ever outranks one from an
    earlier tier) but the cap and the running counts are SHARED across all of them.
    Ordering each tier separately and concatenating breaks the guarantee at the
    seam: a community that filled the whole eligible tier starts the backfill tier
    with a count of zero and can take the next several slots unopposed.
    """
    picked: List[dict] = []
    counts: Dict[str, int] = {}

    for tier in tiers:
        queues: Dict[str, List[Tuple[float, int, dict]]] = {}
        for position, (score, item) in enumerate(tier):
            subreddit = str((item or {}).get("subreddit") or "")
            # position keeps the sort stable and fully deterministic on ties.
            queues.setdefault(subreddit, []).append((score, position, item))
        for queue in queues.values():
            queue.sort(key=lambda entry: (-entry[0], entry[1]))
            queue.reverse()  # pop() from the end is the best remaining

        while any(queues.values()):
            cap = max(1, math.ceil(FAIRNESS_SHARE * (len(picked) + 1)))
            heads = [
                (queue[-1][0], queue[-1][1], subreddit)
                for subreddit, queue in queues.items()
                if queue
            ]
            allowed = [head for head in heads if counts.get(head[2], 0) < cap]
            score, position, subreddit = max(
                allowed or heads, key=lambda head: (head[0], -head[1])
            )
            picked.append(queues[subreddit].pop()[2])
            counts[subreddit] = counts.get(subreddit, 0) + 1
    return picked


def relevance_key(kind: str, item: dict, position: int) -> Tuple[str, str]:
    """Namespaced identity for one item's relevance rating.

    A post and a comment can carry the same raw id (they live in different
    namespaces on Reddit and the archive does not disambiguate them), and the
    archive sometimes gives us an item with neither an id nor a permalink, which
    collapses every such item onto the empty-string key. Both cases hand one item's
    rating to a different item. The kind plus the item's position in its own pool
    is unique in a way the raw id is not.
    """
    ident = str((item or {}).get("id") or "").strip()
    kind = "comment" if kind == "comment" else "post"
    return (kind, ident or "#{}".format(position))


def _as_verdict(value: Any) -> Verdict:
    """Read a relevance mapping's value as a Verdict, accepting a bare int.

    A bare 0..2 is what the mapping held before qualifiers existed, and it is what
    every injected triage fake and every caller outside this module still passes.
    Normalising here rather than at each read site means there is one place where a
    verdict-shaped value is defined, and no caller has to be upgraded to keep
    working.
    """
    if isinstance(value, Verdict):
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return Verdict(1, "")
    return Verdict(int(value), "")


def requirement_unreliable(counts: Any) -> bool:
    """Whether one requirement's verdicts came back too lopsided to rank on.

    Reads the same {"met", "violated", "unknown"} counts the report prints, so the
    flag and the numbers printed beside it can never disagree. See
    DEGENERATE_VIOLATION_SHARE for why a majority of "n" is read as the model
    failing to judge a requirement rather than as a fact about the corpus.

    It cannot be decided from one item's verdict: it needs the whole batch, which
    is why _select_sample computes it once and hands it to everything downstream.
    """
    counts = counts if isinstance(counts, dict) else {}
    met = int(counts.get("met") or 0)
    violated = int(counts.get("violated") or 0)
    unknown = int(counts.get("unknown") or 0)
    rated = met + violated + unknown
    if rated < DEGENERATE_MIN_RATED:
        return False
    return violated > rated * DEGENERATE_VIOLATION_SHARE


def qualifier_tier(verdict: Any, unreliable: Sequence[bool] = ()) -> str:
    """"confirmed", "unconfirmed" or "violated" for one verdict.

    Any "n" violates: one requirement the item plainly breaks is enough, because
    the brief stated all of them. Otherwise any "y" confirms. An item that says
    nothing either way, and an item from a brief with no requirements at all, are
    both "unconfirmed", which is the tier that costs an item nothing.

    `unreliable` is one flag per requirement in the brief's own order, from
    requirement_unreliable over the whole batch. A flagged requirement's character
    is skipped entirely, so it can neither demote nor promote: a verdict nobody
    trusts must not decide the ranking in either direction, and the report says
    plainly that it was not used. It defaults to no flags, so every existing caller
    keeps today's behaviour exactly.
    """
    qualifiers = _as_verdict(verdict).qualifiers
    trusted = "".join(
        char
        for index, char in enumerate(qualifiers)
        if not (index < len(unreliable) and unreliable[index])
    )
    if "n" in trusted:
        return "violated"
    if "y" in trusted:
        return "confirmed"
    return "unconfirmed"


def rank_items(
    posts: Sequence[dict],
    comments: Sequence[dict],
    relevance: Optional[Dict[Tuple[str, str], Any]] = None,
    now: Optional[float] = None,
    unreliable: Sequence[bool] = (),
) -> Tuple[List[dict], List[dict]]:
    """Fully order both pools, best candidates first.

    `relevance` maps relevance_key(kind, item, position) -> Verdict from the triage
    call, and a bare 0..2 int is accepted for it too (see _as_verdict). Items it
    does not cover are untriaged and rank in the unconfirmed tier below every rated
    item (UNTRIAGED_WEIGHT), so a partial triage degrades the ranking instead of
    quietly promoting unrated items to the same footing as ones a model actually
    looked at and called adjacent. Items rated 0, and posts with no substance, drop
    to a backfill tier below everything eligible: still available when a quiet topic
    needs them, never ahead of anything actually on-topic.

    Three tiers, not two, once the brief carries requirements: items that meet one
    lead, items that say nothing either way follow, and items that plainly break one
    join the existing backfill tier. Nothing is deleted, and that is deliberate. It
    differs from the hard filtering the keyword phrases get because the two are not
    the same kind of claim: a keyword match is a checkable fact about the text,
    while a qualifier verdict is a model inferring a fuzzy social attribute from a
    truncated snippet. "I'm new to running Meta ads" from a Series B growth lead
    reads as a beginner in 600 characters, so false negatives are the expected
    failure and demotion is the honest response to them. Deleting would also need
    new empty-sample handling, since classify_sample raises on an empty sample,
    which would turn a disappointing run into a failed one.

    A multiplier instead of a tier does not work. With confirmed at 1.0 and unknown
    at 0.6, a confirmed item at base 0.3 scores 0.30 while a silent item at base 0.9
    scores 0.54, so engagement drowns the constraint that was the point of the
    brief. Tiers in _fair_order are absolute, while the fairness cap stays shared
    across every seam.

    Because every requirement is stored as a positive sentence, only an explicit "n"
    demotes: absence of evidence costs an item nothing.

    `unreliable` marks requirements whose verdicts came back degenerate across the
    whole batch (see requirement_unreliable); they take no part in the tiering, so
    an item that only breaks a flagged requirement is not demoted. The default of no
    flags is today's behaviour.
    """
    relevance = relevance or {}
    timestamp = time.time() if now is None else now

    def ordered(items: Sequence[dict], kind: str, substance_check: bool) -> List[dict]:
        items = [item for item in items if isinstance(item, dict)]
        base = _base_scores(items, timestamp)
        confirmed: List[Tuple[float, dict]] = []
        unconfirmed: List[Tuple[float, dict]] = []
        backfill: List[Tuple[float, dict]] = []
        for position, (item, score) in enumerate(zip(items, base)):
            rating = relevance.get(relevance_key(kind, item, position))
            if rating is None:
                weight = UNTRIAGED_WEIGHT
                tier = "unconfirmed"
            else:
                verdict = _as_verdict(rating)
                weight = RELEVANCE_WEIGHTS.get(verdict.topic, RELEVANCE_WEIGHTS[1])
                tier = qualifier_tier(verdict, unreliable)
            if weight <= 0 or (substance_check and not _has_substance(item)):
                # Ranked by base score alone: relevance said no, so it no longer
                # gets a say in how the leftovers are ordered. Checked before the
                # qualifier tier, so an off-topic item stays in backfill however
                # well its author qualifies.
                backfill.append((score, item))
            elif tier == "violated":
                backfill.append((score, item))
            elif tier == "confirmed":
                confirmed.append((weight * score, item))
            else:
                unconfirmed.append((weight * score, item))
        # With no requirements every item is "unconfirmed", so `confirmed` is empty
        # and this is exactly the two-tier order it was before.
        return _fair_order(confirmed, unconfirmed, backfill)

    return ordered(posts, "post", True), ordered(comments, "comment", False)


# ── Injected callables ────────────────────────────────────────────────────────
# The same contract pipeline._call documents, kept here because pipeline imports
# analysis and the dependency cannot run the other way. Both exist for one reason:
# the triage and decomposition functions are substitution points, and a fake that
# predates a new keyword argument has to keep working.
def _accepts(func: Callable, name: str) -> bool:
    """Whether `func` will take a keyword argument called `name`."""
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


def _call(func: Callable, *args, **kwargs):
    """Call an injected callable, dropping any keyword it cannot take.

    A `None` value is dropped as well as an undeclared keyword, so passing
    `soft_deadline=None` (no deadline in play) behaves exactly as passing nothing.
    """
    return func(*args, **{
        name: value
        for name, value in kwargs.items()
        if value is not None and _accepts(func, name)
    })


def _past(deadline: Optional[float]) -> bool:
    """True once an absolute time.monotonic() deadline has gone by."""
    return deadline is not None and time.monotonic() >= deadline


# ── Selection: brief decomposition ────────────────────────────────────────────
def _as_brief(value: Any) -> Brief:
    """Read a bare query string as a Brief, so old callers keep working."""
    if isinstance(value, Brief):
        return value
    return Brief("" if value is None else str(value), ())


def _decompose_prompt(query: str, profile: str) -> str:
    return """You are reading a content research brief and splitting it in two: the topic to
search Reddit for, and any constraint the brief states about WHO posted a thread
or WHAT KIND of thread it is.

Brief: {query}
Researcher: {profile}

Return ONLY a JSON object with exactly these keys:

{{
  "topic": "...",
  "requirements": [{{"text": "...", "kind": "require"}}]
}}

Rules:
- "topic" is the subject matter alone, under {topic_chars} characters, with every
  constraint about who posted removed. It is what to search for, nothing else.
- "requirements" holds ONLY constraints about who posted the thread or what kind
  of thread it is. Seniority, job, company stage, company size and the kind of
  thread it is all belong here. Nothing about the subject matter belongs here.
- Most briefs state no such constraint at all. When this one states none, return
  an empty list. That is the normal answer and it is not a failure.
- Never invent a constraint the brief does not state, and never turn the topic
  into a requirement.
- At most {max_requirements} requirements. Keep each one under
  {requirement_chars} characters.
- Phrase every requirement as a POSITIVE sentence a thread either meets or
  breaks, starting with "The author" or "The thread". A brief that rules
  something out still becomes a positive sentence, with "not" inside it: "not
  looking for posts by beginners" becomes "The author is not a beginner."
- "kind" is "require" when the brief asks for it and "exclude" when the brief
  rules it out. Both are phrased positively either way; "kind" only records which
  way the brief said it.
- Do not include URLs, links, image syntax or markdown formatting in any string.
- Never use an em dash or an en dash in any string you return. Use a comma or
  start a new sentence instead.

No markdown fences, no commentary. JSON only.""".format(
        query=query,
        profile=profile or "not given",
        topic_chars=MAX_BRIEF_TOPIC_CHARS,
        max_requirements=MAX_REQUIREMENTS,
        requirement_chars=MAX_REQUIREMENT_CHARS,
    )


def sanitize_brief(raw: Any, query: str) -> Brief:
    """Coerce the decomposition call's output into a Brief. Never raises.

    Every failure mode returns Brief(query, ()), which is precisely the behaviour
    the triage had before requirements existed: the whole brief goes in as the
    topic and no qualifier is checked. That is what makes this call safe to add.
    """
    raw = raw if isinstance(raw, dict) else {}

    topic = strip_markdown(raw.get("topic"))[:MAX_BRIEF_TOPIC_CHARS].strip()

    requirements: List[Requirement] = []
    entries = raw.get("requirements")
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, str):
            entry = {"text": entry, "kind": "require"}
        if not isinstance(entry, dict):
            continue
        text = strip_markdown(entry.get("text"))[:MAX_REQUIREMENT_CHARS].strip()
        if not text:
            continue
        kind = str(entry.get("kind") or "").strip().lower()
        requirements.append(Requirement(text, "exclude" if kind == "exclude" else "require"))
        if len(requirements) >= MAX_REQUIREMENTS:
            break

    # A topic the model did not give us is not a reason to lose the requirements it
    # did: the raw query is what the triage used as its topic before this call
    # existed, so falling back to it changes nothing about the rating.
    return Brief(topic or query, tuple(requirements))


def decompose_brief(
    query: str,
    profile: str = "",
    chat: Optional[Any] = None,
) -> Brief:
    """Split the brief into its topic and the constraints it states about authors.

    One extra call per run, before the triage, with thinking off: naming what a
    sentence asks for is reading comprehension, not deliberation.

    Deliberately its own call rather than folded into discovery, because discovery
    does not run at all for manual subreddits, so the constraints would silently
    vanish for exactly the power users most likely to write a brief that has them.
    Deliberately not decided per triage batch either: the same brief would be
    re-interpreted 7 to 12 times, so two items with identical text would get
    different verdicts according to which batch they landed in.

    Fails open to Brief(query, ()) on anything at all, which reproduces today's
    behaviour exactly. A brief that could not be read is never worth failing a run
    over, and running without the constraints is what every run did until now.
    """
    text = " ".join(str(query or "").split())
    if not text:
        return Brief(str(query or ""), ())

    chat = chat or chat_json
    try:
        raw, _ = chat(
            _decompose_prompt(text, " ".join(str(profile or "").split())),
            DECOMPOSE_MAX_TOKENS,
            "brief-decomposition",
            thinking=False,
        )
        brief = sanitize_brief(raw, query)
    except Exception as exc:  # noqa: BLE001 - fail open, this call is never fatal
        logger.warning(
            "brief decomposition failed, running on the topic alone: %s", exc
        )
        return Brief(query, ())

    logger.info(
        "brief read as %r with %s requirement(s)", brief.topic, len(brief.requirements)
    )
    return brief


# ── Selection: relevance triage ───────────────────────────────────────────────
def _triage_flair(item: dict) -> str:
    """"(flair: ...) " from the author's and the thread's flair, or "".

    Both flairs, author first, because a "Series B | Head of Growth" author flair
    and a "Case Study" link flair say different things and either can be the only
    signal in the whole item about who is writing.
    """
    parts: List[str] = []
    for field in ("author_flair_text", "link_flair_text"):
        value = strip_markdown((item or {}).get(field))
        if value and value not in parts:
            parts.append(value)
    text = " / ".join(parts)[:TRIAGE_FLAIR_CHARS].strip()
    return "(flair: {}) ".format(text) if text else ""


def _triage_preview(
    item: dict, chars: int = TRIAGE_PREVIEW_CHARS, flair: bool = False
) -> str:
    """The one line of text the triage judges an item from.

    Never the username. A handle carries no qualifier information and inviting a
    model to read seniority off "growthguy99" is inviting it to invent one.

    Flair is off by default so a run with no requirements sends the line it always
    sent: flair says nothing about a topic, and the common case is meant to have no
    regression surface at all.
    """
    item = item or {}
    text = " ".join("{} {}".format(item.get("title") or "", item.get("body") or "").split())
    if not flair:
        return text[:chars]
    # Truncated inside the same budget rather than added on top of it, so `chars`
    # stays the honest per-item cap the batch arithmetic is computed from.
    return (_triage_flair(item) + text)[:chars]


def _triage_lines(
    items: Sequence[dict],
    start: int = 1,
    preview_chars: int = TRIAGE_PREVIEW_CHARS,
    flair: bool = False,
) -> str:
    lines = []
    for position, item in enumerate(items, start):
        kind = "comment" if (item or {}).get("kind") == "comment" else "post"
        lines.append(
            "{}. [{}] {}".format(
                position, kind, _triage_preview(item, preview_chars, flair)
            )
        )
    return "\n".join(lines)


def _requirement_lines(requirements: Sequence[Requirement]) -> str:
    return "\n".join(
        "{}. {}".format(position, requirement.text)
        for position, requirement in enumerate(requirements, 1)
    )


def _triage_prompt(
    items: Sequence[dict],
    query: str,
    profile: str,
    start: int,
    brief: Optional[Any] = None,
) -> str:
    """The triage prompt, in one of two shapes.

    With no requirements this returns the prompt byte for byte as it was before
    qualifiers existed, asking for {"i", "r"} and nothing else. That is the whole
    point of omitting "q" rather than sending an empty one: the common case keeps
    today's prompt, today's batch size and today's call count, so it has no
    regression surface at all.
    """
    brief = _as_brief(brief if brief is not None else query)
    if not brief.requirements:
        return (
            "You are rating Reddit items for relevance to a research brief. "
            "You never invent content.\n\n"
            "Brief: {query}\n"
            "Researcher: {profile}\n\n"
            "Below are {count} numbered items, each a short preview of a post or comment.\n\n"
            "{listing}\n\n"
            'Return ONLY a JSON object: {{"ratings": [{{"i": {start}, "r": 2}}, ...]}}\n\n'
            "Rules:\n"
            "- Include EVERY item number shown above, exactly once.\n"
            '- "r" is 2 (directly about the brief\'s topic), 1 (adjacent: the same '
            "audience or a neighbouring problem, partly related, or too short to tell), "
            "or 0 (a different audience or subject entirely, or spam).\n"
            "- When unsure, use 1. No markdown fences, no commentary. JSON only."
        ).format(
            query=query,
            profile=profile or "not given",
            count=len(items),
            listing=_triage_lines(items, start),
            start=start,
        )

    count = len(brief.requirements)
    return (
        "You are rating Reddit items for a research brief. You never invent content.\n\n"
        "Topic: {topic}\n"
        "Researcher: {profile}\n\n"
        "The brief also states {n} constraint(s) about who posted a thread or what "
        "kind of thread it is:\n"
        "{requirements}\n\n"
        "Below are {count} numbered items, each a short preview of a post or comment. "
        "Some items open with the author's or the thread's flair, which is often the "
        "only thing an item says about who wrote it.\n\n"
        "{listing}\n\n"
        'Return ONLY a JSON object: {{"ratings": [{{"i": {start}, "r": 2, '
        '"q": "{example}"}}, ...]}}\n\n'
        "Rules:\n"
        "- Include EVERY item number shown above, exactly once.\n"
        '- "r" is 2 (directly about the topic above), 1 (adjacent: the same audience '
        "or a neighbouring problem, partly related, or too short to tell), or 0 (a "
        "different audience or subject entirely, or spam). When unsure, use 1.\n"
        '- "q" is exactly {n} character(s), one per constraint in the order listed '
        'above, each one "y", "n" or "u".\n'
        '- "u" means the item does not say. It is the correct answer for most items, '
        'and most of the characters you return should be "u", because most Reddit '
        "threads never say anything about who wrote them.\n"
        '- "y" only when the item itself states or clearly implies something that '
        "meets the constraint.\n"
        '- "n" only when the item itself states or clearly implies something that is '
        "INCOMPATIBLE with the constraint. An item that simply never mentions what a "
        'constraint is about is "u", never "n". Silence is always "u". Not being able '
        'to tell is "u". "u" is the correct answer, not a failure.\n'
        '- Worked example, for the constraint "The author is from a funded startup, '
        'Series A, B or C". A thread that asks a detailed question about ad costs and '
        'never mentions the author\'s company is "u", because it says nothing either '
        'way. A thread whose author writes "I am a solo bootstrapper with no '
        'investors" is "n", because that is incompatible with being funded. A thread '
        'whose author writes "we raised our Series B last year" is "y".\n'
        "- Judge each constraint only on what the item says about its AUTHOR. Never "
        "infer it from what the thread is about, which community it is in, the "
        "writing style, the tone, how experienced the author sounds, or a username. "
        'An expert-sounding thread whose author never describes themselves is "u".\n'
        '- Judge "r" on the topic only and "q" on the constraints only. An item can '
        'be squarely on topic and still be "n", and off topic and still be "y".\n'
        "- No markdown fences, no commentary. JSON only. Never use an em dash or an "
        "en dash in any string you return."
    ).format(
        topic=brief.topic,
        profile=profile or "not given",
        n=count,
        requirements=_requirement_lines(brief.requirements),
        count=len(items),
        listing=_triage_lines(items, start, TRIAGE_QUALIFIER_PREVIEW_CHARS, flair=True),
        start=start,
        example="u" * count,
    )


def _triage_output_tokens(
    n_items: int, per_item: int = TRIAGE_OUTPUT_TOKENS_PER_ITEM
) -> int:
    """Room for one pretty-printed {"i", "r"} object per item, padded the same
    way as _classification_output_tokens and for the same reason. A qualifier run
    passes the larger per-item figure, because "q" is one more line per item."""
    return min(TRIAGE_MAX_TOKENS, per_item * max(1, n_items) + 300)


def _triage_shape(brief: Optional[Any]) -> Tuple[int, int]:
    """(preview chars, output tokens per item) for this brief's triage calls.

    One function so the batch arithmetic and the calls themselves can never
    disagree about how long a preview is or how much answer to reserve for it.
    """
    if _as_brief(brief).requirements:
        return TRIAGE_QUALIFIER_PREVIEW_CHARS, TRIAGE_QUALIFIER_OUTPUT_TOKENS_PER_ITEM
    return TRIAGE_PREVIEW_CHARS, TRIAGE_OUTPUT_TOKENS_PER_ITEM


def _triage_batch_size(query: str, profile: str, brief: Optional[Any] = None) -> int:
    """Items per triage call, sized so one call fits its share of the budget.

    TRIAGE_BUDGET_SHARE existed as a constant nobody consulted, which is how a
    preview-length change can silently push a batch past what the request will
    take. The arithmetic: each listed item costs its preview plus its numbering
    and kind tag, and reserves the per-item output tokens for the rating that
    comes back. Whatever is left of the share after the fixed instructions decides
    how many items fit, capped at TRIAGE_BATCH_SIZE so a huge budget does not
    produce one enormous call.

    A qualifier brief doubles the preview and raises the output reservation, which
    is why TRIAGE_BUDGET_SHARE went up with it: at the old share this returned
    about 76, which is 11 sequential calls for an 800 item pool instead of 7.
    """
    brief = _as_brief(brief if brief is not None else query)
    preview_chars, output_per_item = _triage_shape(brief)
    allowance = int(token_budget() * TRIAGE_BUDGET_SHARE)
    overhead = estimate_tokens(_triage_prompt([], query, profile, 1, brief)) + 100
    per_item = preview_chars // CHARS_PER_TOKEN + 8 + output_per_item
    fits = (allowance - overhead) // max(1, per_item)
    return max(1, min(TRIAGE_BATCH_SIZE, int(fits)))


def _sanitize_qualifiers(raw: Any, n_requirements: int) -> str:
    """One character per requirement, "u" for anything the model did not say.

    "u" is the fail-open value for every malformed shape: a missing "q", a null, a
    list, an int, a string of the wrong length either way, an unrecognised letter.
    It must never collapse into "n", because "the item does not say" and "the item
    breaks this" are different facts and only the second one demotes.
    """
    if n_requirements <= 0:
        return ""
    text = raw if isinstance(raw, str) else ""
    out = []
    for char in text[:n_requirements]:
        lowered = char.lower()
        out.append(lowered if lowered in ("y", "n", "u") else "u")
    # Short answers are padded, long ones were truncated above.
    out.extend("u" * (n_requirements - len(out)))
    return "".join(out)


def _sanitize_ratings(
    raw: dict, batch_size: int, start: int, n_requirements: int = 0
) -> Dict[int, Verdict]:
    """Every item in the batch gets a verdict; anything malformed fails open.

    A fumbled topic rating becomes 1 and a fumbled qualifier character becomes "u",
    in both cases costing that item nothing in either direction. The two are
    sanitised independently, so a malformed "q" cannot disturb a valid "r" on the
    same entry, or the other way round.
    """
    default = Verdict(1, "u" * max(0, n_requirements))
    ratings = {position: default for position in range(start, start + batch_size)}
    entries = raw.get("ratings") if isinstance(raw, dict) else None
    seen = set()
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        index = entry.get("i")
        if isinstance(index, bool) or not isinstance(index, int):
            continue
        if index < start or index >= start + batch_size or index in seen:
            continue
        seen.add(index)
        rating = entry.get("r")
        topic = default.topic
        if not isinstance(rating, bool) and isinstance(rating, (int, float)):
            if int(rating) in (0, 1, 2):
                topic = int(rating)
        ratings[index] = Verdict(
            topic, _sanitize_qualifiers(entry.get("q"), n_requirements)
        )
    return ratings


def rate_relevance(
    items: Sequence[dict],
    query: str,
    profile: str,
    chat: Optional[Any] = None,
    brief: Optional[Any] = None,
    soft_deadline: Optional[float] = None,
) -> Optional[Dict[int, Verdict]]:
    """Rate every candidate against the brief, keyed by 1-based position in `items`.

    Each value is a Verdict: the 0..2 topic rating, plus one character per
    requirement the brief states. With no requirements the qualifier string is
    empty and the prompt, the batch size and the call count are exactly what they
    were before requirements existed.

    Batches keep each call small; indices are global across batches so results
    merge by simple union. A batch that fails after chat_json's own retries is
    dropped (its items fall back to the untriaged tier in rank_items); only when
    every batch fails does this return None, telling the caller to select
    deterministically. Triage failure is never worth failing a run over.

    `soft_deadline` is an absolute time.monotonic() value. Once it has passed, no
    new batch is issued: these calls are uninterruptible once started, there are 7
    or more of them, and the items that never get rated already have an honest home
    in the UNTRIAGED_WEIGHT tier. Stopping early degrades the ranking; overrunning
    fails the whole run.
    """
    chat = chat or chat_json
    brief = _as_brief(brief if brief is not None else query)
    n_requirements = len(brief.requirements)
    ratings: Dict[int, Verdict] = {}
    batches = 0
    failures = 0
    batch_size = _triage_batch_size(query, profile, brief)
    _, output_per_item = _triage_shape(brief)

    for offset in range(0, len(items), batch_size):
        if _past(soft_deadline):
            logger.warning(
                "out of time after %s triage batch(es); %s items stay untriaged",
                batches, len(items) - offset,
            )
            break
        batch = items[offset : offset + batch_size]
        start = offset + 1
        batches += 1
        try:
            raw, _ = chat(
                _triage_prompt(batch, query, profile, start, brief),
                _triage_output_tokens(len(batch), output_per_item),
                "relevance-triage",
                thinking=False,
            )
        except (ValueError, PayloadTooLarge) as exc:
            failures += 1
            logger.warning(
                "relevance-triage batch at %s failed, items keep the default rating: %s",
                start, exc,
            )
            continue
        ratings.update(_sanitize_ratings(raw, len(batch), start, n_requirements))

    if batches > 0 and failures == batches:
        return None
    return ratings


# ── Item selection and prompt shaping ─────────────────────────────────────────
def select_items(
    posts: Sequence[dict],
    comments: Sequence[dict],
    max_posts: int = MAX_POSTS_ANALYSED,
    max_comments: int = MAX_COMMENTS_ANALYSED,
) -> List[dict]:
    """Build the indexed analysis sample: the best posts, then the best comments.

    A pure slicer: both lists arrive already ranked by rank_items (or in fetch
    order when the caller skipped ranking), so every prefix is already the best
    sample of its size. Index order is the contract between the model, stats.py
    and every citation, so this function is the single source of truth for it.
    """
    return list(posts or [])[:max_posts] + list(comments or [])[:max_comments]


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
    rendered unescaped that becomes remote content in our UI. Bare URLs and bare
    domains go the same way: nothing legitimate in a hook, a finding or a topic
    label contains a link, so anything that looks like one was lifted out of a
    Reddit post and is not ours to republish.

    Emphasis markers go too. `**bold**` renders as literal asterisks in a report
    that does not parse markdown, and as unexpected formatting in one that does.

    Em and en dashes are normalised out here too. The prompts forbid them, but a
    prompt rule is a request and this is the enforcement: every string the model
    produces passes through this function, so a slip cannot reach a hook, an export
    or the screen.
    """
    text = "" if value is None else str(value)
    text = _MARKDOWN_LINK_PATTERN.sub(r"\1", text)
    text = _BARE_URL_PATTERN.sub("", text)
    text = _BARE_DOMAIN_PATTERN.sub("", text)
    # Twice, so "**bold _and_ italic**" loses both layers.
    for _ in range(2):
        text = _EMPHASIS_PATTERN.sub(r"\2", text)
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
        if topic_id == 0:
            # 0 is the prompt's "this item fits no topic" sentinel. A topic that
            # claims it would collect every unassigned item in the sample, so the
            # topic is dropped rather than renumbered: renumbering it onto a free id
            # would hand it the sentinel's items anyway.
            logger.info("classification proposed a topic with the reserved id 0; dropped")
            continue
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


_CLAIM_NOISE_PATTERN = re.compile(r"[^a-z0-9 ]+")


def _claim_fingerprint(text: str) -> str:
    """What two claims have to share to count as the same claim.

    Case, punctuation and a trailing question mark are all the difference between
    the pain point "people cannot price retainers" and the question "People cannot
    price retainers?", which the model returns in both lists often enough that a
    report ships the same sentence twice under two headings.
    """
    return " ".join(_CLAIM_NOISE_PATTERN.sub(" ", text.lower()).split())


def _sanitize_claims(
    raw: Any, n_items: int, cap: int, seen: Optional[set] = None
) -> List[dict]:
    """Coerce a claim list, dropping anything too thin or already said.

    `seen` is a fingerprint set the caller owns, so questions can be deduped
    against the pain points that were sanitised before them.
    """
    claims = []
    fingerprints = seen if seen is not None else set()
    for entry in raw or []:
        if isinstance(entry, str):
            entry = {"text": entry, "item_indices": []}
        if not isinstance(entry, dict):
            continue
        text = strip_markdown(entry.get("text"))
        if len(text) < MIN_CLAIM_CHARS:
            # "Pricing" is a category, not a finding. A claim this short cannot
            # carry evidence and reads as filler next to the ones that can.
            continue
        fingerprint = _claim_fingerprint(text)
        if not fingerprint or fingerprint in fingerprints:
            continue
        fingerprints.add(fingerprint)
        claims.append({"text": text, "item_indices": _valid_indices(entry.get("item_indices"), n_items)})
        if len(claims) >= cap:
            break
    return claims


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


def _trim_to_words(text: str, limit: int) -> str:
    """Cut to at most `limit` characters without leaving half a word behind."""
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rstrip()
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(" ,.;:-")


_HOOK_NOISE_PATTERN = re.compile(r"[^a-z0-9]+")


def _hook_words(text: Any) -> List[str]:
    """A hook flattened to bare lowercase words, so punctuation and casing
    cannot hide a repeat."""
    return [word for word in _HOOK_NOISE_PATTERN.sub(" ", str(text or "").lower()).split() if word]


def _hook_stem(hook: str) -> str:
    """The opening of a hook, flattened, for spotting two ideas that start the same."""
    return " ".join(_hook_words(hook))[:HOOK_STEM_CHARS]


def _echoes(hook: str, source: str) -> bool:
    """True when `hook` repeats a long verbatim run of words from `source`.

    Used against the user's profile only. The query is deliberately left out:
    the words of the question are the subject everybody in the sample is
    talking about, so a good hook about scope creep will legitimately contain
    "scope creep", and rejecting that would cost more ideas than it saved. The
    profile is a different case, because nothing a Redditor said can contain
    the user's own description of their business.
    """
    words = _hook_words(hook)
    source_text = " {} ".format(" ".join(_hook_words(source)))
    if len(words) < PROFILE_ECHO_MIN_WORDS or not source_text.strip():
        return False
    for start in range(len(words)):
        # Longest run first, and stop as soon as the runs get too short to
        # count, because everything after that point is shorter still.
        for end in range(len(words), start + PROFILE_ECHO_MIN_WORDS - 1, -1):
            run = " ".join(words[start:end])
            if len(run) < PROFILE_ECHO_MIN_CHARS:
                break
            if " {} ".format(run) in source_text:
                return True
    return False


_SENTENCE_END_PATTERN = re.compile(r"[.!?:;]$")


def brand_tokens(profile: str) -> set:
    """Names the user gave for themselves: their brand, product or tool.

    A word they capitalised mid-sentence is a name, and nothing else in an
    ordinary sentence is. The first word of each sentence is skipped because a
    capital there carries no information, and short words are skipped because
    an initialism of two or three letters collides with too much ordinary text.

    Deliberately narrow, at the cost of missing a brand that only ever appears
    as the first word of a sentence ("Ledgerly is bookkeeping software."). The
    prompt is the primary defence here; this is the backstop for the shape a
    profile usually takes, and a backstop that started deleting good ideas
    because somebody wrote a capital letter would be worse than the gap.
    """
    tokens = set()
    previous_ends_sentence = True
    for raw in str(profile or "").split():
        word = raw.strip("\"'()[[],")
        stripped = word.rstrip(".,!?:;")
        starts_sentence = previous_ends_sentence
        previous_ends_sentence = bool(_SENTENCE_END_PATTERN.search(word))
        if starts_sentence:
            continue
        if len(stripped) < BRAND_TOKEN_MIN_CHARS or not stripped[:1].isupper():
            continue
        if not stripped.replace("-", "").isalnum():
            continue
        tokens.add(stripped.lower())
    return tokens


def report_title_from_query(query: str) -> str:
    """The title a report falls back to when the model does not supply a usable one."""
    text = strip_markdown(query)
    if not text:
        return "Content ideas from Reddit"
    return _trim_to_words(text, FALLBACK_TITLE_CHARS)


def sanitize_synthesis(
    raw: Any,
    n_items: int,
    topics: Sequence[dict] = (),
    query: str = "",
    content_types: Optional[Sequence[str]] = None,
    profile: str = "",
) -> dict:
    """Coerce call 2's output; drop anything that cannot be rendered honestly.

    Two of those drops are about hooks specifically, because the hook is the
    part a user publishes verbatim. An idea whose hook opens on the same forty
    characters as an earlier one is the same idea twice, and an idea whose hook
    repeats the user's own description of themselves is the brief read back as
    if a stranger had said it. Both are banned in the prompt; this is the
    backstop for when the model does it anyway.
    """
    raw = raw if isinstance(raw, dict) else {}
    descriptions = _match_topic_descriptions(raw.get("topic_descriptions"), topics)
    # When the user asked for particular content types, those are the formats,
    # whatever the model wrote in the field. Nothing else in the report would
    # honour a stray "Poll" among fourteen carousels. An idea outside the set
    # goes to the chosen format used least so far, which both keeps the promise
    # and pulls the spread back towards even.
    allowed_formats = chosen_formats(content_types)
    format_counts = {label: 0 for label in allowed_formats}

    def pinned_format(value: str) -> str:
        if not allowed_formats:
            return value
        label = _FORMAT_ALIASES.get(value.strip().lower(), "")
        if label not in format_counts:
            label = min(
                allowed_formats, key=lambda name: (format_counts[name], allowed_formats.index(name))
            )
        format_counts[label] += 1
        return label

    ideas = []
    hook_stems: set = set()
    names = brand_tokens(profile)
    for entry in raw.get("content_ideas") or []:
        if not isinstance(entry, dict):
            continue
        title = strip_markdown(entry.get("title"))
        if not title:
            continue
        # A missing hook is not worth dropping a whole idea over; the report
        # renders it only when there is one. A repeated or brief-echoing hook is
        # a different matter, and takes its idea down with it: the hook is the
        # reason the idea is on the page.
        hook = strip_markdown(entry.get("hook"))[:MAX_HOOK_CHARS].strip()
        if hook:
            stem = _hook_stem(hook)
            if stem in hook_stems:
                logger.info("dropped an idea whose hook repeats an earlier one: %r", hook)
                continue
            if _echoes(hook, profile):
                logger.info("dropped an idea whose hook echoes the user profile: %r", hook)
                continue
            named = names.intersection(_hook_words(hook))
            if named:
                logger.info(
                    "dropped an idea whose hook names the user's own %s: %r",
                    ", ".join(sorted(named)), hook,
                )
                continue
            hook_stems.add(stem)
        ideas.append(
            {
                "title": title,
                "hook": hook,
                "format": pinned_format(
                    strip_markdown(entry.get("format")) or "Content"
                ),
                "angle": strip_markdown(entry.get("angle")),
                "rationale": strip_markdown(entry.get("rationale")),
                "addresses": strip_markdown(entry.get("addresses")),
                "item_indices": _valid_indices(entry.get("item_indices"), n_items),
            }
        )
    ideas = ideas[:SANITIZE_MAX_CONTENT_IDEAS]

    title = _trim_to_words(strip_markdown(raw.get("report_title")), MAX_REPORT_TITLE_CHARS)

    # One fingerprint set across both lists: the model reliably files the same
    # sentence as a pain point and again as a question, and a report that says the
    # same thing twice under two headings looks unread.
    claimed: set = set()
    pain_points = _sanitize_claims(raw.get("pain_points"), n_items, MAX_PAIN_POINTS, claimed)
    questions = _sanitize_claims(raw.get("questions"), n_items, MAX_QUESTIONS, claimed)

    return {
        "report_title": title or report_title_from_query(query),
        "executive_summary": strip_markdown(raw.get("executive_summary")),
        "topic_descriptions": descriptions,
        "pain_points": pain_points,
        "questions": questions,
        "content_ideas": ideas,
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


_HOOK_RULES = """- "hook" is the literal first line of the post or script, at most {max_hook}
  characters, written to stop a professional feed scroll. Not a description of the
  hook, the words themselves. Use the community's own language, never "In today's post".
- Every hook must contain at least one of these three things: a specific number, a
  short phrase quoted from the evidence, or a concrete named situation. A hook with
  none of them is a slogan and will be rejected.
- A hook is ONE voice. Either it is what a person in these communities said, or it is
  your own observation. Never a quotation with a second sentence bolted onto it.
- NEVER append a promise, an instruction, a reassurance or an offer to a quote.
  These exact shapes are banned, and so is every variation of them:
    [Quote]. It doesn't have to be.
    [Quote]. Here's how.
    [Quote]. Explore these alternatives.
    [Quote]. There is a better way.
  If you have quoted somebody, the hook ends where their words end.
- NEVER write a hook as a question followed by an instruction, an offer or a promise.
  "[Problem]? [Do this]." is banned in every form. The mechanical test: if a hook
  contains a question mark, that question mark must be the LAST character of the
  hook. These all fail it and are all banned:
    Struggling with X? Here is how.
    Clients ask for one quick change? Here's how I stop scope creep.
    Client asked for a partial refund? Here's my contract clause.
    How do you design for someone who cannot decide? My process.
- A hook may never end on a fragment that promises the answer, wherever in the hook
  it appears and whether or not a question came before it. "My process." /
  "Here's how." / "Here's my template." / "Here is what I do." / "My system." are
  banned, and so is every phrasing like them. State the thing, do not advertise it.
- The brand, product, service or tool of the person you are writing for may never
  appear inside a hook, in any voice: not quoted, not as "my [product]", not as a
  passing mention of the tool they sell. A hook that names it reads as though a
  stranger on Reddit endorsed them, which is a quote nobody said. If the idea is
  about their product, that belongs in "angle" and "rationale". Never in "hook".
- A hook may never restate the research question, and may never repeat back the
  self-description of the person you are writing for. Both of those are the brief.
  The brief is not an opening line and nobody on Reddit ever said it.
- Every hook must open on different words. No two hooks in this response may begin
  with the same phrase, and no two may be one sentence with two different endings.
- Good hooks, because each one is a scene or a number a real person could have lived:
    I lost four projects to the same eight seconds of silence.
    250 cold emails last quarter. One reply worth having.
    Unlimited revisions is not standard. It is just unpriced.
- Bad hooks, because each one could top any post about anything:
    Struggling to price your work? Here is the fix.
    Want more clients? Read this.
    Tired of scope creep? Try these three steps."""


def _synthesis_prompt(
    items: Sequence[dict],
    topics: Sequence[dict],
    query: str,
    profile: str,
    subreddits: Sequence[str],
    preview_chars: int = BODY_PREVIEW_CHARS,
    content_types: Optional[Sequence[str]] = None,
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

    allowed_formats = chosen_formats(content_types)
    if len(allowed_formats) == 1:
        format_rule = (
            '- "format" must be exactly "{}" for EVERY idea. They asked for that one '
            "format, so write every idea to work in it.".format(allowed_formats[0])
        )
    elif allowed_formats:
        format_rule = (
            '- "format" must be exactly one of these {n} labels: {labels}. They asked '
            "for those formats and no others, so every idea has to work in one of "
            "them. Spread the ideas roughly evenly across the {n} formats rather than "
            "writing most of them in one, and pick the format that suits each "
            "idea.".format(n=len(allowed_formats), labels=", ".join(allowed_formats))
        )
    else:
        format_rule = '- "format" must be exactly one of these: {}'.format(
            ", ".join(IDEA_FORMATS)
        )

    own_brand = "their own product or brand"
    if profile:
        own_brand = "their own product, brand or service"

    return """You are a content strategist for someone growing an audience on LinkedIn.

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
  "report_title": "...",
  "executive_summary": "...",
  "topic_descriptions": [{{"id": 1, "label": "<exact label>", "finding": "..."}}],
  "pain_points": [{{"text": "...", "item_indices": [4, 9]}}],
  "questions": [{{"text": "...", "item_indices": [4, 9]}}],
  "content_ideas": [{{"title": "...", "hook": "...", "format": "...",
                      "angle": "...", "rationale": "...", "addresses": "...",
                      "item_indices": [4]}}]
}}

Rules:
- "report_title": a plain title for this report, at most {max_title} characters.
  Name the subject the way the people in these communities would, not the way a
  consultant would. No markdown, no colon-heavy headline formula, no quotation marks.
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
  terms. Not categories, specific problems. Each one at least a full clause.
- "questions": the {max_q} questions people are actually asking, phrased as questions.
  A question must not restate a pain point you already listed; if the only thing you
  can write is the same sentence with a question mark, give a different question.
- "content_ideas": {min_ideas} to {max_ideas} posts they could publish. No two may
  make the same point in different words.
{hook_rules}
- At most 2 of the {max_ideas} ideas may name {own_brand}. Every other idea has to
  earn attention from the insight itself, because a feed full of product mentions
  gets scrolled past.
{format_rule}
- "angle" is the specific take in one sentence. "addresses" names the pain point or
  topic it answers. "rationale" is one sentence on why this audience would engage.
- Every "item_indices" must contain 1-3 item numbers taken from the numbered list
  above, chosen because they directly support the point. Never invent a number
  that is not shown above.
- Do not include URLs, links, image syntax or markdown formatting in any string.
- Never use an em dash or an en dash in any string you return. Use a comma or
  start a new sentence instead.

No markdown fences, no commentary. JSON only.""".format(
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
        max_title=MAX_REPORT_TITLE_CHARS,
        hook_rules=_HOOK_RULES.format(max_hook=MAX_HOOK_CHARS),
        format_rule=format_rule,
        own_brand=own_brand,
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


def _triage_pool(items: Sequence[dict], scores: Sequence[float], cap: int) -> List[int]:
    """Positions of the strongest candidates by base score, worth a triage call.

    Positions rather than items, because the position in the source pool is half of
    the key a rating is filed under (see relevance_key).
    """
    paired = sorted(zip(scores, range(len(items))), key=lambda entry: (-entry[0], entry[1]))
    return [index for _, index in paired[:cap]]


def top_items(items: Sequence[dict], cap: int) -> List[dict]:
    """The `cap` strongest items by engagement and recency, best first.

    The public face of the same deterministic pre-rank the triage uses to decide
    which items are worth a model call. Retrieval needs it for a different reason:
    comments are now fetched per post, so something has to choose which posts are
    worth spending a request on before any model has read anything. Sharing the
    ranking means the posts we gather evidence for are the posts most likely to
    survive selection.

    Judging eligibility is the caller's job, not this function's: pass in the
    items that could usefully be fetched and this returns the best of them.
    """
    if cap <= 0:
        return []
    pool = [item for item in items or [] if isinstance(item, dict)]
    if not pool:
        return []
    positions = _triage_pool(pool, _base_scores(pool, time.time()), cap)
    return [pool[index] for index in positions]


def _empty_qualifier_counts() -> Dict[str, int]:
    return {"confirmed": 0, "unconfirmed": 0, "violated": 0}


def analysed_qualifier_counts(items: Sequence[dict]) -> Dict[str, int]:
    """Count the qualifier tiers of the items that actually got analysed.

    Reads the `qualifier_tier` stamp _select_sample left on each item. It has to be
    a stamp rather than a second lookup, because relevance_key depends on the item's
    position in its PRE-ranking pool, and ranking destroys exactly that. An item
    with no stamp was never triaged, and an untriaged item ranks in the unconfirmed
    tier, so that is what it counts as here too.
    """
    counts = _empty_qualifier_counts()
    for item in items or []:
        tier = (item or {}).get("qualifier_tier")
        counts[tier if tier in counts else "unconfirmed"] += 1
    return counts


def _select_sample(
    posts: Sequence[dict],
    comments: Sequence[dict],
    query: str,
    profile: str,
    soft_deadline: Optional[float] = None,
) -> Tuple[List[dict], List[dict], dict]:
    """Rank both pools for selection and say how the ranking was decided.

    Returns (ranked_posts, ranked_comments, selection). The model rates relevance
    for the strongest candidates only (a triage call over the whole pool would
    spend tokens rating items that could never win a slot anyway); Python computes
    every ranking decision from those ratings.

    `soft_deadline` is an absolute time.monotonic() value, threaded down to the
    triage so it stops issuing batches rather than overrunning the run's budget.
    Past it, neither the decomposition call nor the triage is made at all.
    """
    posts = [item for item in posts or [] if isinstance(item, dict)]
    comments = [item for item in comments or [] if isinstance(item, dict)]
    now = time.time()

    post_positions = _triage_pool(posts, _base_scores(posts, now), TRIAGE_MAX_POSTS)
    comment_positions = _triage_pool(
        comments, _base_scores(comments, now), TRIAGE_MAX_COMMENTS
    )
    pool = [posts[index] for index in post_positions] + [
        comments[index] for index in comment_positions
    ]
    # Parallel to `pool`: the key each pooled item's rating gets filed under, built
    # from the item's position in its OWN pool rather than in the merged one.
    pool_keys = [relevance_key("post", posts[index], index) for index in post_positions] + [
        relevance_key("comment", comments[index], index) for index in comment_positions
    ]

    # One decomposition per run, immediately before the triage that consumes it, so
    # every batch is judged against the same reading of the brief.
    brief = _as_brief(query)
    ratings = None
    if query and not _past(soft_deadline):
        brief = _as_brief(decompose_brief(query, profile))
        ratings = _call(
            rate_relevance, pool, query, profile,
            brief=brief, soft_deadline=soft_deadline,
        )

    unreliable: List[bool] = []
    if ratings is None:
        relevance: Optional[Dict[Tuple[str, str], Verdict]] = None
        selection = {
            "method": "deterministic-fallback",
            "triaged": 0,
            "relevance_counts": {"0": 0, "1": 0, "2": 0},
        }
    else:
        relevance = {}
        counts = {"0": 0, "1": 0, "2": 0}
        # met / violated / unknown per requirement, in the brief's own order.
        per_requirement = [
            {"met": 0, "violated": 0, "unknown": 0} for _ in brief.requirements
        ]
        # First pass counts the verdicts. The tier an item lands in cannot be decided
        # here, because whether a requirement is reliable enough to tier on is a
        # property of the WHOLE batch of verdicts, not of any one item's.
        accepted: List[Tuple[int, Verdict]] = []
        for position, value in ratings.items():
            if not (1 <= position <= len(pool_keys)):
                continue
            verdict = _as_verdict(value)
            relevance[pool_keys[position - 1]] = verdict
            counts[str(verdict.topic)] += 1
            accepted.append((position, verdict))
            for index, char in enumerate(verdict.qualifiers[: len(per_requirement)]):
                key = {"y": "met", "n": "violated"}.get(char, "unknown")
                per_requirement[index][key] += 1

        # Then the batch-wide judgement, once, and the tiers that follow from it.
        unreliable = [requirement_unreliable(entry) for entry in per_requirement]
        qualifier_counts = _empty_qualifier_counts()
        for position, verdict in accepted:
            tier = qualifier_tier(verdict, unreliable)
            qualifier_counts[tier] += 1
            # The stamp survives ranking, which is the only reason the analysed
            # sample can be counted at all (see analysed_qualifier_counts).
            pool[position - 1]["qualifier_tier"] = tier

        selection = {
            "method": "relevance+engagement",
            "triaged": len(relevance),
            "relevance_counts": counts,
        }
        if brief.requirements:
            # Only ever written when the brief actually stated a requirement, so a
            # topical run's selection block is the exact shape it has always been.
            selection["method"] = "relevance+qualifiers"
            selection["pool"] = len(pool)
            selection["topic"] = brief.topic
            selection["requirements"] = [
                dict(
                    text=requirement.text,
                    kind=requirement.kind,
                    unreliable=unreliable[index],
                    **per_requirement[index]
                )
                for index, requirement in enumerate(brief.requirements)
            ]
            selection["qualifier_counts"] = qualifier_counts

    ranked_posts, ranked_comments = rank_items(posts, comments, relevance, now, unreliable)
    return ranked_posts, ranked_comments, selection


def classify_sample(
    posts: Sequence[dict],
    comments: Sequence[dict],
    subreddits: Sequence[str],
    query: str = "",
    profile: str = "",
    soft_deadline: Optional[float] = None,
) -> Tuple[List[dict], int, dict]:
    """Select the best sample, fit it to the token budget, classify it, shrink and
    retry if rejected.

    Returns (items, preview_chars, classification). The items list is authoritative
    from here on: every index in the classification refers to it.

    `soft_deadline` reaches the selection triage, which is several uninterruptible
    model calls and the one stage that can overrun a run's budget invisibly.
    """
    if not posts and not comments:
        raise ValueError("There was nothing to analyse in this sample.")

    # Ranked once, outside the refit loop: a refit changes how much of the ranking
    # is used, never the ranking itself.
    ranked_posts, ranked_comments, selection = _select_sample(
        posts, comments, query, profile, soft_deadline
    )

    # Local to this run on purpose. A rejection tells us this prompt was too big for
    # the provider right now, which is not a reason to shrink every other report the
    # process will ever produce, and a module-level version of this number races
    # between two concurrent runs.
    budget = token_budget()
    for attempt in range(MAX_REFIT_ATTEMPTS):
        items, preview_chars = fit_sample(ranked_posts, ranked_comments, subreddits, budget)
        try:
            classification = classify_items(items, subreddits, preview_chars)
            classification["capacity_limited"] = is_capacity_limited(items, preview_chars, posts, comments)
            # Copied rather than mutated: which items got analysed is decided per
            # refit attempt, and a shared dict would carry the losing attempt's
            # counts. Present only for a brief that stated a requirement.
            if "qualifier_counts" in selection:
                selection = dict(selection)
                selection["qualifier_analysed"] = analysed_qualifier_counts(items)
            classification["selection"] = selection
            return items, preview_chars, classification
        except PayloadTooLarge:
            if attempt == MAX_REFIT_ATTEMPTS - 1:
                raise ValueError(
                    "This sample is larger than the analysis service can take right "
                    "now. Please try again in a moment."
                )
            # A rejection means the provider counts this prompt as bigger than our
            # estimate did, so this run's budget is shaved and the ladder is tried
            # again a rung or two lower.
            budget = int(budget * 0.75)
            logger.warning("refitting sample against a %s token budget", budget)

    raise ValueError(_UNAVAILABLE)


def synthesize(
    items: Sequence[dict],
    topics: Sequence[dict],
    query: str,
    profile: str,
    subreddits: Sequence[str],
    preview_chars: int = BODY_PREVIEW_CHARS,
    content_types: Optional[Sequence[str]] = None,
) -> dict:
    """Call 2: turn the labels into publishable content ideas with evidence."""
    logger.info(
        "synthesising ideas for %r (content types: %s)",
        query, ", ".join(content_types or []) or "any",
    )
    try:
        raw, model = _chat_json(
            _synthesis_prompt(
                items, topics, query, profile, subreddits, preview_chars, content_types
            ),
            SYNTHESIS_MAX_TOKENS,
            "synthesis",
        )
    except PayloadTooLarge:
        logger.warning("synthesis payload rejected; retrying with a tighter listing")
        # Only the listing shrinks. The output ceiling stays where it is: the request
        # was rejected for what we sent, and cutting the model's room to answer would
        # trade a rejection for a truncated, unparseable one.
        raw, model = _chat_json(
            _synthesis_prompt(items, topics, query, profile, subreddits, 90, content_types),
            SYNTHESIS_MAX_TOKENS,
            "synthesis",
        )
    result = sanitize_synthesis(raw, len(items), topics, query, content_types, profile)
    result["model"] = model
    matched = len(result["topic_descriptions"])
    if topics and matched < len(topics):
        logger.warning("only %s of %s topics came back with a finding", matched, len(topics))
    if len(result["content_ideas"]) < SANITIZE_MIN_CONTENT_IDEAS:
        logger.warning(
            "synthesis returned only %s usable ideas, below the floor of %s",
            len(result["content_ideas"]), SANITIZE_MIN_CONTENT_IDEAS,
        )
    logger.info(
        "synthesis: %s topic findings, %s pain points, %s questions, %s ideas",
        matched,
        len(result["pain_points"]),
        len(result["questions"]),
        len(result["content_ideas"]),
    )
    return result


# ── Draft post ────────────────────────────────────────────────────────────────
_HASHTAG_LINE_PATTERN = re.compile(r"^#\w[\w-]*(\s+#\w[\w-]*)*$")
_LIST_MARKER_PATTERN = re.compile(r"^\s*(?:[#>]+|[-*+]|\d+[.)])\s+")


def _sanitize_post_line(line: str) -> str:
    """One line of a draft, flattened but not merged into its neighbours."""
    return strip_markdown(_LIST_MARKER_PATTERN.sub("", line))


def _cap_hashtags(lines: List[str]) -> List[str]:
    """Trim a trailing hashtag line to the agreed maximum.

    Models reliably overshoot hashtags, and a wall of twelve of them is the clearest
    possible signal that a post was machine-written. Only a line that is purely
    hashtags is touched, so a hashtag used mid-sentence is left alone.
    """
    for position in range(len(lines) - 1, -1, -1):
        line = lines[position]
        if not line:
            continue
        if _HASHTAG_LINE_PATTERN.match(line):
            tags = line.split()
            if len(tags) > DRAFT_MAX_HASHTAGS:
                logger.info("trimmed %s hashtags to %s", len(tags), DRAFT_MAX_HASHTAGS)
                lines[position] = " ".join(tags[:DRAFT_MAX_HASHTAGS])
        break
    return lines


def sanitize_post(value: Any) -> str:
    """Clean a draft without destroying its shape.

    `strip_markdown` flattens all whitespace, which is right for a one-line field and
    wrong for a post: the blank lines between short paragraphs are what makes it
    readable in a feed. So it is applied per line, and the line structure is rebuilt
    around it. Runs of blank lines collapse to one, because two blank lines look like
    a mistake rather than a pause.
    """
    raw = "" if value is None else str(value)
    lines = [_sanitize_post_line(line) for line in raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")]

    kept: List[str] = []
    for line in lines:
        if not line and (not kept or not kept[-1]):
            continue
        kept.append(line)
    while kept and not kept[-1]:
        kept.pop()

    return "\n".join(_cap_hashtags(kept))


# LinkedIn's own poll UI refuses more than four options, so a fifth one is not a
# stylistic slip, it is a draft that cannot be posted as written.
DRAFT_MAX_POLL_OPTIONS = 4


def _cap_poll_options(post: str) -> str:
    """Drop any "Option:" line past the fourth so a poll draft stays postable.

    The prompt asks for two to four options, but the model sometimes overshoots.
    Non-option lines and the order of everything kept are left untouched.
    """
    lines = post.split("\n")
    kept: List[str] = []
    options_seen = 0
    for line in lines:
        if line.startswith("Option:"):
            options_seen += 1
            if options_seen > DRAFT_MAX_POLL_OPTIONS:
                continue
        kept.append(line)
    return "\n".join(kept)


def _evidence_lines(evidence: Sequence[Any]) -> str:
    lines = []
    for raw in list(evidence or [])[:DRAFT_MAX_EVIDENCE]:
        text = strip_markdown(raw)[:DRAFT_EVIDENCE_CHARS].strip()
        if text:
            lines.append("  - {}".format(text))
    return "\n".join(lines)


def _draft_format(idea: dict) -> str:
    """The idea's format, normalised to one this module knows how to write."""
    wanted = strip_markdown((idea or {}).get("format")).lower()
    for label in DRAFT_FORMAT_RULES:
        if label.lower() == wanted:
            return label
    # A partial match covers "Carousel" for "Carousel (PDF)" and similar near
    # misses; anything else is written as a plain feed post.
    for label in DRAFT_FORMAT_RULES:
        head = label.split(" (")[0].lower()
        if wanted and (wanted.startswith(head) or head.startswith(wanted)):
            return label
    return "Text post"


def _draft_prompt(idea: dict, evidence: Sequence[Any], query: str, profile: str) -> str:
    idea = idea if isinstance(idea, dict) else {}
    excerpts = _evidence_lines(evidence)
    fmt = _draft_format(idea)
    min_words, max_words = DRAFT_FORMAT_WORDS[fmt]

    context = []
    if query:
        context.append("They are building an audience around: {}".format(strip_markdown(query)))
    if profile:
        context.append("They describe themselves as: {}".format(strip_markdown(profile)))
    context_block = "\n".join(context)

    brief = []
    for field, caption in (
        ("title", "Idea"),
        ("hook", "Opening line to use"),
        ("format", "Format"),
        ("angle", "Angle"),
        ("addresses", "What it answers"),
    ):
        value = strip_markdown(idea.get(field))
        if value:
            brief.append("  {}: {}".format(caption, value))

    return """You are ghostwriting a LinkedIn post for the person described below. You
write the way a practitioner talks: specific, plain, and worth someone's time.

{context}

Write the post for this idea:

{brief}

Source excerpts from the Reddit discussions this idea came from. Draw the substance
and the specifics from these. Do not quote any of them at length and do not name
Reddit or any subreddit:

{excerpts}

Return ONLY a JSON object with exactly this key:

{{
  "post": "..."
}}

Rules for "post":
- Open with the given opening line. You may sharpen its wording, but keep its promise
  and keep it as the first line.
- {format_rule}
- {min_words} to {max_words} words in total. Use "\\n\\n" between blocks inside the
  JSON string, because a wall of text does not get read in a feed.
- Make one point and support it with something concrete from the excerpts: a number, a
  named situation, a real objection. Cut every sentence that could apply to any topic.
- EVERY specific claim must come from the excerpts above. Never invent a statistic, a
  percentage, a survey, a study, a named tool, a named company, a client story or a
  personal anecdote that is not in them. If you cannot point to the line in the
  excerpts that a sentence came from, do not write that sentence.
- If the excerpts have no concrete specifics to draw on, write a clear, opinionated
  piece from the angle instead. A strong opinion is honest. An invented number is not.
- Do not put numbers on things the excerpts did not count. No "3 out of 4", no
  "most people", no "studies show".
- End with one line that invites a reply, phrased as a genuine question rather than
  "What do you think?".
- Then at most {max_hashtags} hashtags on the final line. Zero is fine.
- Plain text only. No markdown, no bold, no bullet characters, no headings, no links,
  no URLs, no emoji.
- Never use an em dash or an en dash. Use a comma or start a new sentence instead.
- Never mention AI, research, analysis, "data shows", or how this post was produced.
  This is the author speaking from their own experience.

No markdown fences, no commentary. JSON only.""".format(
        context=context_block or "  (no extra context given)",
        brief="\n".join(brief) or "  (no idea details given)",
        excerpts=excerpts or "  (no excerpts available)",
        format_rule=DRAFT_FORMAT_RULES[fmt],
        min_words=min_words,
        max_words=max_words,
        max_hashtags=DRAFT_MAX_HASHTAGS,
    )


def draft_post(
    idea: dict, evidence: Sequence[Any] = (), query: str = "", profile: str = ""
) -> dict:
    """Turn one content idea into a post the user can publish as written.

    Returns {"post": str}. Thinking stays on: this is the one call whose whole output
    is prose a person will put their name on, so it is worth the extra seconds.

    Raises ValueError with a user-ready sentence, like the other public calls, so the
    caller can show it without translating anything.
    """
    logger.info("drafting a post for %r", strip_markdown((idea or {}).get("title"))[:60])
    raw, model = _chat_json(
        _draft_prompt(idea or {}, evidence, query, profile), DRAFT_MAX_TOKENS, "draft"
    )

    post = sanitize_post(raw.get("post"))
    if not post:
        logger.error("draft came back with no usable text")
        raise ValueError(_DRAFT_FAILED)

    fmt = _draft_format(idea or {})
    if fmt == "Poll":
        capped = _cap_poll_options(post)
        if capped != post:
            logger.info("trimmed a poll draft down to %s options", DRAFT_MAX_POLL_OPTIONS)
            post = capped

    words = len(post.split())
    min_words, max_words = DRAFT_FORMAT_WORDS[fmt]
    if not min_words * 0.7 <= words <= max_words * 1.3:
        # Worth knowing about, not worth refusing: a post 20 words over still reads
        # fine, and the user can see the length for themselves.
        logger.info("draft length %s words is outside the %s to %s target",
                    words, min_words, max_words)
    logger.info("draft complete with %s: %s words, %s lines",
                model, words, post.count("\n") + 1)
    return {"post": post}
