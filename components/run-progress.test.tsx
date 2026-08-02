import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import { RunProgress } from "./run-progress";

/**
 * The five stages in order, alongside the exact backend lines that should
 * land on each one. Every string here is copied verbatim from a `say(...)`
 * call in pipeline.py's `run_report` (see the file for line numbers), so a
 * wording change there is meant to break this table rather than pass
 * silently. Scrape mode and search mode are both covered: search mode is the
 * default retrieval path and used to leave the stepper stuck on "Finding
 * communities" for the whole retrieval phase because none of its lines
 * matched anything.
 */
const STAGE_MESSAGES: Array<{ stage: number; message: string }> = [
  // discover
  { stage: 0, message: "Checking the communities you chose…" },
  { stage: 0, message: "Finding communities talking about this…" },
  { stage: 0, message: "Found r/test, r/other. Fetching posts…" },
  // read, scrape mode
  { stage: 1, message: "Fetching posts from r/test…" },
  { stage: 1, message: "Fetching posts from r/test… (12 so far)" },
  { stage: 1, message: "Fetching comments from r/test…" },
  { stage: 1, message: "Fetching posts mentioning \"widgets\" from r/test…" },
  // read, search mode (the previously-unmatched lines)
  { stage: 1, message: "Searching r/test for widgets, gadgets…" },
  { stage: 1, message: "Searching r/test for widgets, gadgets… (7 found)" },
  { stage: 1, message: "Reading the replies to the 20 strongest threads…" },
  { stage: 1, message: "Too little matched those searches. Reading those communities…" },
  {
    stage: 1,
    message: "Too little mentioning \"widgets\". Reading those communities again…",
  },
  // select
  { stage: 2, message: "Selecting the most relevant items…" },
  { stage: 2, message: "Selecting items…" },
  // classify
  { stage: 3, message: "Classifying the sample…" },
  { stage: 3, message: "Classified 40 posts and 15 comments…" },
  // write
  { stage: 4, message: "Writing your content ideas…" },
];

function stepText(stage: number): string {
  return `Step ${stage + 1} of 5`;
}

describe("RunProgress stage mapping", () => {
  afterEach(cleanup);

  for (const { stage, message } of STAGE_MESSAGES) {
    it(`maps ${JSON.stringify(message)} to stage ${stage + 1}`, () => {
      render(<RunProgress messages={[message]} />);
      expect(screen.getByText(stepText(stage), { exact: false })).toBeInTheDocument();
    });
  }
});

describe("RunProgress monotonic stepper", () => {
  afterEach(cleanup);

  it("never regresses when a later message is unrecognised", () => {
    const { rerender } = render(
      <RunProgress
        messages={[
          "Finding communities talking about this…",
          "Searching r/test for widgets…",
        ]}
      />,
    );
    // Search-mode retrieval should already be on stage 2 (index 1).
    expect(screen.getByText(stepText(1), { exact: false })).toBeInTheDocument();

    // An unmatched line (a heartbeat resend, or wording the frontend has
    // never seen) must not walk the stepper backwards.
    rerender(
      <RunProgress
        messages={[
          "Finding communities talking about this…",
          "Searching r/test for widgets…",
          "Some completely unknown line the backend never documented",
        ]}
      />,
    );
    expect(screen.getByText(stepText(1), { exact: false })).toBeInTheDocument();
  });

  it("keeps the furthest stage even when a late message describes an earlier one", () => {
    render(
      <RunProgress
        messages={[
          "Finding communities talking about this…",
          "Selecting the most relevant items…",
          // Arrives late but describes retrieval, an earlier stage.
          "Fetching comments from r/test…",
        ]}
      />,
    );
    expect(screen.getByText(stepText(2), { exact: false })).toBeInTheDocument();
  });

  it("defaults an empty log to the first stage", () => {
    render(<RunProgress messages={[]} />);
    expect(screen.getByText(stepText(0), { exact: false })).toBeInTheDocument();
    expect(screen.getByText("Starting…")).toBeInTheDocument();
  });
});

describe("RunProgress elapsed timer", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it("starts at 0:00 and ticks with real time", () => {
    render(<RunProgress messages={["Finding communities talking about this…"]} />);
    expect(screen.getByText(/0:00/)).toBeInTheDocument();

    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(screen.getByText(/0:05/)).toBeInTheDocument();
  });

  it("rolls over past a minute", () => {
    render(<RunProgress messages={["Searching r/test for widgets…"]} />);

    act(() => {
      vi.advanceTimersByTime(65_000);
    });
    expect(screen.getByText(/1:05/)).toBeInTheDocument();
  });

  it("keeps advancing across repeated heartbeat lines with no new message", () => {
    const { rerender } = render(
      <RunProgress messages={["Searching r/test for widgets…"]} />,
    );
    act(() => {
      vi.advanceTimersByTime(10_000);
    });
    // The API resends the same line every 10s as a heartbeat; the array
    // grows but the text is identical.
    rerender(
      <RunProgress
        messages={["Searching r/test for widgets…", "Searching r/test for widgets…"]}
      />,
    );
    expect(screen.getByText(/0:10/)).toBeInTheDocument();

    act(() => {
      vi.advanceTimersByTime(10_000);
    });
    expect(screen.getByText(/0:20/)).toBeInTheDocument();
  });

  it("clamps to 0:00 instead of going negative when the wall clock steps backward", () => {
    // Date.now() is spied on directly (independent of vi's fake timer clock,
    // which continues to drive setInterval scheduling below) so the test can
    // move "now" backward the same way an NTP correction would, without that
    // also rewinding when the interval itself is due to fire.
    let now = 1_700_000_000_000;
    const dateNowSpy = vi.spyOn(Date, "now").mockImplementation(() => now);

    render(<RunProgress messages={["Finding communities talking about this…"]} />);
    expect(screen.getByText(/0:00/)).toBeInTheDocument();

    now -= 10_000; // the wall clock stepped backward by 10 seconds
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(screen.getByText(/0:00/)).toBeInTheDocument();

    dateNowSpy.mockRestore();
  });
});
