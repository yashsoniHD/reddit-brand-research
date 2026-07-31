import { ChevronRight } from "lucide-react";

/**
 * The research zone's building blocks.
 *
 * Everything below the ideas is supporting material, so headings here are one
 * step down from the ideas heading and share a single type scale defined in
 * globals.css. No section styles its own titles.
 *
 * Heading-to-content gap is 1rem here (`mt-4`) and 1rem everywhere else a
 * heading block sits above its content (the two zone headings in
 * report-view.tsx). One value, so the distance from a heading to what it
 * introduces never signals a hierarchy that is not there.
 *
 * Each section carries a hairline above it. Five sections separated by
 * whitespace alone read as one unbroken column, and `.rule` was already in the
 * stylesheet doing this job elsewhere. `first:` clears it so the leading
 * section in any container never opens on a rule.
 */
export function Section({
  title,
  description,
  children,
  id,
  flush,
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
  id?: string;
  /** Drops the hairline. For a section whose container already draws one,
   *  such as the two claim columns, which sit side by side and would
   *  otherwise get a rule on one column and not the other. */
  flush?: boolean;
}) {
  return (
    <section
      id={id}
      className={
        flush ? "scroll-mt-8" : "rule scroll-mt-8 border-t pt-6 first:border-t-0 first:pt-0"
      }
    >
      <div className="flex flex-col gap-1">
        <h3 className="h-section text-foreground">{title}</h3>
        {description ? (
          <p className="text-xs leading-relaxed text-muted-foreground">{description}</p>
        ) : null}
      </div>
      <div className="mt-4">{children}</div>
    </section>
  );
}

/** The muted line under a section that qualifies what the numbers mean. */
export function Note({ children }: { children: React.ReactNode }) {
  return (
    <p className="mt-3 border-t pt-3 text-xs leading-relaxed text-muted-foreground">
      {children}
    </p>
  );
}

/** A collapsed-by-default block, used for source data and methodology. */
export function Drawer({
  summary,
  hint,
  children,
}: {
  summary: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <details className="surface-hush group px-5 py-4 transition-colors open:bg-card">
      <summary className="flex cursor-pointer list-none items-center gap-2.5 marker:hidden">
        <ChevronRight
          aria-hidden="true"
          strokeWidth={2.25}
          className="size-3.5 shrink-0 text-muted-foreground transition-transform duration-200 group-open:rotate-90"
        />
        <span className="h-section text-foreground">{summary}</span>
        {hint ? (
          <span className="num ml-auto truncate pl-3 text-xs text-muted-foreground">
            {hint}
          </span>
        ) : null}
      </summary>
      <div className="mt-4 border-t pt-4">{children}</div>
    </details>
  );
}
