import { ExternalLink } from "@/components/external-link";
import { Note, Section } from "@/components/report/section";
import { compact } from "@/lib/format";
import type { Bundle } from "@/lib/types";

/**
 * A run that picked its own subreddits has to show its work, or the citations
 * further down read as coming from nowhere in particular.
 */
export function Communities({ bundle }: { bundle: Bundle }) {
  const communities = bundle.discovery?.communities ?? [];
  const considered = bundle.discovery?.considered ?? [];

  return (
    <Section
      title="Communities analysed"
      description="Proposed by the model, then checked against the Reddit archive before anything was read."
    >
      {communities.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No community details were recorded for this run.
        </p>
      ) : (
        <ul className="grid list-none gap-3 p-0 sm:grid-cols-2 lg:grid-cols-3">
          {communities.map((community) => (
            <li
              key={community.name}
              className={`rounded-xl border bg-card p-4 ${
                community.used ? "" : "opacity-60"
              }`}
            >
              <div className="flex items-baseline justify-between gap-3">
                <ExternalLink
                  href={`https://reddit.com/r/${community.name}`}
                  className="font-medium text-[var(--brand-ink)] underline-offset-2 hover:underline"
                >
                  r/{community.name}
                </ExternalLink>
                <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
                  {compact(community.subscribers)} members
                </span>
              </div>
              {community.reason ? (
                <p className="mt-2 text-sm leading-snug text-muted-foreground">
                  {community.reason}
                </p>
              ) : null}
              {!community.used ? (
                <p className="mt-2 text-xs font-medium text-muted-foreground">
                  No recent posts, so it was left out of this report.
                </p>
              ) : null}
            </li>
          ))}
        </ul>
      )}

      {considered.length > 0 ? (
        <Note>
          Candidates considered: {considered.map((name) => `r/${name}`).join(", ")}.
        </Note>
      ) : null}
    </Section>
  );
}
