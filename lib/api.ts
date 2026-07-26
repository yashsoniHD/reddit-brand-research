import {
  isBundleV2,
  type Bundle,
  type ReportRequest,
  type SampleResponse,
  type StreamLine,
} from "./types";

/**
 * A run ends in exactly one of these. `invalid` carries per-field validation
 * messages from the engine; `error` carries one already user-ready sentence.
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
      terminal = isBundleV2(parsed.bundle)
        ? { kind: "report", bundle: parsed.bundle }
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

/** GET /api/sample. Returns null when no v2 sample is available. */
export async function fetchSample(signal?: AbortSignal): Promise<Bundle | null> {
  try {
    const response = await fetch("/api/sample", { signal });
    if (!response.ok) return null;
    const payload = (await response.json()) as SampleResponse;
    if (!payload || payload.available !== true) return null;
    if (!isBundleV2(payload.bundle)) return null;
    return { ...payload.bundle, is_sample: true };
  } catch {
    return null;
  }
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
