import { ExternalLink } from "@/components/external-link";
import { truncate } from "@/lib/format";
import type { Citation } from "@/lib/types";

/** Evidence under a claim: the real title, so the reader can judge it unopened. */
export function CitationList({ citations }: { citations: Citation[] }) {
  if (!citations || citations.length === 0) return null;
  return (
    <ul className="mt-2 flex list-none flex-col gap-1 p-0 text-sm text-muted-foreground">
      {citations.map((citation) => (
        <li key={`${citation.index}-${citation.url}`} className="leading-snug">
          <ExternalLink href={citation.url}>{truncate(citation.text, 90)}</ExternalLink>
          <span className="whitespace-nowrap">
            {" "}
            · {citation.date} · {citation.score} upvotes
          </span>
        </li>
      ))}
    </ul>
  );
}

/** Compact inline form, used inside the dense idea cards. */
export function InlineCitations({ citations }: { citations: Citation[] }) {
  if (!citations || citations.length === 0) return null;
  return (
    <p className="text-xs leading-relaxed text-muted-foreground">
      <span className="font-medium text-foreground">Evidence: </span>
      {citations.map((citation, index) => (
        <span key={`${citation.index}-${citation.url}`}>
          {index > 0 ? " · " : ""}
          <ExternalLink href={citation.url}>{truncate(citation.text, 50)}</ExternalLink>
        </span>
      ))}
    </p>
  );
}
