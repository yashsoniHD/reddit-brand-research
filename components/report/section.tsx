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
      <h3 className="h-section text-foreground">{title}</h3>
      {description ? (
        <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{description}</p>
      ) : null}
      <div className="mt-3">{children}</div>
    </section>
  );
}

/** The muted line under a section that qualifies what the numbers mean. */
export function Note({ children }: { children: React.ReactNode }) {
  return <p className="mt-3 text-xs leading-relaxed text-muted-foreground">{children}</p>;
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
    <details className="surface-hush group px-5 py-4">
      <summary className="flex cursor-pointer list-none items-center gap-2 marker:hidden">
        <span
          aria-hidden="true"
          className="text-xs text-muted-foreground transition-transform duration-200 group-open:rotate-90"
        >
          &rsaquo;
        </span>
        <span className="h-section text-foreground">{summary}</span>
        {hint ? (
          <span className="ml-auto text-xs text-muted-foreground">{hint}</span>
        ) : null}
      </summary>
      <div className="mt-4">{children}</div>
    </details>
  );
}
