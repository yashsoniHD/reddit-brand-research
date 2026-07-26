import { InlineCitations } from "@/components/report/citation-list";
import { Section } from "@/components/report/section";
import { IDEA_PLATFORM_LABELS, type Bundle, type ContentIdea } from "@/lib/types";

function Badge({ children, tone }: { children: React.ReactNode; tone: "accent" | "plain" }) {
  const styles =
    tone === "accent"
      ? "bg-accent text-accent-foreground"
      : "bg-secondary text-secondary-foreground";
  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${styles}`}
    >
      {children}
    </span>
  );
}

function IdeaCard({ idea }: { idea: ContentIdea }) {
  const platformLabel =
    IDEA_PLATFORM_LABELS[idea.platform] ?? String(idea.platform ?? "");

  return (
    <article className="flex flex-col gap-3 rounded-xl border bg-card p-5">
      {idea.hook ? (
        // The literal opening line they would publish, so it leads the card.
        <p className="border-l-2 pl-4 text-base leading-snug font-medium text-foreground [border-color:var(--brand)]">
          &ldquo;{idea.hook}&rdquo;
        </p>
      ) : null}

      <h3 className="text-sm leading-snug font-semibold text-foreground">{idea.title}</h3>

      <div className="flex flex-wrap gap-2">
        {platformLabel ? <Badge tone="accent">{platformLabel}</Badge> : null}
        {idea.format ? <Badge tone="plain">{idea.format}</Badge> : null}
      </div>

      {idea.angle ? (
        <p className="text-sm leading-relaxed text-foreground">{idea.angle}</p>
      ) : null}

      {idea.addresses ? (
        <p className="text-xs leading-relaxed text-muted-foreground">
          <span className="font-medium text-foreground">Addresses: </span>
          {idea.addresses}
        </p>
      ) : null}

      {idea.rationale ? (
        <p className="text-xs leading-relaxed text-muted-foreground">{idea.rationale}</p>
      ) : null}

      <InlineCitations citations={idea.citations} />
    </article>
  );
}

export function ContentIdeas({ bundle }: { bundle: Bundle }) {
  const ideas = bundle.analysis.content_ideas ?? [];

  return (
    <Section
      title={`Content ideas for ${bundle.inputs.platform_label}`}
      description="Each one opens with a hook you could publish as written, and cites the posts it came from."
    >
      {ideas.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No content ideas were produced for this run.
        </p>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          {ideas.map((idea, index) => (
            <IdeaCard key={`${index}-${idea.title}`} idea={idea} />
          ))}
        </div>
      )}
    </Section>
  );
}
