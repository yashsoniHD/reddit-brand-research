/**
 * The positioning, said once, in the largest type on the page.
 *
 * Flat and factual rather than persuasive: the headline names the output and
 * the input, and the single line under it states the one property that makes
 * the output checkable. The form below explains the rest by existing.
 */
export function Hero() {
  return (
    <div className="mx-auto flex w-full max-w-[46rem] flex-col items-center gap-4 text-center">
      <h1 className="t-display text-balance text-foreground">
        {/* Kept on one line: it is the positioning, and a break through the
            middle of it is the one wrap this headline cannot afford. */}
        <span className="whitespace-nowrap text-[var(--brand-ink)]">Content ideas</span>{" "}
        from real Reddit conversations.
      </h1>

      <p className="t-lead max-w-xl text-balance text-foreground-soft">
        Every idea links to the thread it came from.
      </p>
    </div>
  );
}
