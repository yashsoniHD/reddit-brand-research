"use client";

/**
 * One dismissible banner above the form.
 *
 * The lifecycle rule this app keeps: any new user action clears whatever the
 * previous one said. A failure banner that outlives the thing it describes
 * reads as a live problem with whatever is on screen now.
 */
export function ErrorBanner({
  messages,
  onDismiss,
}: {
  messages: string[];
  onDismiss: () => void;
}) {
  if (messages.length === 0) return null;

  return (
    <div
      role="alert"
      className="flex items-start gap-4 rounded-xl border border-destructive/40 bg-destructive/8 px-4 py-3"
    >
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        {messages.map((message, index) => (
          <p
            key={`${index}-${message}`}
            className="text-sm leading-relaxed text-foreground"
          >
            {message}
          </p>
        ))}
      </div>
      <button
        type="button"
        onClick={onDismiss}
        className="shrink-0 rounded px-2 py-0.5 text-sm font-medium text-muted-foreground hover:text-foreground"
      >
        Dismiss
      </button>
    </div>
  );
}
