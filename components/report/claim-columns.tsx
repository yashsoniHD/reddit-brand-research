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
    <Section title={title}>
      {claims.length === 0 ? (
        <p className="text-xs text-muted-foreground">None identified in this sample.</p>
      ) : (
        <ul className="flex list-none flex-col gap-0 p-0">
          {claims.map((claim, index) => (
            <li
              key={`${index}-${claim.text}`}
              className="border-b border-border/60 py-2.5 text-sm leading-relaxed text-foreground last:border-b-0"
            >
              {claim.text}{" "}
              <EvidencePills citations={claim.citations} label="Source" />
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
    <div className="grid gap-8 md:grid-cols-2">
      <ClaimList title="Pain points" claims={painPoints} />
      <ClaimList title="Questions being asked" claims={questions} />
    </div>
  );
}
