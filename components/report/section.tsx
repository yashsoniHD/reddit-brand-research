import { ChevronRight } from "lucide-react";

/**
 * The research zone's building blocks.
 *
 * Everything below the ideas is supporting material, so headings here are one
 * step down from the ideas heading and share a single type scale defined in
 * globals.css. No section styles its own titles.
 */
export function Section({
  title,
  description,
  children,
  id,
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
  id?: string;
}) {
  return (
    <section id={id} className="scroll-mt-8">
      <div className="flex flex-col gap-1">
        <h3 className="h-section text-foreground">{title}</h3>
        {description ? (
          <p className="text-xs leading-relaxed text-muted-foreground">{description}</p>
        ) : null}
      </div>
      <div className="mt-3.5">{children}</div>
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
