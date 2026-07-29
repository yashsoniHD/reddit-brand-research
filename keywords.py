"""Quoted-phrase parsing and matching for keyword search.

A pure leaf module: stdlib only, no project imports. Both `reddit_data` (the
archive-side prefilter) and `pipeline` (the post-filter and tier ladder) import
this without creating a cycle. Phrase length and count policy are product rules
about what a "reasonable phrase" is, not knowledge about any particular data
source, so they live here rather than in `reddit_data`.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import List, NamedTuple, Optional, Pattern

# ── Phrase policy ─────────────────────────────────────────────────────────────
MAX_PHRASES = 3
MIN_PHRASE_CHARS = 3
MAX_PHRASE_CHARS = 60
MAX_PHRASE_WORDS = 8

# Straight double quote plus every curly double-quote variant a phone keyboard
# or a pasted document is likely to produce, all normalised to `"` before we
# scan. Single quotes (straight or curly) are NEVER delimiters: if `'` opened a
# phrase, "don't" and "clients'" would each open an unterminated quote and wreck
# an ordinary, unquoted brief. That single decision is what keeps this feature
# from being a landmine for the common case.
_CURLY_DOUBLE_QUOTES = "“”‟″"  # " " ‟ ″
_QUOTE_TRANSLATION = {ord(ch): '"' for ch in _CURLY_DOUBLE_QUOTES}


class PhraseParse(NamedTuple):
    phrases: List[str]   # accepted hard filters, first-seen order
    query: str            # the brief with quote characters removed
    dropped: List[str]    # phrases refused, so the report can say why


def parse_phrases(text: Optional[str]) -> PhraseParse:
    """Pull up to MAX_PHRASES quoted phrases out of a free-text brief.

    Never raises: `None` and `""` return empty results, and a badly quoted
    brief degrades to "no phrases found" rather than an error, because a typo
    in a quote mark must never turn into a validation failure for the whole
    query.
    """
    if text is None:
        return PhraseParse([], "", [])

    raw = str(text)
    normalized = raw.translate(_QUOTE_TRANSLATION)

    # The query is simply the brief with every quote character gone. This is
    # correct for every case at once: a stray unbalanced quote is stripped
    # (never treated as "phrase runs to the end of the string"), an empty
    # pair `""` leaves nothing behind, and a wholly quoted brief becomes the
    # same words unquoted.
    query = normalized.replace('"', "")

    segments = normalized.split('"')
    quote_count = normalized.count('"')
    # Strict left-to-right pairing: quote 1 opens, quote 2 closes, quote 3
    # opens the next phrase, and so on. An odd quote_count means the final
    # quote never found a partner, so it is ignored entirely rather than
    # being read as "runs to end of string". Segments at odd indices
    # (1, 3, 5, ...) are the text between a pair; only whole pairs count.
    complete_pairs = quote_count // 2

    phrases: List[str] = []
    dropped: List[str] = []
    seen_lower = set()

    for pair_index in range(complete_pairs):
        candidate = segments[2 * pair_index + 1]
        # Whitespace-collapse before measuring length/words, so "  seo  " is
        # judged as the 3-char phrase "seo", not a padded 7-char string.
        collapsed = " ".join(candidate.split())

        if not collapsed:
            # Empty pair, e.g. `""` or `"   "`. Ignored silently: this is a
            # no-op from the author, not a mistake worth reporting.
            continue

        words = collapsed.split()
        if len(collapsed) < MIN_PHRASE_CHARS or len(collapsed) > MAX_PHRASE_CHARS \
                or len(words) > MAX_PHRASE_WORDS:
            dropped.append(collapsed)
            continue

        key = collapsed.lower()
        if key in seen_lower:
            # Dedupe is case-insensitive and whitespace-normalised, first
            # spelling wins (mirrors reddit_data.parse_subreddit_input). A
            # repeat is redundant, not refused, so it is not reported as
            # dropped either.
            continue

        if len(phrases) >= MAX_PHRASES:
            # AND across phrases fails toward "too narrow", which the loose
            # fallback tier can recover from; there is no ladder for "too
            # broad", so extra phrases are capped rather than silently kept.
            dropped.append(collapsed)
            continue

        phrases.append(collapsed)
        seen_lower.add(key)

    return PhraseParse(phrases, query, dropped)


@lru_cache(maxsize=None)
def phrase_pattern(phrase: str) -> Pattern:
    """Compile a case-insensitive, whitespace-flexible, word-bounded matcher.

    `\\s+` joins words instead of a literal space because the source text
    (a post title plus its body) can have a newline exactly where the phrase
    has a space, and a plain substring check would miss "meta\\nads" for the
    phrase "meta ads".

    The `(?<!\\w)` / `(?!\\w)` boundary guards stop a short phrase from
    matching inside a longer word: at the 3-character floor, "seo" matching
    "Seoul" is a real false positive, not a hypothetical one.

    Each word is `re.escape`d so a phrase containing regex metacharacters
    (like "c++ ads" or "what?") is matched literally and never raises
    `re.error`.
    """
    words = phrase.split()
    body = r"\s+".join(re.escape(word) for word in words)
    return re.compile(r"(?<!\w)" + body + r"(?!\w)", re.IGNORECASE)


def matches_all(text: Optional[str], phrases: List[str]) -> bool:
    """True when every phrase is present in `text`. An empty phrase list is True.

    This is the AND semantics the archive's own `query=` already has, so N
    accepted phrases compose into a single deterministic post-filter.
    """
    if not phrases:
        return True
    haystack = text or ""
    return all(phrase_pattern(phrase).search(haystack) for phrase in phrases)
