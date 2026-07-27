import { MAX_MANUAL_SUBREDDITS } from "./types";

/**
 * Client-side input checks, shared by the form and by the submit handler.
 *
 * The form uses them to complain the moment a field loses focus; the submit
 * handler uses the same functions so the two can never disagree about what
 * counts as valid. Nothing here replaces the engine's own validation: it only
 * stops the obvious mistakes before they cost a round trip.
 */

/** Reddit's own rule: 3 to 21 characters, letters, digits, underscore, hyphen. */
export const COMMUNITY_PATTERN = /^[A-Za-z0-9_-]{3,21}$/;

/** Split the comma-separated field, tolerating "r/" prefixes and stray spaces. */
export function parseCommunities(raw: string): string[] {
  return raw
    .split(",")
    .map((name) => name.trim().replace(/^\/?r\//i, "").trim())
    .filter(Boolean);
}

export interface CommunityCheck {
  names: string[];
  /** Every name that fails the pattern, not just the first. */
  invalid: string[];
  /** Ready-to-show sentences, empty when the field is fine. */
  messages: string[];
}

export function checkCommunities(raw: string): CommunityCheck {
  const names = parseCommunities(raw);
  const invalid = names.filter((name) => !COMMUNITY_PATTERN.test(name));
  const messages: string[] = [];

  if (names.length > MAX_MANUAL_SUBREDDITS) {
    messages.push(
      `Please choose ${MAX_MANUAL_SUBREDDITS} communities or fewer. You entered ${names.length}.`,
    );
  }
  if (invalid.length === 1) {
    messages.push(
      `"${invalid[0]}" does not look like a community name. Use 3 to 21 letters, digits, underscores or hyphens.`,
    );
  } else if (invalid.length > 1) {
    messages.push(
      `These do not look like community names: ${invalid
        .map((name) => `"${name}"`)
        .join(", ")}. Use 3 to 21 letters, digits, underscores or hyphens.`,
    );
  }

  return { names, invalid, messages };
}

/**
 * Does this error sentence point at the communities field?
 *
 * Used to decide whether the Settings panel should open itself so the
 * user can see the field the message is about.
 */
export function mentionsCommunities(message: string): boolean {
  return /communit|subreddit|\br\//i.test(message);
}
