import { bundleKey } from "./format";
import { isBundle, type Bundle } from "./types";

export const MAX_HISTORY = 8;

/**
 * History is namespaced per signed-in user. The old single key held whatever the
 * last person to use this browser had generated, so on a shared machine user B
 * opened the app already looking at user A's reports. Each user's history now
 * lives under `<prefix>:<sub>` (their Supabase user id), and sign-out clears the
 * current user's key outright (see `clearHistory`).
 */
const STORAGE_PREFIX = "reddit-content-ideas:history";
// The pre-namespacing key. Removed on load rather than migrated: migrating it
// into whoever is signed in now would hand one user the history of whoever used
// this browser before them, which is the exact leak the namespacing closes.
const LEGACY_STORAGE_KEY = "reddit-content-ideas:history";
/**
 * The id of a run still in flight server-side, namespaced the same way as
 * history and for the same reason: a shared browser must not hand one user's
 * in-progress run to whoever signs in next and reloads the page.
 */
const ACTIVE_RUN_PREFIX = "reddit-content-ideas:active-run";
/**
 * Bumped whenever the stored shape changes. A mismatch discards the store
 * rather than feeding an old shape to renderers that assume the new one.
 */
const STORE_VERSION = 3;

interface StoredHistory {
  version: number;
  bundles: Bundle[];
}

function storage(): Storage | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage;
  } catch {
    // Private-mode and blocked-storage browsers: history simply does not persist.
    return null;
  }
}

/**
 * The `sub` of the signed-in user, read from the Supabase session cookie, or
 * null when it cannot be determined.
 *
 * The session lives in cookies (see lib/supabase/client.ts), which are present
 * synchronously at mount, so this needs no async round trip. @supabase/ssr
 * stores the session as a `sb-<ref>-auth-token` cookie whose value is the
 * session JSON, base64-encoded behind a `base64-` marker and split across `.0`,
 * `.1` chunks when it is large. The identity is read from the access token's
 * `sub` claim first (what the API itself verifies) and from `user.id` as a
 * fallback, so a change to the stored session's exact shape does not silently
 * merge two users' histories. Any parse failure returns null, and the caller
 * falls back to a shared key that sign-out still clears.
 */
function currentUserSub(): string | null {
  if (typeof document === "undefined") return null;
  try {
    const session = readSupabaseSession();
    if (!session) return null;
    const token = typeof session.access_token === "string" ? session.access_token : null;
    const fromToken = token ? jwtSubject(token) : null;
    if (fromToken) return fromToken;
    const user = session.user;
    if (user && typeof user.id === "string" && user.id) return user.id;
    return null;
  } catch {
    return null;
  }
}

interface StoredSession {
  access_token?: unknown;
  user?: { id?: unknown };
}

function readSupabaseSession(): StoredSession | null {
  const jar = document.cookie ? document.cookie.split("; ") : [];
  const chunks: Record<string, string> = {};
  let found = false;
  for (const entry of jar) {
    const eq = entry.indexOf("=");
    if (eq < 0) continue;
    const match = /^(sb-.*-auth-token)(?:\.(\d+))?$/.exec(entry.slice(0, eq));
    if (!match) continue;
    found = true;
    chunks[match[2] ?? "base"] = entry.slice(eq + 1);
  }
  if (!found) return null;

  const ordered: string[] = [];
  if (chunks.base !== undefined) ordered.push(chunks.base);
  for (let i = 0; chunks[String(i)] !== undefined; i += 1) ordered.push(chunks[String(i)]);

  let raw = ordered.join("");
  try {
    raw = decodeURIComponent(raw);
  } catch {
    // Value was not percent-encoded; use it as-is.
  }
  if (raw.startsWith("base64-")) {
    try {
      raw = atob(raw.slice("base64-".length));
    } catch {
      return null;
    }
  }
  try {
    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed)) return (parsed[0] ?? null) as StoredSession | null;
    return parsed as StoredSession;
  } catch {
    return null;
  }
}

function jwtSubject(token: string): string | null {
  const parts = token.split(".");
  if (parts.length < 2) return null;
  try {
    const padded = parts[1].replace(/-/g, "+").replace(/_/g, "/");
    const pad = padded.length % 4 === 0 ? "" : "=".repeat(4 - (padded.length % 4));
    const claims = JSON.parse(atob(padded + pad)) as { sub?: unknown };
    return typeof claims.sub === "string" && claims.sub ? claims.sub : null;
  } catch {
    return null;
  }
}

/** The localStorage key for the signed-in user under a given prefix, or a shared anon key. */
function keyFor(prefix: string): string {
  const sub = currentUserSub();
  return sub ? `${prefix}:${sub}` : `${prefix}:anon`;
}

/** The localStorage key for the signed-in user, or a shared anon key. */
function storageKey(): string {
  return keyFor(STORAGE_PREFIX);
}

/** Drop the pre-namespacing key so its contents can never surface for anyone. */
function dropLegacyKey(store: Storage): void {
  try {
    store.removeItem(LEGACY_STORAGE_KEY);
  } catch {
    /* nothing else to try */
  }
}

export function loadHistory(): Bundle[] {
  const store = storage();
  if (!store) return [];
  // Runs on mount, which is the reliable moment to retire the un-namespaced key.
  dropLegacyKey(store);
  const key = storageKey();
  let raw: string | null;
  try {
    raw = store.getItem(key);
  } catch {
    return [];
  }
  if (!raw) return [];

  try {
    const parsed = JSON.parse(raw) as StoredHistory;
    if (!parsed || parsed.version !== STORE_VERSION || !Array.isArray(parsed.bundles)) {
      store.removeItem(key);
      return [];
    }
    // Every entry is re-validated: a bundle written by an older engine would
    // otherwise crash a section that assumes v2.
    const bundles = parsed.bundles.filter(isBundle).slice(0, MAX_HISTORY);
    if (bundles.length !== parsed.bundles.length) saveHistory(bundles);
    return bundles;
  } catch {
    try {
      store.removeItem(key);
    } catch {
      /* nothing else to try */
    }
    return [];
  }
}

/**
 * Persist, shedding the oldest entries until the write fits.
 *
 * A single bundle carries every post and comment it was built from, so eight of
 * them can outgrow the quota. Losing the oldest report is a far better outcome
 * than losing the whole history to an exception.
 */
export function saveHistory(bundles: Bundle[]): void {
  const store = storage();
  if (!store) return;
  const key = storageKey();
  const candidates = bundles.slice(0, MAX_HISTORY);

  for (let size = candidates.length; size > 0; size -= 1) {
    const payload: StoredHistory = { version: STORE_VERSION, bundles: candidates.slice(0, size) };
    try {
      store.setItem(key, JSON.stringify(payload));
      return;
    } catch {
      /* too big: try again with one fewer report */
    }
  }

  try {
    store.removeItem(key);
  } catch {
    /* nothing else to try */
  }
}

/**
 * Forget the signed-in user's history outright.
 *
 * Called on sign-out, before the session ends, so `storageKey` still resolves to
 * that user's key while the session cookie is present. The full-page reload the
 * sign-out does drops the in-memory copy; this removes the persisted one, so the
 * next person to sign in on this browser inherits nothing.
 *
 * Also drops the persisted active-run id, for the same reason: the next
 * person to sign in on this browser must not have some other user's
 * in-progress run silently resumed for them.
 */
export function clearHistory(): void {
  const store = storage();
  if (!store) return;
  try {
    store.removeItem(storageKey());
  } catch {
    /* nothing else to try */
  }
  dropLegacyKey(store);
  clearActiveRun();
}

/**
 * Persist the id of a run kicked off server-side, so a reload can resume
 * polling it instead of losing track of it. `startedAt` rides along so a
 * resumed run keeps its original deadline: see `loadActiveRunStartedAt`.
 */
export function saveActiveRun(runId: string): void {
  const store = storage();
  if (!store) return;
  try {
    store.setItem(keyFor(ACTIVE_RUN_PREFIX), JSON.stringify({ runId, startedAt: Date.now() }));
  } catch {
    /* best effort: losing this only means a reload cannot resume the run */
  }
}

interface ActiveRunRecord {
  runId: string;
  startedAt: number;
}

/** The persisted active-run record for the signed-in user, or null. */
function readActiveRun(): ActiveRunRecord | null {
  const store = storage();
  if (!store) return null;
  let raw: string | null;
  try {
    raw = store.getItem(keyFor(ACTIVE_RUN_PREFIX));
  } catch {
    return null;
  }
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as { runId?: unknown; startedAt?: unknown };
    if (typeof parsed.runId !== "string" || !parsed.runId) return null;
    return {
      runId: parsed.runId,
      startedAt:
        typeof parsed.startedAt === "number" && Number.isFinite(parsed.startedAt)
          ? parsed.startedAt
          : Number.NaN,
    };
  } catch {
    return null;
  }
}

/** The persisted active run id for the signed-in user, or null. */
export function loadActiveRun(): string | null {
  return readActiveRun()?.runId ?? null;
}

/**
 * When the persisted active run was started, or null if nothing is
 * persisted (or the record predates `startedAt` and carries none).
 *
 * A resumed run reads this on mount so its wall-clock timeout runs from when
 * it actually started, not from the moment this tab happened to reload.
 */
export function loadActiveRunStartedAt(): number | null {
  const startedAt = readActiveRun()?.startedAt;
  return typeof startedAt === "number" && Number.isFinite(startedAt) ? startedAt : null;
}

/** Forget the persisted active run, once it has reached a terminal state. */
export function clearActiveRun(): void {
  const store = storage();
  if (!store) return;
  try {
    store.removeItem(keyFor(ACTIVE_RUN_PREFIX));
  } catch {
    /* nothing else to try */
  }
}

/** Newest first, no duplicates, capped. Sample reports are never remembered. */
export function addToHistory(history: Bundle[], bundle: Bundle): Bundle[] {
  if (bundle.is_sample) return history;
  const key = bundleKey(bundle);
  return [bundle, ...history.filter((entry) => bundleKey(entry) !== key)].slice(0, MAX_HISTORY);
}
