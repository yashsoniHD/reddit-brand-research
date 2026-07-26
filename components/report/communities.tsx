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
    <div className="flex flex-wrap items-center gap-2">
      <span className="h-quiet">Communities read</span>
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
          className={`rounded-full border px-2.5 py-0.5 text-xs font-medium transition-colors hover:border-[var(--brand)]/40 hover:text-[var(--brand-ink)] ${
            community.used
              ? "text-foreground"
              : "text-muted-foreground line-through decoration-1"
          }`}
        >
          r/{community.name}
          <span className="ml-1.5 font-normal tabular-nums text-muted-foreground">
            {compact(community.subscribers)}
          </span>
        </a>
      ))}
    </div>
  );
}
