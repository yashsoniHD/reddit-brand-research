import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  clearActiveRun,
  clearHistory,
  loadActiveRun,
  loadActiveRunStartedAt,
  loadHistory,
  saveActiveRun,
  saveHistory,
} from "./storage";
import type { Bundle } from "./types";

/**
 * History is stored per signed-in user, keyed on the `sub` read from the
 * Supabase session cookie, and sign-out clears it. These tests stand in that
 * cookie and assert that one user never sees another's history and that
 * `clearHistory` empties the current user's store.
 */

const STORAGE_PREFIX = "reddit-content-ideas:history";
const LEGACY_KEY = "reddit-content-ideas:history";

function base64Url(value: object): string {
  return btoa(JSON.stringify(value)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/** An access token whose only claim that matters here is `sub`. */
function accessTokenFor(sub: string): string {
  return `${base64Url({ alg: "ES256", typ: "JWT" })}.${base64Url({ sub })}.signature`;
}

/** Write the Supabase session cookie for a given user, the way @supabase/ssr does. */
function signInAs(sub: string): void {
  const session = { access_token: accessTokenFor(sub), user: { id: sub } };
  const encoded = `base64-${btoa(JSON.stringify(session))}`;
  document.cookie = `sb-testproject-auth-token=${encoded}`;
}

function signOutCookies(): void {
  for (const entry of document.cookie ? document.cookie.split("; ") : []) {
    const name = entry.split("=")[0];
    document.cookie = `${name}=; expires=Thu, 01 Jan 1970 00:00:00 GMT`;
  }
}

function bundle(query: string): Bundle {
  return {
    bundle_version: 5,
    inputs: { query },
    analysis: {},
    sample: {},
    stats: { sentiment: {}, date_range: {}, engagement: {} },
    generated_ts: 1,
    generated_at: query,
  } as unknown as Bundle;
}

beforeEach(() => {
  localStorage.clear();
  signOutCookies();
});

afterEach(() => {
  localStorage.clear();
  signOutCookies();
});

describe("per-user history", () => {
  it("keeps one user's history out of another user's view", () => {
    signInAs("user-a");
    saveHistory([bundle("a's report")]);
    expect(loadHistory().map((b) => b.inputs.query)).toEqual(["a's report"]);

    // A different person on the same browser starts empty.
    signOutCookies();
    signInAs("user-b");
    expect(loadHistory()).toEqual([]);

    // And user A still has theirs.
    signOutCookies();
    signInAs("user-a");
    expect(loadHistory().map((b) => b.inputs.query)).toEqual(["a's report"]);
  });

  it("writes under a key namespaced by the user's sub", () => {
    signInAs("user-a");
    saveHistory([bundle("a's report")]);
    expect(localStorage.getItem(`${STORAGE_PREFIX}:user-a`)).not.toBeNull();
    expect(localStorage.getItem(`${STORAGE_PREFIX}:user-b`)).toBeNull();
  });

  it("falls back to a shared anon key when there is no session", () => {
    saveHistory([bundle("signed out")]);
    expect(localStorage.getItem(`${STORAGE_PREFIX}:anon`)).not.toBeNull();
    expect(loadHistory().map((b) => b.inputs.query)).toEqual(["signed out"]);
  });
});

describe("clearHistory", () => {
  it("removes only the current user's history", () => {
    signInAs("user-a");
    saveHistory([bundle("a's report")]);
    signOutCookies();
    signInAs("user-b");
    saveHistory([bundle("b's report")]);

    // While signed in as B, clearing removes B's key and leaves A's alone.
    clearHistory();
    expect(localStorage.getItem(`${STORAGE_PREFIX}:user-b`)).toBeNull();
    expect(localStorage.getItem(`${STORAGE_PREFIX}:user-a`)).not.toBeNull();
  });
});

describe("active run startedAt", () => {
  it("reads back the moment saveActiveRun persisted", () => {
    vi.useFakeTimers();
    try {
      vi.setSystemTime(1_700_000_000_000);
      saveActiveRun("run-1");
      expect(loadActiveRun()).toBe("run-1");
      expect(loadActiveRunStartedAt()).toBe(1_700_000_000_000);
    } finally {
      vi.useRealTimers();
    }
  });

  it("returns null for both once the run is cleared", () => {
    saveActiveRun("run-1");
    clearActiveRun();
    expect(loadActiveRun()).toBeNull();
    expect(loadActiveRunStartedAt()).toBeNull();
  });

  it("returns null when nothing is persisted", () => {
    expect(loadActiveRunStartedAt()).toBeNull();
  });
});

describe("legacy un-namespaced history", () => {
  it("is discarded on load rather than surfacing for the current user", () => {
    localStorage.setItem(
      LEGACY_KEY,
      JSON.stringify({ version: 3, bundles: [bundle("old shared report")] }),
    );
    signInAs("user-a");
    // The legacy key is not this user's, so it must not appear...
    expect(loadHistory()).toEqual([]);
    // ...and it is removed so it can never appear for anyone.
    expect(localStorage.getItem(LEGACY_KEY)).toBeNull();
  });
});
