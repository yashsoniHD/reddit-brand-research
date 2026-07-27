/**
 * The positioning, said once, in the largest type on the page.
 *
 * Every output format this engine writes is a LinkedIn format, so the headline
 * says LinkedIn rather than leaving the reader to infer it. One line follows it
 * and no more: the form directly below explains itself, and the wedge this
 * product competes on (real threads, cited) fits inside that single line.
 */
export function Hero() {
  return (
    <div className="mx-auto flex w-full max-w-[46rem] flex-col items-center gap-5 text-center">
      <h1 className="t-display text-balance text-foreground">
        Turn what people actually say on Reddit into{" "}
        {/* Kept on one line: the positioning is the phrase, and a break through
            the middle of it is the one wrap this headline cannot afford. */}
        <span className="whitespace-nowrap text-[var(--brand-ink)]">LinkedIn posts</span>{" "}
        you can publish.
      </h1>

      <p className="t-lead max-w-xl text-balance text-foreground-soft">
        We read the communities where your audience posts, and cite every idea back to
        the thread it came from.
      </p>
    </div>
  );
}
