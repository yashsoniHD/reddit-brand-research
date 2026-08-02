import { accessToken } from "./supabase/client";
import {
  isBundle,
  type Bundle,
  type DraftRequest,
  type DraftResponse,
  type ReportRequest,
  type SampleResponse,
  type SavedReport,
} from "./types";

/**
 * Every request this module makes, with the session token attached.
 *
 * There is exactly one of these and every call site below goes through it,
 * which is the point: in production vercel.json routes /api/* straight to the
 * Python function, bypassing Next.js entirely, so a bearer token is the only
 * thing that identifies the caller and a single forgotten header would be a
 * route that silently 401s.
 *
 * Cookies cannot do this job. The Python function is a different origin from
 * Next.js as far as the Supabase cookie is concerned in local development, and
 * it has no way to read the chunked cookie format @supabase/ssr writes anyway.
 *
 * Deliberately thin. It adds one header and forwards everything else untouched,
 * so `signal` still aborts and every caller below sees exactly what a plain
 * fetch would have given it. A missing token is not an error here: the
 * request goes anyway and the API answers 401, which keeps one place
 * deciding what signed out means.
 */
async function authedFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const token = await accessToken();
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  return fetch(path, { ...init, headers });
}

/**
 * A run ends in exactly one of these. `invalid` carries per-field validation
 * messages from the engine; `error` carries one already user-ready sentence.
 *
 * The bundle a `report` outcome carries already has `report_id` stashed on it
 * when the server saved the run: `outcomeForFinishedRun` reads it off the
 * `/api/runs/{id}` poll and fetches the saved report, so every consumer
 * downstream (history, exports, the draft dialog) reads it from the one
 * object they already pass around, exactly as when the bundle rode a
 * streamed terminal line.
 *
 * `aborted` survives the move from streaming to polling for `createRun`
 * alone: the initial POST can still be given a signal, and an aborted POST is
 * not a validation failure or a dead service, it is the caller changing its
 * mind before a run even started.
 */
export type RunOutcome =
  | { kind: "report"; bundle: Bundle }
  | { kind: "invalid"; errors: string[] }
  | { kind: "error"; message: string }
  | { kind: "aborted" };

const UNREADABLE =
  "The analysis service sent something this app could not read. Please try again.";
const RUN_FAILED = "The analysis run failed before it finished. Please try again.";
const RUN_LOST_REPORT =
  "That run finished but the report could not be found. Please try again.";

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

/** The five states a run can be in. Mirrors the engine's own run record. */
export type RunStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelling"
  | "cancelled";

function isRunStatus(value: unknown): value is RunStatus {
  return (
    value === "queued" ||
    value === "running" ||
    value === "succeeded" ||
    value === "failed" ||
    value === "cancelling" ||
    value === "cancelled"
  );
}

/** GET /api/runs/{id}. */
export interface RunPoll {
  status: RunStatus;
  progress: string[];
  report_id: string | null;
  error: string | null;
}

/** One row of GET /api/runs, newest first. */
export interface RunSummary {
  id: string;
  status: RunStatus;
  query: string;
  created_at: string;
  report_id: string | null;
}

function isRunSummary(value: unknown): value is RunSummary {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<RunSummary>;
  return (
    typeof candidate.id === "string" &&
    isRunStatus(candidate.status) &&
    typeof candidate.query === "string" &&
    typeof candidate.created_at === "string"
  );
}

/**
 * POST /api/report. Starts a run server-side and returns immediately with its
 * id; the caller polls `pollRun` for progress and the terminal result instead
 * of holding a stream open, which is what lets a run outlive the tab that
 * started it.
 *
 * The validation-failure shape is unchanged from the old streamed contract
 * (`{"type": "invalid", "errors": [...]}` or `{"type": "error", "message":
 * "..."}`), it just now arrives as one JSON body rather than a terminal NDJSON
 * line, on whatever status code the engine sent it on. That shape is checked
 * first, before the HTTP status, for exactly that reason.
 */
export async function createRun(
  request: ReportRequest,
  signal?: AbortSignal,
): Promise<{ kind: "started"; runId: string } | RunOutcome> {
  let response: Response;
  try {
    response = await authedFetch("/api/report", {
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

  let payload: unknown = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }

  const type = (payload as { type?: unknown } | null)?.type;
  if (type === "invalid") {
    const errors = (payload as { errors?: unknown }).errors;
    return { kind: "invalid", errors: Array.isArray(errors) ? errors.map(String) : [] };
  }
  if (type === "error") {
    const message = (payload as { message?: unknown }).message;
    return { kind: "error", message: String(message || UNREADABLE) };
  }

  if (response.status === 202) {
    const runId = (payload as { run_id?: unknown } | null)?.run_id;
    if (typeof runId === "string" && runId) return { kind: "started", runId };
    return { kind: "error", message: UNREADABLE };
  }

  return {
    kind: "error",
    message: `The analysis service returned an unexpected response (${response.status}). Please try again.`,
  };
}

/**
 * GET /api/runs/{id}. Returns null on any transport or shape failure rather
 * than throwing, so a caller polling every couple of seconds can treat one
 * missed beat as exactly that instead of ending the run over a single dropped
 * request.
 */
export async function pollRun(runId: string, signal?: AbortSignal): Promise<RunPoll | null> {
  try {
    const response = await authedFetch(`/api/runs/${encodeURIComponent(runId)}`, {
      signal,
      cache: "no-store",
    });
    if (!response.ok) return null;
    const payload = (await response.json()) as Partial<RunPoll> | null;
    if (!payload || !isRunStatus(payload.status)) return null;
    return {
      status: payload.status,
      progress: Array.isArray(payload.progress) ? payload.progress.map(String) : [],
      report_id: typeof payload.report_id === "string" ? payload.report_id : null,
      error: typeof payload.error === "string" ? payload.error : null,
    };
  } catch {
    return null;
  }
}

/** GET /api/runs. The signed-in user's own runs, newest first. Empty on any failure. */
export async function listRuns(signal?: AbortSignal): Promise<RunSummary[]> {
  try {
    const response = await authedFetch("/api/runs", { signal, cache: "no-store" });
    if (!response.ok) return [];
    const payload = (await response.json()) as { runs?: unknown } | null;
    const runs = Array.isArray(payload?.runs) ? payload.runs : [];
    return runs.filter(isRunSummary);
  } catch {
    return [];
  }
}

/**
 * POST /api/runs/{id}/cancel. Resolves to whether the request landed, not to
 * whether the run has actually stopped: cancellation is asynchronous
 * (`"cancelling"` before `"cancelled"`), and the poll loop is what observes
 * the run actually winding down.
 */
export async function cancelRun(runId: string): Promise<boolean> {
  try {
    const response = await authedFetch(`/api/runs/${encodeURIComponent(runId)}/cancel`, {
      method: "POST",
    });
    return response.ok;
  } catch {
    return false;
  }
}

/**
 * Turn a terminal poll (`"succeeded"` or `"failed"`) into the same
 * `RunOutcome` the UI has always consumed. Calling this on a non-terminal
 * poll is a caller bug, not a state this function tries to make sense of, so
 * it falls through to the generic failure message rather than pretending to
 * know what a "queued" or "cancelled" outcome would mean.
 */
export async function outcomeForFinishedRun(
  poll: RunPoll,
  signal?: AbortSignal,
): Promise<RunOutcome> {
  if (poll.status === "succeeded") {
    if (!poll.report_id) return { kind: "error", message: RUN_LOST_REPORT };
    const saved = await fetchReport(poll.report_id, signal);
    if (saved.kind === "report") return { kind: "report", bundle: saved.bundle };
    return { kind: "error", message: saved.message };
  }
  if (poll.status === "failed") {
    const message = poll.error?.trim();
    return { kind: "error", message: message || RUN_FAILED };
  }
  return { kind: "error", message: RUN_FAILED };
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
    const response = await authedFetch("/api/sample/available", { signal });
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
    const response = await authedFetch("/api/sample", { signal });
    if (!response.ok) return null;
    const payload = (await response.json()) as SampleResponse;
    if (!payload || payload.available !== true) return null;
    if (!isBundle(payload.bundle)) return null;
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
    response = await authedFetch(`/api/reports/${encodeURIComponent(id)}`, { signal });
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
  if (!payload || !isBundle(payload.bundle)) {
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
    const response = await authedFetch("/api/drafts", {
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
    response = await authedFetch("/api/draft", {
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
      response = await authedFetch("/api/draft", {
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

export type ExportKind = "markdown" | "ideas-csv" | "posts-csv" | "comments-csv";

const EXPORT_PATHS: Record<ExportKind, string> = {
  markdown: "/api/export/markdown",
  "ideas-csv": "/api/export/ideas-csv",
  "posts-csv": "/api/export/posts-csv",
  "comments-csv": "/api/export/comments-csv",
};

/**
 * POST the bundle back and save whatever comes down. The filename comes from
 * the server's Content-Disposition (report_export.filename_stem), so exports
 * are named identically however they are produced.
 */
export async function downloadExport(bundle: Bundle, kind: ExportKind): Promise<void> {
  const response = await authedFetch(EXPORT_PATHS[kind], {
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
  if (kind === "posts-csv") return "content-ideas-posts.csv";
  return "content-ideas-comments.csv";
}
