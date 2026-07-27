import {
  isBundleV4,
  type Bundle,
  type DraftRequest,
  type DraftResponse,
  type ReportRequest,
  type SampleResponse,
  type SavedReport,
  type StreamLine,
} from "./types";

/**
 * A run ends in exactly one of these. `invalid` carries per-field validation
 * messages from the engine; `error` carries one already user-ready sentence.
 *
 * The bundle a `report` outcome carries already has `report_id` stashed on it
 * when the server saved the run: the id arrives beside the bundle on the
 * terminal line, and putting it on the bundle here means every consumer
 * downstream (history, exports, the draft dialog) reads it from the one object
 * they already pass around.
 */
export type RunOutcome =
  | { kind: "report"; bundle: Bundle }
  | { kind: "invalid"; errors: string[] }
  | { kind: "error"; message: string }
  | { kind: "aborted" };

const STREAM_DIED =
  "The connection to the analysis service dropped before the report was finished. Please try again.";
const UNREADABLE =
  "The analysis service sent something this app could not read. Please try again.";

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

function parseLine(line: string): StreamLine | null {
  try {
    const parsed: unknown = JSON.parse(line);
    if (!parsed || typeof parsed !== "object") return null;
    const type = (parsed as { type?: unknown }).type;
    if (type === "progress" || type === "report" || type === "invalid" || type === "error") {
      return parsed as StreamLine;
    }
    return null;
  } catch {
    return null;
  }
}

/**
 * POST /api/report and read the NDJSON stream line by line.
 *
 * `onProgress` fires for every progress line as it arrives; the promise
 * resolves with the terminal line. A stream that ends without a terminal line
 * resolves as an error rather than hanging or throwing something raw at the UI.
 */
export async function runReport(
  request: ReportRequest,
  options: { onProgress?: (message: string) => void; signal?: AbortSignal } = {},
): Promise<RunOutcome> {
  const { onProgress, signal } = options;

  let response: Response;
  try {
    response = await fetch("/api/report", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
      signal,
    });
  } catch (error) {
    if (isAbort(error)) return { kind: "aborted" };
    return {
      kind: "error",
      message:
        "Could not reach the analysis service. Check your connection and try again.",
    };
  }

  if (!response.ok || !response.body) {
    // A non-streaming failure (a proxy error page, a crashed function) has no
    // usable body for the user, so it gets one sentence rather than a status code.
    return {
      kind: "error",
      message: `The analysis service returned an unexpected response (${response.status}). Please try again.`,
    };
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let terminal: RunOutcome | null = null;
  let sawUnparseable = false;

  const handle = (raw: string) => {
    const line = raw.trim();
    if (!line) return;
    const parsed = parseLine(line);
    if (!parsed) {
      sawUnparseable = true;
      return;
    }
    if (parsed.type === "progress") {
      onProgress?.(parsed.message);
      return;
    }
    if (parsed.type === "report") {
      const savedId =
        typeof parsed.report_id === "string" && parsed.report_id ? parsed.report_id : null;
      terminal = isBundleV4(parsed.bundle)
        ? {
            kind: "report",
            bundle: savedId ? { ...parsed.bundle, report_id: savedId } : parsed.bundle,
          }
        : {
            kind: "error",
            message:
              "That report came back in a format this app does not recognise. Please try again.",
          };
      return;
    }
    if (parsed.type === "invalid") {
      const errors = Array.isArray(parsed.errors) ? parsed.errors.map(String) : [];
      terminal = { kind: "invalid", errors };
      return;
    }
    terminal = { kind: "error", message: String(parsed.message || UNREADABLE) };
  };

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let newline = buffer.indexOf("\n");
      while (newline !== -1) {
        handle(buffer.slice(0, newline));
        buffer = buffer.slice(newline + 1);
        newline = buffer.indexOf("\n");
      }
    }
    buffer += decoder.decode();
    handle(buffer);
  } catch (error) {
    if (isAbort(error)) return { kind: "aborted" };
    // The stream died mid-flight. If a terminal line already arrived it still
    // counts; otherwise the run is lost and the user is told plainly.
    return terminal ?? { kind: "error", message: STREAM_DIED };
  } finally {
    reader.releaseLock();
  }

  if (terminal) return terminal;
  return { kind: "error", message: sawUnparseable ? UNREADABLE : STREAM_DIED };
}

/**
 * GET /api/sample/available. A yes/no answer, so the mount-time check does not
 * pull the whole multi-megabyte sample down on every first paint.
 *
 * A 404 means this build's backend predates the endpoint: the full sample may
 * well still be there, so the optimistic answer is the right one and the button
 * click finds out for certain. Any other failure hides the button.
 */
export async function fetchSampleAvailable(signal?: AbortSignal): Promise<boolean> {
  try {
    const response = await fetch("/api/sample/available", { signal });
    if (response.status === 404) return true;
    if (!response.ok) return false;
    const payload = (await response.json()) as { available?: unknown } | null;
    return payload?.available === true;
  } catch {
    return false;
  }
}

/** GET /api/sample. Returns null when no v3 sample is available. */
export async function fetchSample(signal?: AbortSignal): Promise<Bundle | null> {
  try {
    const response = await fetch("/api/sample", { signal });
    if (!response.ok) return null;
    const payload = (await response.json()) as SampleResponse;
    if (!payload || payload.available !== true) return null;
    if (!isBundleV4(payload.bundle)) return null;
    return { ...payload.bundle, is_sample: true };
  } catch {
    return null;
  }
}

/* ── Saved reports ──────────────────────────────────────────────────────────── */

/**
 * A saved report either loads, is not there, or could not be reached. The three
 * are kept apart because they deserve different sentences: "this link is dead"
 * is final, "storage is down" is worth retrying.
 */
export type SavedReportOutcome =
  | { kind: "report"; bundle: Bundle; drafts: Record<string, string> }
  | { kind: "missing"; message: string }
  | { kind: "error"; message: string };

const REPORT_MISSING = "This report link does not exist or was removed.";
const REPORT_UNREACHABLE =
  "That report could not be loaded just now. Please refresh to try again.";

/**
 * GET /api/reports/{id}.
 *
 * The bundle comes back with `report_id` already stashed on it, so a report
 * opened from a link behaves exactly like one that was just run: the same copy
 * link, the same draft persistence, the same history entry.
 */
export async function fetchReport(
  id: string,
  signal?: AbortSignal,
): Promise<SavedReportOutcome> {
  let response: Response;
  try {
    response = await fetch(`/api/reports/${encodeURIComponent(id)}`, { signal });
  } catch {
    return { kind: "error", message: REPORT_UNREACHABLE };
  }

  if (response.status === 404) {
    const message = await messageFrom(response);
    return { kind: "missing", message: message ?? REPORT_MISSING };
  }
  if (!response.ok) {
    const message = await messageFrom(response);
    return { kind: "error", message: message ?? REPORT_UNREACHABLE };
  }

  let payload: SavedReport | null = null;
  try {
    payload = (await response.json()) as SavedReport;
  } catch {
    payload = null;
  }
  if (!payload || !isBundleV4(payload.bundle)) {
    return {
      kind: "error",
      message:
        "That report was saved in a format this app does not recognise. Please run it again.",
    };
  }

  const drafts: Record<string, string> = {};
  for (const [key, value] of Object.entries(payload.drafts ?? {})) {
    if (typeof value === "string") drafts[key] = value;
  }
  return { kind: "report", bundle: { ...payload.bundle, report_id: id }, drafts };
}

/** The server's own sentence when it sent one, otherwise null. */
async function messageFrom(response: Response): Promise<string | null> {
  try {
    const payload = (await response.json()) as { message?: unknown } | null;
    const message = payload?.message;
    return typeof message === "string" && message.trim() ? message : null;
  } catch {
    return null;
  }
}

/**
 * POST /api/drafts. Save an edited post against a saved report.
 *
 * Resolves to whether it landed and never throws: this fires in the background
 * behind an edit the reader has already made, and an unhandled rejection from a
 * blur handler helps nobody. The caller decides whether silence is acceptable
 * (it is, today: the draft is still cached in the page either way).
 */
export async function saveDraft(
  reportId: string,
  ideaIndex: number,
  post: string,
): Promise<boolean> {
  try {
    const response = await fetch("/api/drafts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ report_id: reportId, idea_index: ideaIndex, post }),
    });
    return response.ok;
  } catch {
    return false;
  }
}

/**
 * POST /api/draft. Plain JSON, no streaming.
 *
 * Resolves rather than throws: the caller renders either a post or a sentence,
 * and a drafting failure is an ordinary state of the dialog, not an exception.
 */
export type DraftOutcome =
  | { kind: "post"; post: string }
  | { kind: "error"; message: string }
  | { kind: "aborted" };

const DRAFT_FAILED =
  "That post could not be written just now. Please try again in a moment.";

const DRAFT_RETRY_DELAY_MS = 700;

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms);
    signal?.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        resolve();
      },
      { once: true },
    );
  });
}

export async function draftPost(
  request: DraftRequest,
  signal?: AbortSignal,
): Promise<DraftOutcome> {
  let response: Response;
  try {
    response = await fetch("/api/draft", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
      signal,
    });
    // One retry, and only for a server-side fault: a 5xx from the model host is
    // usually a blip, while a 4xx means the request itself is wrong and sending
    // it again would just bill a second failure. An abort is never retried.
    if (response.status >= 500 && !signal?.aborted) {
      await sleep(DRAFT_RETRY_DELAY_MS, signal);
      if (signal?.aborted) return { kind: "aborted" };
      response = await fetch("/api/draft", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(request),
        signal,
      });
    }
  } catch (error) {
    if (isAbort(error)) return { kind: "aborted" };
    return {
      kind: "error",
      message: "Could not reach the writing service. Check your connection and try again.",
    };
  }

  let payload: DraftResponse | null = null;
  try {
    payload = (await response.json()) as DraftResponse;
  } catch {
    payload = null;
  }

  // The engine puts a user-ready sentence in `error`, so prefer it over any
  // status code we could invent a message for.
  if (payload && "error" in payload && payload.error) {
    return { kind: "error", message: String(payload.error) };
  }
  if (!response.ok) {
    return { kind: "error", message: DRAFT_FAILED };
  }
  if (payload && "post" in payload && typeof payload.post === "string" && payload.post.trim()) {
    return { kind: "post", post: payload.post };
  }
  return { kind: "error", message: DRAFT_FAILED };
}

export type ExportKind = "markdown" | "ideas-csv" | "posts-csv";

const EXPORT_PATHS: Record<ExportKind, string> = {
  markdown: "/api/export/markdown",
  "ideas-csv": "/api/export/ideas-csv",
  "posts-csv": "/api/export/posts-csv",
};

/**
 * POST the bundle back and save whatever comes down. The filename comes from
 * the server's Content-Disposition (report_export.filename_stem), so exports
 * are named identically however they are produced.
 */
export async function downloadExport(bundle: Bundle, kind: ExportKind): Promise<void> {
  const response = await fetch(EXPORT_PATHS[kind], {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ bundle }),
  });
  if (!response.ok) {
    throw new Error("That export could not be built. Please try again.");
  }

  const blob = await response.blob();
  const disposition = response.headers.get("Content-Disposition") ?? "";
  const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disposition);
  const filename = match ? decodeURIComponent(match[1]) : fallbackName(kind);

  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function fallbackName(kind: ExportKind): string {
  if (kind === "markdown") return "content-ideas.md";
  if (kind === "ideas-csv") return "content-ideas-ideas.csv";
  return "content-ideas-posts.csv";
}
