import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Bundle } from "@/lib/types";

/**
 * The submit-then-poll flow inside ContentIdeasApp: a submit starts a run and
 * polls it to a terminal state, a reload resumes whatever run localStorage
 * says is active, and cancelling calls the server rather than aborting a
 * fetch. Every child component below is stubbed to a bare interactive shell so
 * these tests exercise the app's own state machine, not QueryForm's fields or
 * ReportView's rendering, both of which belong to other tests.
 */

vi.mock("@/components/account-menu", () => ({ AccountMenu: () => null }));
vi.mock("@/components/hero", () => ({ Hero: () => null }));
vi.mock("@/components/history-drawer", () => ({ HistoryDrawer: () => null }));
vi.mock("@/components/error-banner", () => ({
  ErrorBanner: ({ messages }: { messages: string[] }) =>
    messages.length > 0 ? <div data-testid="errors">{messages.join(" | ")}</div> : null,
}));
vi.mock("@/components/report/report-view", () => ({
  ReportView: ({ bundle }: { bundle: Bundle }) => (
    <div data-testid="report">{bundle.inputs.query}</div>
  ),
}));
vi.mock("@/components/run-progress", () => ({
  RunProgress: ({ messages }: { messages: string[] }) => (
    <div data-testid="progress">{messages.join(" | ")}</div>
  ),
}));
vi.mock("@/components/app-shell", () => ({
  AppShell: ({ children, action }: { children: ReactNode; action?: ReactNode }) => (
    <div>
      {action}
      {children}
    </div>
  ),
}));
vi.mock("@/components/query-form", () => ({
  QueryForm: ({
    values,
    onChange,
    onSubmit,
    onCancel,
    running,
  }: {
    values: { query: string };
    onChange: (next: { query: string }) => void;
    onSubmit: () => void;
    onCancel: () => void;
    running: boolean;
  }) => (
    <div>
      <input
        data-testid="query"
        value={values.query}
        onChange={(event) => onChange({ ...values, query: event.target.value })}
      />
      <button type="button" data-testid="submit" onClick={onSubmit} disabled={running}>
        submit
      </button>
      <button type="button" data-testid="cancel" onClick={onCancel}>
        cancel
      </button>
    </div>
  ),
}));

const { createRun, pollRun, listRuns, cancelRun, outcomeForFinishedRun } = vi.hoisted(() => ({
  createRun: vi.fn(),
  pollRun: vi.fn(),
  listRuns: vi.fn(),
  cancelRun: vi.fn(),
  outcomeForFinishedRun: vi.fn(),
}));
vi.mock("@/lib/api", () => ({
  createRun,
  pollRun,
  listRuns,
  cancelRun,
  outcomeForFinishedRun,
  fetchSample: vi.fn(async () => null),
  fetchSampleAvailable: vi.fn(async () => false),
}));

const { loadActiveRun, loadActiveRunStartedAt, saveActiveRun, clearActiveRun } = vi.hoisted(() => ({
  loadActiveRun: vi.fn(),
  loadActiveRunStartedAt: vi.fn(),
  saveActiveRun: vi.fn(),
  clearActiveRun: vi.fn(),
}));
vi.mock("@/lib/storage", () => ({
  addToHistory: (history: unknown[], bundle: unknown) => [bundle, ...history],
  loadHistory: () => [],
  saveHistory: vi.fn(),
  loadActiveRun,
  loadActiveRunStartedAt,
  saveActiveRun,
  clearActiveRun,
}));

function bundle(query: string, reportId: string): Bundle {
  return {
    bundle_version: 5,
    report_id: reportId,
    inputs: { query },
    analysis: {},
    sample: {},
    stats: { sentiment: {}, date_range: {}, engagement: {} },
    generated_ts: 1,
    generated_at: query,
  } as unknown as Bundle;
}

const POLL_INTERVAL_MS = 2500;

beforeEach(() => {
  vi.clearAllMocks();
  loadActiveRun.mockReturnValue(null);
  loadActiveRunStartedAt.mockReturnValue(null);
  listRuns.mockResolvedValue([]);
  // jsdom implements neither matchMedia nor scrollIntoView, and the app's
  // scroll-to helper (used after every submit, cancel and finished run) calls
  // both. Bare stubs are enough to keep that code from throwing; scrolling
  // itself is not under test here.
  window.matchMedia =
    window.matchMedia ??
    ((query: string) =>
      ({
        matches: false,
        media: query,
        onchange: null,
        addListener: () => {},
        removeListener: () => {},
        addEventListener: () => {},
        removeEventListener: () => {},
        dispatchEvent: () => false,
      }) as MediaQueryList);
  Element.prototype.scrollIntoView = Element.prototype.scrollIntoView ?? (() => {});
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("submit, then poll to completion", () => {
  it("starts a run on submit and shows the report once polling reports success", async () => {
    createRun.mockResolvedValue({ kind: "started", runId: "run-1" });
    pollRun
      .mockResolvedValueOnce({
        status: "running",
        progress: ["Starting…"],
        report_id: null,
        error: null,
      })
      .mockResolvedValueOnce({
        status: "succeeded",
        progress: ["Starting…", "Writing your content ideas…"],
        report_id: "report-1",
        error: null,
      });
    outcomeForFinishedRun.mockResolvedValue({
      kind: "report",
      bundle: bundle("brand tracking", "report-1"),
    });

    const { ContentIdeasApp } = await import("./content-ideas-app");
    render(<ContentIdeasApp />);

    fireEvent.change(screen.getByTestId("query"), {
      target: { value: "content ideas about brand tracking tools" },
    });
    await act(async () => {
      fireEvent.click(screen.getByTestId("submit"));
    });

    await waitFor(() => expect(createRun).toHaveBeenCalledTimes(1));
    // The run id is persisted before anything else observes it, so a reload
    // one tick later can always recover it.
    expect(saveActiveRun).toHaveBeenCalledWith("run-1");
    await waitFor(() => expect(pollRun).toHaveBeenCalledTimes(1));
    expect(screen.getByTestId("progress")).toHaveTextContent("Starting…");

    // The second poll fires only after the real POLL_INTERVAL_MS timer, so
    // this waits it out rather than faking it: mixing fake timers with
    // testing-library's own polling (`waitFor`) is a documented source of
    // deadlocks, and the real wait is a couple of seconds, not minutes.
    await waitFor(() => expect(pollRun).toHaveBeenCalledTimes(2), {
      timeout: POLL_INTERVAL_MS + 2000,
    });
    await waitFor(() => expect(outcomeForFinishedRun).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByTestId("report")).toHaveTextContent("brand tracking"));
    // The finished run is no longer the page's active one.
    expect(clearActiveRun).toHaveBeenCalled();
    expect(screen.queryByTestId("progress")).not.toBeInTheDocument();
  }, 10000);
});

describe("cancel", () => {
  it("calls the server to cancel and stops polling locally without waiting for it", async () => {
    createRun.mockResolvedValue({ kind: "started", runId: "run-2" });
    pollRun.mockResolvedValue({
      status: "running",
      progress: ["Starting…"],
      report_id: null,
      error: null,
    });
    cancelRun.mockResolvedValue(true);

    const { ContentIdeasApp } = await import("./content-ideas-app");
    render(<ContentIdeasApp />);

    fireEvent.change(screen.getByTestId("query"), {
      target: { value: "content ideas about brand tracking tools" },
    });
    await act(async () => {
      fireEvent.click(screen.getByTestId("submit"));
    });
    await waitFor(() => expect(pollRun).toHaveBeenCalledTimes(1));

    await act(async () => {
      fireEvent.click(screen.getByTestId("cancel"));
    });

    expect(cancelRun).toHaveBeenCalledWith("run-2");
    expect(clearActiveRun).toHaveBeenCalled();
    expect(screen.queryByTestId("progress")).not.toBeInTheDocument();

    const callsBeforeWait = pollRun.mock.calls.length;
    // Wait out more than one poll interval in real time: the local timer was
    // cleared along with everything else, so cancelling must not leave a
    // stray loop still calling pollRun against a run this page no longer owns.
    await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS + 500));
    expect(pollRun.mock.calls.length).toBe(callsBeforeWait);
  }, 10000);
});

describe("resuming on mount", () => {
  it("resumes polling a run id persisted from before a reload", async () => {
    loadActiveRun.mockReturnValue("run-3");
    pollRun.mockResolvedValueOnce({
      status: "succeeded",
      progress: ["Writing your content ideas…"],
      report_id: "report-3",
      error: null,
    });
    outcomeForFinishedRun.mockResolvedValue({
      kind: "report",
      bundle: bundle("resumed topic", "report-3"),
    });

    const { ContentIdeasApp } = await import("./content-ideas-app");
    render(<ContentIdeasApp />);

    await waitFor(() => expect(pollRun).toHaveBeenCalledWith("run-3"));
    expect(createRun).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.getByTestId("report")).toHaveTextContent("resumed topic"));
  });
});

/**
 * The client-side wall-clock cap on the poll loop (H1): a run stuck "queued"
 * or "running" answers every poll validly, so MAX_POLL_FAILURES (which only
 * counts transport/shape errors from pollRun) never fires for it. These use
 * fake timers, unlike the suites above, precisely because the point is to
 * cross many real minutes of elapsed time without the test itself taking
 * minutes: `vi.advanceTimersByTimeAsync` both fires the pending
 * `setTimeout` chain and lets each poll's promise resolve in between.
 *
 * RUN_TIMEOUT_MS mirrors the app's own unexported constant (8 minutes). It
 * is not exported for tests to import, so it is restated here; if the app's
 * value changes, these tests should change with it rather than silently
 * testing a stale number.
 */
describe("wall-clock run timeout", () => {
  const RUN_TIMEOUT_MS = 8 * 60 * 1000;

  it("surfaces a timeout failure when a run stays queued/running past the cap", async () => {
    vi.useFakeTimers();
    createRun.mockResolvedValue({ kind: "started", runId: "run-stuck" });
    pollRun.mockResolvedValue({
      status: "running",
      progress: ["Starting…"],
      report_id: null,
      error: null,
    });

    const { ContentIdeasApp } = await import("./content-ideas-app");
    render(<ContentIdeasApp />);

    fireEvent.change(screen.getByTestId("query"), {
      target: { value: "content ideas about brand tracking tools" },
    });

    // Split into an initial 0ms flush and a further advance, rather than one
    // big jump: before the first poll timer exists, fake timers have nothing
    // to step through and simply fast-forward `Date.now()` to the end of the
    // requested window. The 0ms flush drains the submit's `createRun` await
    // and lets the poll effect register its first real `setTimeout`, so the
    // later advance actually steps through each POLL_INTERVAL_MS tick (and
    // each poll it answers) on the way to the cap, the way it would in the
    // browser.
    await act(async () => {
      fireEvent.click(screen.getByTestId("submit"));
      await vi.advanceTimersByTimeAsync(0);
    });
    // Crosses the cap: a run that never leaves "running" must eventually be
    // given up on even though every single poll it answers is valid.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(RUN_TIMEOUT_MS + POLL_INTERVAL_MS * 2);
    });

    expect(screen.getByTestId("errors")).toHaveTextContent(/taking longer than expected/i);
    expect(clearActiveRun).toHaveBeenCalled();
    expect(screen.queryByTestId("progress")).not.toBeInTheDocument();
    // Confirms the cap fired only after genuinely polling a stuck run
    // through many valid, non-terminal responses, not on the very first tick.
    expect(pollRun.mock.calls.length).toBeGreaterThan(50);
  });

  it("still resolves normally when the run finishes just under the cap", async () => {
    vi.useFakeTimers();
    createRun.mockResolvedValue({ kind: "started", runId: "run-almost" });

    // Ticks fire at t = 0, POLL_INTERVAL_MS, 2*POLL_INTERVAL_MS, ... The
    // 192nd tick lands at t = 191 * 2500 = 477500ms, exactly one poll
    // interval (2500ms) short of the 480000ms cap. Answering "succeeded"
    // there proves the cap's `>` boundary does not cut off a run that was
    // about to finish on its own.
    let calls = 0;
    pollRun.mockImplementation(async () => {
      calls += 1;
      if (calls < 192) {
        return { status: "running", progress: ["Starting…"], report_id: null, error: null };
      }
      return {
        status: "succeeded",
        progress: ["Starting…", "Writing your content ideas…"],
        report_id: "report-almost",
        error: null,
      };
    });
    outcomeForFinishedRun.mockResolvedValue({
      kind: "report",
      bundle: bundle("almost done", "report-almost"),
    });

    const { ContentIdeasApp } = await import("./content-ideas-app");
    render(<ContentIdeasApp />);

    fireEvent.change(screen.getByTestId("query"), {
      target: { value: "content ideas about brand tracking tools" },
    });

    // See the timeout test above for why this is split into an initial 0ms
    // flush (registers the first real poll timer) before the longer advance
    // that actually steps through each tick.
    await act(async () => {
      fireEvent.click(screen.getByTestId("submit"));
      await vi.advanceTimersByTimeAsync(0);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(RUN_TIMEOUT_MS - POLL_INTERVAL_MS);
    });
    // The "succeeded" poll itself lands within the advance above; this flushes
    // the awaited `outcomeForFinishedRun` call chained after it.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(screen.getByTestId("report")).toHaveTextContent("almost done");
    expect(screen.queryByTestId("errors")).not.toBeInTheDocument();
    expect(clearActiveRun).toHaveBeenCalled();
    expect(calls).toBe(192);
  });

  it("times out a resumed run relative to its persisted startedAt, not the resume moment", async () => {
    vi.useFakeTimers();
    const now = 1_700_000_000_000;
    vi.setSystemTime(now);
    // Started just under a full cap ago: the original run began long before
    // this reload, and only ~1s of its budget is left.
    const originalStartedAt = now - (RUN_TIMEOUT_MS - 1000);
    loadActiveRun.mockReturnValue("run-resumed");
    loadActiveRunStartedAt.mockReturnValue(originalStartedAt);
    pollRun.mockResolvedValue({
      status: "running",
      progress: ["Writing your content ideas…"],
      report_id: null,
      error: null,
    });

    const { ContentIdeasApp } = await import("./content-ideas-app");
    render(<ContentIdeasApp />);

    // The resume moment (now) is not the clock's origin: there is only ~1s
    // of budget left, so nothing has timed out yet immediately after mount.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.queryByTestId("errors")).not.toBeInTheDocument();

    // One more poll interval carries elapsed time (measured from
    // originalStartedAt) past the cap.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 2);
    });

    expect(screen.getByTestId("errors")).toHaveTextContent(/taking longer than expected/i);
    expect(createRun).not.toHaveBeenCalled();
  });
});
