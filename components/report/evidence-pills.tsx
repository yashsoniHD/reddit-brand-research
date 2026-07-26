import { compact } from "@/lib/format";
import type { Citation } from "@/lib/types";

/**
 * Citations as numbered footnote pills, never as text hyperlinks.
 *
 * The old treatment rendered every citation as an underlined post title plus
 * a date and an upvote count, which meant a page of research read as a wall of
 * links and the actual findings disappeared underneath them. A pill carries the
 * same affordance at a tenth of the visual weight; the title, date and score
 * live in the tooltip and in the accessible name.
 */

function describe(citation: Citation): string {
  const parts = [citation.text];
  if (citation.subreddit) parts.push(`r/${citation.subreddit}`);
  if (citation.date) parts.push(citation.date);
  parts.push(`${compact(citation.score)} upvotes`);
  return parts.join(" · ");
}

export function EvidencePills({
  citations,
  label = "Evidence",
  className,
}: {
  citations: Citation[];
  /** Screen-reader prefix, so a bare "1" is never the whole accessible name. */
  label?: string;
  className?: string;
}) {
  if (!citations || citations.length === 0) return null;

  return (
    <span className={`inline-flex flex-wrap items-center gap-1 ${className ?? ""}`}>
      <span className="sr-only">{label}: </span>
      {citations.map((citation, index) => {
        const description = describe(citation);
        return (
          <a
            key={`${citation.index}-${citation.url}`}
            href={citation.url}
            target="_blank"
            rel="noopener noreferrer"
            className="pill"
            title={description}
            aria-label={`${label} ${index + 1}: ${description}`}
          >
            {index + 1}
          </a>
        );
      })}
    </span>
  );
}
