import Link from "next/link";

const NAME = "Reddit Content Ideas";

/**
 * The page chrome: sticky header, main column, footer.
 *
 * It was copy-pasted verbatim into three files, which is how the live app
 * ended up on `py-12 sm:py-20`, the saved-report page on `py-12 sm:py-16` and
 * the error page on `py-24` with no header or footer at all. The three were
 * never decided, they were transcribed, so the extra air on the landing page
 * now has to be asked for by name (`pad="roomy"`) rather than being whatever
 * the last person typed.
 *
 * Vertical rhythm INSIDE the column stays with the page: the ideas grid and a
 * loading skeleton do not want the same gap, and that is a real difference
 * rather than another transcription error.
 */
export function AppShell({
  action,
  linkHome = false,
  pad = "default",
  children,
}: {
  /** The header's right-hand slot: a history button on the live app, a
   *  "Run your own" link on a saved report, nothing on an error page. */
  action?: React.ReactNode;
  /** The wordmark is inert on the live app because it is already home, and a
   *  link back on any page that is not. */
  linkHome?: boolean;
  /** "roomy" is the landing page's extra breathing room, kept as a choice. */
  pad?: "default" | "roomy";
  children: React.ReactNode;
}) {
  const wordmark = (
    <>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src="/logo.svg" alt="" width={24} height={24} className="size-6" />
      {NAME}
    </>
  );
  /* size-6 rather than the size-[22px] the copies carried: 22px is off every
     spacing step in the system, and nothing about the mark needed it. */
  const wordmarkClass =
    "flex items-center gap-2.5 text-sm font-semibold tracking-[-0.012em] text-foreground";

  return (
    <>
      <header className="sticky top-0 z-30 border-b bg-background/80 backdrop-blur-md">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-5 py-3 sm:px-8">
          {linkHome ? (
            <Link href="/" className={wordmarkClass}>
              {wordmark}
            </Link>
          ) : (
            <span className={wordmarkClass}>{wordmark}</span>
          )}
          {action}
        </div>
      </header>

      <main
        className={`mx-auto w-full max-w-6xl px-5 py-12 sm:px-8 ${
          pad === "roomy" ? "sm:py-20" : "sm:py-16"
        }`}
      >
        {children}
      </main>

      {/* Two groups, not three stacked lines: what the product is and where its
          data comes from belong together, and the byline is a different kind of
          fact, so it takes the opposite end of the rule rather than a third row
          of the same grey. It falls back to stacking under sm, where a row would
          leave the byline stranded against the gutter. */}
      <footer className="mt-auto border-t">
        <div className="mx-auto flex max-w-6xl flex-col gap-3 px-5 py-8 text-xs leading-relaxed text-muted-foreground sm:flex-row sm:items-end sm:justify-between sm:gap-6 sm:px-8">
          <div className="flex flex-col gap-1">
            <span className="font-medium text-foreground-soft">{NAME}</span>
            <span>
              Public Reddit data via the Arctic Shift archive. Not affiliated with Reddit.
            </span>
          </div>
          <span className="shrink-0">
            Built by <span className="font-medium text-foreground-soft">Yash Soni</span>
          </span>
        </div>
      </footer>
    </>
  );
}
