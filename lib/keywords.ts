import {
  MAX_PHRASES,
  MAX_PHRASE_CHARS,
  MAX_PHRASE_WORDS,
  MIN_PHRASE_CHARS,
} from "./types";

/**
 * Client-side mirror of the engine's `keywords.py`.
 *
 * The engine is authoritative: it does the real fetching, filtering and
 * counting, and its parse is the one that decides what a run actually reads.
 * This file exists only so the query form can show a same-page hint of what
 * the server is about to do, the moment the user types a quote, without a
 * round trip. If the two parsers ever drift, the failure mode is a hint that
 * describes the run slightly wrong, never a run that behaves differently
 * from what it says. `lib/validate.ts` documents the same relationship for
 * field validation.
 *
 * Rules mirrored from the engine:
 * - Delimiters are the straight double quote and the curly forms
 *   " " " ‟ ″. A single quote is never a delimiter, or `don't` and
 *   `clients'` would open a phrase and wreck an ordinary brief.
 * - Pairing is strict left to right: the 1st and 2nd delimiter found form a
 *   phrase, the 3rd and 4th form the next, and so on. A delimiter left over
 *   at the end is unbalanced and ignored entirely, its own character simply
 *   stripped out. It is never treated as "phrase runs to the end of the
 *   string": that would turn a typo into a filtered-to-nothing run.
 * - An empty pair (`""`) is ignored silently.
 * - A phrase shorter than MIN_PHRASE_CHARS, longer than MAX_PHRASE_CHARS, or
 *   with more than MAX_PHRASE_WORDS words is refused and reported in
 *   `dropped`.
 * - Beyond MAX_PHRASES accepted phrases, further distinct phrases are also
 *   refused into `dropped`.
 * - Phrases are deduplicated case-insensitively with internal whitespace
 *   collapsed; the first spelling encountered wins.
 */

const DELIMITER_CHARS = '"“”‟″';
const DELIMITER_PATTERN = new RegExp(`[${DELIMITER_CHARS}]`, "g");

export function parsePhrases(text: string): {
  phrases: string[];
  query: string;
  dropped: string[];
} {
  const indices: number[] = [];
  for (let i = 0; i < text.length; i++) {
    if (DELIMITER_CHARS.includes(text[i])) indices.push(i);
  }

  const phrases: string[] = [];
  const dropped: string[] = [];
  const seen = new Set<string>();

  for (let pair = 0; pair + 1 < indices.length; pair += 2) {
    const raw = text.slice(indices[pair] + 1, indices[pair + 1]);
    const normalized = raw.trim().replace(/\s+/g, " ");
    if (normalized === "") continue; // empty pair, ignored silently

    const wordCount = normalized.split(" ").length;
    const outOfRange =
      normalized.length < MIN_PHRASE_CHARS ||
      normalized.length > MAX_PHRASE_CHARS ||
      wordCount > MAX_PHRASE_WORDS;
    if (outOfRange) {
      dropped.push(normalized);
      continue;
    }

    const key = normalized.toLowerCase();
    if (seen.has(key)) continue; // duplicate; first spelling already kept
    if (phrases.length >= MAX_PHRASES) {
      dropped.push(normalized);
      continue;
    }
    seen.add(key);
    phrases.push(normalized);
  }

  const query = text.replace(DELIMITER_PATTERN, "");

  return { phrases, query, dropped };
}
