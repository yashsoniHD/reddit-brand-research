import { compact } from "@/lib/format";
import type { Bundle } from "@/lib/types";

/**
 * What discovery actually read, as a compact chip row.
 *
 * A run that picked its own subreddits has to show its work or the evidence
 * below reads as coming from nowhere. It does not need a card each: the member
 * count and the reason fit on one line and in the tooltip.
 */
export function Communities({ bundle }: { bundle: Bundle }) {
  const communities = bundle.discovery?.communities ?? [];
  if (communities.length === 0) return null;

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
      <span className="h-quiet">Communities read</span>
      <div className="flex flex-wrap items-center gap-1.5">
        {communities.map((community) => (
          <a
            key={community.name}
            href={`https://reddit.com/r/${community.name}`}
            target="_blank"
            rel="noopener noreferrer"
            title={
              community.used
                ? `${community.reason} · ${compact(community.subscribers)} members`
                : `${community.reason} · no recent posts, so it was left out`
            }
            className={`chip transition-colors hover:border-[var(--brand-line)] hover:bg-[var(--brand-soft)] hover:text-[var(--brand-ink)] ${
              community.used
                ? "text-foreground"
                : "text-muted-foreground line-through decoration-1"
            }`}
          >
            r/{community.name}
            <span className="num font-normal text-muted-foreground">
              {compact(community.subscribers)}
            </span>
          </a>
        ))}
      </div>
    </div>
  );
}
