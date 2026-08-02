import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  cancelRun,
  createRun,
  listRuns,
  outcomeForFinishedRun,
  pollRun,
  type RunPoll,
} from "./api";
import type { Bundle } from "./types";

/**
 * The submit-then-poll contract: POST /api/report starts a run and returns its
 * id, GET /api/runs/{id} reports progress and the terminal state, GET
 * /api/runs lists this user's runs, and POST /api/runs/{id}/cancel asks for an
 * early stop. These tests stand in for the network with a stubbed `fetch` and
 * assert the mapping into the RunOutcome shape the UI already knows how to
 * render, so a run reaching "succeeded" still ends up as `{kind: "report",
 * bundle}` exactly as it did when the same terminal state rode a streamed
 * NDJSON line.
 */

function jsonResponse(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  } as unknown as Response;
}

function bundle(query: string): Bundle {
  return {
    bundle_version: 5,
    report_id: "report-1",
    inputs: { query },
    analysis: {},
    sample: {},
    stats: { sentiment: {}, date_range: {}, engagement: {} },
    generated_ts: 1,
    generated_at: query,
  } as unknown as Bundle;
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("createRun", () => {
  it("returns the run id on a 202", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(202, { run_id: "run-1" }));
    const outcome = await createRun({ query: "q", profile: "" });
    expect(outcome).toEqual({ kind: "started", runId: "run-1" });
  });

  it("maps an invalid-shaped body to the invalid outcome regardless of status", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(400, { type: "invalid", errors: ["Add more detail."] }),
    );
    const outcome = await createRun({ query: "q", profile: "" });
    expect(outcome).toEqual({ kind: "invalid", errors: ["Add more detail."] });
  });

  it("maps an error-shaped body to the error outcome", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(500, { type: "error", message: "The engine is down." }),
    );
    const outcome = await createRun({ query: "q", profile: "" });
    expect(outcome).toEqual({ kind: "error", message: "The engine is down." });
  });

  it("reports a generic error for an unexpected response", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(500, {}));
    const outcome = await createRun({ query: "q", profile: "" });
    expect(outcome.kind).toBe("error");
    expect((outcome as { message: string }).message).toContain("500");
  });

  it("reports a generic error when a 202 carries no run id", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(202, {}));
    const outcome = await createRun({ query: "q", profile: "" });
    expect(outcome.kind).toBe("error");
  });

  it("reports aborted when the request is aborted", async () => {
    fetchMock.mockRejectedValueOnce(new DOMException("aborted", "AbortError"));
    const outcome = await createRun({ query: "q", profile: "" });
    expect(outcome).toEqual({ kind: "aborted" });
  });

  it("reports a connection error when the request throws", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("network down"));
    const outcome = await createRun({ query: "q", profile: "" });
    expect(outcome.kind).toBe("error");
  });
});

describe("pollRun", () => {
  it("parses a well-formed poll", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(200, {
        status: "running",
        progress: ["Starting…", "Finding communities…"],
        report_id: null,
        error: null,
      }),
    );
    const poll = await pollRun("run-1");
    expect(poll).toEqual({
      status: "running",
      progress: ["Starting…", "Finding communities…"],
      report_id: null,
      error: null,
    });
  });

  it("returns null on a non-ok response", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(404, { error: "not found" }));
    expect(await pollRun("run-1")).toBeNull();
  });

  it("returns null when the status is not one it recognises", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { status: "unknown-status" }));
    expect(await pollRun("run-1")).toBeNull();
  });

  it("returns null when the request throws", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("network down"));
    expect(await pollRun("run-1")).toBeNull();
  });
});

describe("listRuns", () => {
  it("returns only well-formed rows, newest first as given", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(200, {
        runs: [
          { id: "run-2", status: "running", query: "b", created_at: "t2", report_id: null },
          { not: "a run" },
          { id: "run-1", status: "succeeded", query: "a", created_at: "t1", report_id: "r1" },
        ],
      }),
    );
    const runs = await listRuns();
    expect(runs).toEqual([
      { id: "run-2", status: "running", query: "b", created_at: "t2", report_id: null },
      { id: "run-1", status: "succeeded", query: "a", created_at: "t1", report_id: "r1" },
    ]);
  });

  it("returns an empty list on failure", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(500, {}));
    expect(await listRuns()).toEqual([]);
  });
});

describe("cancelRun", () => {
  it("resolves true when the cancel request lands", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { status: "cancelling" }));
    expect(await cancelRun("run-1")).toBe(true);
  });

  it("resolves false on a failed response", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(404, {}));
    expect(await cancelRun("run-1")).toBe(false);
  });

  it("resolves false when the request throws", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("network down"));
    expect(await cancelRun("run-1")).toBe(false);
  });
});

describe("outcomeForFinishedRun", () => {
  const succeeded = (reportId: string | null): RunPoll => ({
    status: "succeeded",
    progress: [],
    report_id: reportId,
    error: null,
  });
  const failed = (message: string | null): RunPoll => ({
    status: "failed",
    progress: [],
    report_id: null,
    error: message,
  });

  it("fetches and wraps the saved report on success", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(200, { bundle: bundle("hello"), drafts: {} }),
    );
    const outcome = await outcomeForFinishedRun(succeeded("report-1"));
    expect(outcome.kind).toBe("report");
    expect((outcome as { bundle: Bundle }).bundle.report_id).toBe("report-1");
  });

  it("errors when a succeeded run carries no report id", async () => {
    const outcome = await outcomeForFinishedRun(succeeded(null));
    expect(outcome.kind).toBe("error");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("errors when the saved report cannot be loaded", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(404, { message: "Gone" }));
    const outcome = await outcomeForFinishedRun(succeeded("report-1"));
    expect(outcome).toEqual({ kind: "error", message: "Gone" });
  });

  it("surfaces the engine's own error message on failure", async () => {
    const outcome = await outcomeForFinishedRun(failed("The archive timed out."));
    expect(outcome).toEqual({ kind: "error", message: "The archive timed out." });
  });

  it("falls back to a generic message when failure carries none", async () => {
    const outcome = await outcomeForFinishedRun(failed(null));
    expect(outcome.kind).toBe("error");
    expect((outcome as { message: string }).message.length).toBeGreaterThan(0);
  });
});
