"use client";

/**
 * Staged progress, fed by the real NDJSON progress lines.
 *
 * The engine emits free text, so each line is matched to one of four stages and
 * shown verbatim underneath it. The raw log stays visible: when a run is slow,
 * "Fetching posts from r/x (240 so far)" is the difference between "it is
 * working" and "it is stuck".
 */

const STAGES = [
  { id: "discover", label: "Finding communities", match: /^(finding communities|found )/i },
  { id: "read", label: "Reading posts and comments", match: /^fetching /i },
  { id: "classify", label: "Classifying the sample", match: /^classif/i },
  { id: "write", label: "Writing your content ideas", match: /^writing /i },
] as const;

function stageIndexFor(message: string): number {
  for (let index = STAGES.length - 1; index >= 0; index -= 1) {
    if (STAGES[index].match.test(message)) return index;
  }
  return 0;
}

export function RunProgress({ messages }: { messages: string[] }) {
  const latest = messages[messages.length - 1] ?? "Starting…";
  const current = stageIndexFor(latest);

  return (
    <section
      aria-label="Run progress"
      className="rounded-2xl border bg-card p-6"
      aria-busy="true"
    >
      <ol className="flex list-none flex-col gap-3 p-0">
        {STAGES.map((stage, index) => {
          const done = index < current;
          const active = index === current;
          return (
            <li key={stage.id} className="flex items-start gap-3">
              <span
                aria-hidden="true"
                className={`mt-1.5 size-2.5 shrink-0 rounded-full ${
                  done
                    ? "bg-[var(--brand)]"
                    : active
                      ? "animate-pulse bg-[var(--brand)]"
                      : "bg-border"
                }`}
              />
              <div className="flex min-w-0 flex-col">
                <span
                  className={`text-sm ${
                    active
                      ? "font-medium text-foreground"
                      : done
                        ? "text-foreground"
                        : "text-muted-foreground"
                  }`}
                >
                  {stage.label}
                </span>
                {active ? (
                  <span className="text-xs text-muted-foreground" role="status">
                    {latest}
                  </span>
                ) : null}
              </div>
            </li>
          );
        })}
      </ol>

      {messages.length > 1 ? (
        <details className="mt-5">
          <summary className="cursor-pointer text-xs text-muted-foreground underline-offset-4 hover:underline">
            Show all {messages.length} progress updates
          </summary>
          <ul className="mt-2 flex max-h-48 list-none flex-col gap-1 overflow-y-auto p-0 font-mono text-xs text-muted-foreground">
            {messages.map((message, index) => (
              <li key={`${index}-${message}`}>{message}</li>
            ))}
          </ul>
        </details>
      ) : null}
    </section>
  );
}
