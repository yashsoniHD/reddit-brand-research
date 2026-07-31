import { EvidencePills } from "@/components/report/evidence-pills";
import { Section } from "@/components/report/section";
import type { Claim } from "@/lib/types";

/**
 * Pain points and questions as tight lists.
 *
 * Previously each claim was a bold line followed by two or three underlined
 * link titles with dates and scores, so the section read as a link dump. Now the
 * claim is the only text and its evidence trails it as inline footnote pills.
 */
function ClaimList({ title, claims }: { title: string; claims: Claim[] }) {
  return (
    <Section title={title} flush>
      {claims.length === 0 ? (
        <p className="text-xs text-muted-foreground">None identified in this sample.</p>
      ) : (
        <ul className="flex list-none flex-col gap-0 p-0">
          {claims.map((claim, index) => (
            <li
              key={`${index}-${claim.text}`}
              // A brand tick on the left edge marks each claim as a cited
              // finding rather than as an opinion the report is offering.
              className="t-body relative border-b py-3 pl-4 text-foreground last:border-b-0 before:absolute before:top-[1.3em] before:left-0 before:size-1.5 before:rounded-full before:bg-[var(--brand)] before:content-['']"
            >
              {claim.text} <EvidencePills citations={claim.citations} label="Source" />
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

export function ClaimColumns({
  painPoints,
  questions,
}: {
  painPoints: Claim[];
  questions: Claim[];
}) {
  return (
    // The pair carries the research zone's hairline, not the two columns: side
    // by side, a per-section rule would draw above one column and not the other.
    <div className="rule grid gap-8 border-t pt-6 md:grid-cols-2 md:gap-10">
      <ClaimList title="Pain points" claims={painPoints} />
      <ClaimList title="Questions being asked" claims={questions} />
    </div>
  );
}
