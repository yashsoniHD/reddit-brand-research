import { CitationList } from "@/components/report/citation-list";
import { Section } from "@/components/report/section";
import type { Claim } from "@/lib/types";

function ClaimColumn({ title, claims }: { title: string; claims: Claim[] }) {
  return (
    <Section title={title}>
      {claims.length === 0 ? (
        <p className="text-sm text-muted-foreground">None identified in this sample.</p>
      ) : (
        <ul className="flex list-none flex-col gap-4 p-0">
          {claims.map((claim, index) => (
            <li key={`${index}-${claim.text}`}>
              <p className="text-sm leading-snug font-medium text-foreground">
                {claim.text}
              </p>
              <CitationList citations={claim.citations} />
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
    <div className="grid gap-10 md:grid-cols-2 md:gap-8">
      <ClaimColumn title="Pain points" claims={painPoints} />
      <ClaimColumn title="Questions being asked" claims={questions} />
    </div>
  );
}
