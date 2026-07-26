import { bundleKey } from "./format";
import { isBundleV2, type Bundle } from "./types";

export const MAX_HISTORY = 8;

const STORAGE_KEY = "reddit-content-ideas:history";
/**
 * Bumped whenever the stored shape changes. A mismatch discards the store
 * rather than feeding an old shape to renderers that assume the new one.
 */
const STORE_VERSION = 2;

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

export function loadHistory(): Bundle[] {
  const store = storage();
  if (!store) return [];
  let raw: string | null;
  try {
    raw = store.getItem(STORAGE_KEY);
  } catch {
    return [];
  }
  if (!raw) return [];

  try {
    const parsed = JSON.parse(raw) as StoredHistory;
    if (!parsed || parsed.version !== STORE_VERSION || !Array.isArray(parsed.bundles)) {
      store.removeItem(STORAGE_KEY);
      return [];
    }
    // Every entry is re-validated: a bundle written by an older engine would
    // otherwise crash a section that assumes v2.
    const bundles = parsed.bundles.filter(isBundleV2).slice(0, MAX_HISTORY);
    if (bundles.length !== parsed.bundles.length) saveHistory(bundles);
    return bundles;
  } catch {
    try {
      store.removeItem(STORAGE_KEY);
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
  const candidates = bundles.slice(0, MAX_HISTORY);

  for (let size = candidates.length; size > 0; size -= 1) {
    const payload: StoredHistory = { version: STORE_VERSION, bundles: candidates.slice(0, size) };
    try {
      store.setItem(STORAGE_KEY, JSON.stringify(payload));
      return;
    } catch {
      /* too big: try again with one fewer report */
    }
  }

  try {
    store.removeItem(STORAGE_KEY);
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
