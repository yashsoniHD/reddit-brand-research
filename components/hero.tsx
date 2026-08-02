import { MessagesSquare, PenLine, Send } from "lucide-react";

/**
 * The positioning, said once, in the largest type on the page.
 *
 * Flat and factual rather than persuasive: the headline names the output and
 * the input, the line under it states the one property that makes the output
 * checkable, and the strip under that names three more facts rather than
 * arguing for any of them. The form below explains the rest by existing.
 *
 * The strip is also what gives the block a base. Before it the hero was a
 * headline and one grey sentence floating in the page's roomy top padding,
 * with nothing between the header rule and the form to carry weight, so the
 * eye went from the wordmark to the textarea and read the two lines in
 * between as a caption on the way past.
 */

/**
 * Three facts the headline and the standfirst do not already carry.
 *
 * Every one is checkable against the product rather than a claim about it: a
 * run reads thousands of posts and comments (the sample bundle carries 1,679
 * and 908), the channel setting shapes what the ideas are written for, and a
 * finished idea opens straight into a draft. Anything that only restates the
 * sentence above belongs in that sentence, not here.
 *
 * Kept short on purpose. At `t-small` these three sit on one line inside the
 * headline's own measure; a fourth fact, or a wordier one, wraps to two rows
 * and the strip stops reading as a base and starts reading as a paragraph.
 */
const FACTS = [
  { icon: MessagesSquare, text: "Thousands of posts and comments" },
  { icon: Send, text: "Shaped for where you publish" },
  { icon: PenLine, text: "Click an idea, get a draft" },
];

export function Hero() {
  return (
    <div className="mx-auto flex w-full max-w-[46rem] flex-col items-center text-center">
      <h1 className="t-display text-balance text-foreground">
        {/* Two spans, two jobs. The first keeps the product's own name off a
            wrap point. The second is the fix for the break this headline was
            actually taking: `text-balance` optimises for even line lengths,
            not for meaning, so at desktop width it split "real Reddit
            conversations" after "real" and left the adjective stranded at the
            end of line one. Going block from sm up forces the break at the
            preposition instead, which is the one clause boundary the sentence
            has. It stays inline below sm, where the line cannot fit whole and
            a natural wrap is the better answer. */}
        <span className="whitespace-nowrap text-[var(--brand-ink)]">Content ideas</span>{" "}
        from <span className="sm:block">real Reddit conversations.</span>
      </h1>

      <p className="t-lead mt-4 max-w-xl text-balance text-foreground-soft">
        Every idea links to the thread it came from.
      </p>

      {/* Further from the standfirst than the standfirst is from the headline:
          these are a different kind of statement, and an even gap would read as
          a third line of the same sentence.

          A column below sm rather than a centred wrap. Three items cannot share
          a phone's line, and centring each row independently would stagger the
          three marks down the page at three different indents. Left-aligning
          the list and letting the parent centre it as a block puts them back in
          a column, which is the only thing making three short rows read as one
          group. */}
      <ul className="mt-7 flex flex-col items-start gap-2.5 sm:flex-row sm:flex-wrap sm:items-center sm:justify-center sm:gap-x-6">
        {FACTS.map(({ icon: Icon, text }) => (
          <li key={text} className="t-small flex items-center gap-2 text-muted-foreground">
            {/* The mark is the only colour down here, and it is decoration in
                the accessibility sense: every one of these facts is fully
                carried by the words beside it. */}
            <Icon
              aria-hidden="true"
              strokeWidth={2}
              className="size-3.5 shrink-0 text-[var(--brand)]"
            />
            {text}
          </li>
        ))}
      </ul>
    </div>
  );
}
