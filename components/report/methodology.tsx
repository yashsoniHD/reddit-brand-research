import { Drawer } from "@/components/report/section";
import { compact, joinQuoted, phraseDropReason, thousands } from "@/lib/format";
import {
  bundleContentTypes,
  type Bundle,
  type ContentType,
  type FilterInfo,
  type RequirementInfo,
} from "@/lib/types";

const CONTENT_TYPE_LABELS: Record<ContentType, string> = {
  text: "text post",
  carousel: "carousel",
  video: "video script",
  poll: "poll",
  article: "long-form article",
};

/**
 * Explains the quoted-phrase filter for the mode the run actually landed on.
 * Every mode states plainly that matching is case-insensitive and whole-word,
 * because that is the one rule a reader needs to judge whether a thread they
 * expected to see should have matched.
 */
function filterCopy(filter: FilterInfo): string {
  const phraseList = joinQuoted(filter.phrases);
  const counts = `${thousands(filter.posts_scanned)} posts and ${thousands(filter.comments_scanned)} comments were scanned; ${thousands(filter.posts_matched)} posts and ${thousands(filter.comments_matched)} comments matched.`;

  if (filter.mode === "exact") {
    return `Only threads mentioning ${phraseList} were read. ${counts} Matching is case-insensitive and whole-word.`;
  }
  if (filter.mode === "loose") {
    return `Too few threads mentioned ${phraseList} exactly to build a report on, so this run read what the archive matched on those words instead and treated the phrase as a preference rather than a hard filter. ${counts} Matching is case-insensitive and whole-word.`;
  }
  return `Too few recent threads mentioned ${phraseList}, so the filter was dropped and this report covers recent activity instead. Matching would have been case-insensitive and whole-word.`;
}

/** Names each refused phrase and, per phrase, why it was not used. */
function droppedFilterCopy(dropped: string[]): string {
  if (dropped.length === 0) return "";
  return dropped
    .map((phrase) => ` "${phrase}" was not used: ${phraseDropReason(phrase)}.`)
    .join("");
}

/**
 * One requirement's text plus its three triage counts, phrased with `kind`:
 * "require" reads as something the brief asked for, "exclude" as something it
 * ruled out. `text` is already a positive sentence either way, so it is never
 * re-negated here.
 *
 * A requirement flagged `unreliable` gets different copy. Printing "107
 * contradicted it" for a requirement the model marked as contradicted by nearly
 * every thread would be a false claim about real threads, so the line says what
 * actually happened instead.
 */
function requirementCopy(requirement: RequirementInfo): string {
  const framing = requirement.kind === "require" ? "You asked for this" : "You ruled this out";
  if (requirement.unreliable) {
    const rated = requirement.met + requirement.violated + requirement.unknown;
    return `${framing}: ${requirement.text} The model marked ${thousands(requirement.violated)} of the ${thousands(rated)} threads it rated as contradicting this, which almost certainly means it could not judge this from the text rather than that the threads really break it, so it was not used for ranking.`;
  }
  return `${framing}: ${requirement.text} ${thousands(requirement.met)} threads showed it, ${thousands(requirement.violated)} contradicted it, and ${thousands(requirement.unknown)} did not say.`;
}

/**
 * How the final analysed sample split on the requirements, not the whole
 * triaged pool. Called out separately because it is the number that tells a
 * reader how much of what they are about to read is actually confirmed.
 */
function qualifierAnalysedCopy(counts: {
  confirmed: number;
  unconfirmed: number;
  violated: number;
}): string {
  const total = counts.confirmed + counts.unconfirmed + counts.violated;
  const backfillNote =
    counts.violated > 0
      ? `, and ${thousands(counts.violated)} were kept despite contradicting a requirement because nothing better was available`
      : "";
  return ` Of the ${thousands(total)} items actually analysed, ${thousands(counts.confirmed)} showed clear evidence for a requirement and ${thousands(counts.unconfirmed)} never said either way${backfillNote}.`;
}

/**
 * How the posts in this report were found. The distinction matters more than any
 * other line here: "these are the threads that matched what you asked about" and
 * "this is what those communities have been talking about" are different claims,
 * and only one of them is what a given run actually did.
 *
 * A bundle from before retrieval was recorded says nothing rather than guessing.
 */
function retrievalCopy(inputs: { retrieval?: string; terms?: string[] }): string {
  const terms = inputs.terms?.length ? ` Searched for: ${joinQuoted(inputs.terms)}.` : "";
  if (inputs.retrieval === "search") {
    return `Each community was searched for the words your brief is about, and only the threads that matched were read.${terms} A search is not limited to the time range: it takes the most recent matches, however far back those reach.`;
  }
  if (inputs.retrieval === "search+scrape") {
    return "Searching those communities for specific words found too little to work with, so they were read in full instead. This report covers their recent activity rather than only the threads that matched a search.";
  }
  return "The communities you named were read in full for the time range below, and the relevance rating decided what was worth analysing.";
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid gap-1 py-3 sm:grid-cols-[9rem_1fr] sm:gap-6">
      <dt className="h-quiet sm:pt-0.5">{label}</dt>
      <dd className="m-0 text-[0.8125rem] leading-relaxed text-foreground-soft">
        {children}
      </dd>
    </div>
  );
}

export function Methodology({ bundle }: { bundle: Bundle }) {
  const sample = bundle.sample;
  const inputs = bundle.inputs ?? { query: "", profile: "", subreddits: [] };
  const range = bundle.stats?.date_range;
  const engagement = bundle.stats?.engagement;
  const analysed = engagement?.analysed;
  const lagHours = bundle.stats?.data_lag_hours ?? bundle.stats?.lag_hours;
  const contentTypes = bundleContentTypes(bundle.inputs).map(
    (type) => CONTENT_TYPE_LABELS[type],
  );

  return (
    <Drawer summary="Methodology and limitations" hint={bundle.model_label}>
      <dl className="divide-y divide-border">
        <Row label="Communities">
          {(inputs.subreddits ?? []).map((name) => `r/${name}`).join(", ") || "None"}
        </Row>
        <Row label="Discovery">
          A model proposes candidate subreddits for your search. Each one is checked
          against the Reddit archive to confirm it exists and is public before it is
          read, so the model proposes and the archive verifies.
        </Row>
        {inputs.retrieval ? <Row label="Retrieval">{retrievalCopy(inputs)}</Row> : null}
        <Row label="Window">
          {inputs.retrieval === "search"
            ? "Not limited: a search takes the most recent matching threads however old they are."
            : `Requested: last ${inputs.days ?? 7} days.`}{" "}
          Actual coverage: {range?.label || "n/a"}.
        </Row>
        {contentTypes.length > 0 ? (
          <Row label={contentTypes.length === 1 ? "Content type" : "Content types"}>
            {contentTypes.length === 1
              ? `Ideas were written for one format: ${contentTypes[0]}.`
              : `Ideas were written for these formats only: ${contentTypes.join(", ")}.`}
          </Row>
        ) : null}
        <Row label="Posts">
          {thousands(sample?.posts_fetched)} fetched, {thousands(sample?.posts_analysed)}{" "}
          analysed.
        </Row>
        <Row label="Comments">
          {thousands(sample?.comments_fetched)} fetched,{" "}
          {thousands(sample?.comments_analysed)} analysed.
          {inputs.retrieval === "search"
            ? " Every reply was fetched from a thread in this report, rather than from the community at large."
            : ""}
        </Row>
        <Row label="Engagement">
          {compact(engagement?.score)} upvotes and {compact(engagement?.comments)}{" "}
          comments across the posts fetched.
          {analysed
            ? ` Within the analysed sample: ${compact(analysed.upvotes)} upvotes and ${compact(analysed.comments)} comments.`
            : ""}
        </Row>
        {sample?.filter ? (
          <Row label={sample.filter.phrases.length === 1 ? "Keyword filter" : "Keyword filters"}>
            {filterCopy(sample.filter)}
            {droppedFilterCopy(sample.filter.dropped)}
          </Row>
        ) : null}
        <Row label="Selection">
          {sample?.selection?.method === "relevance+engagement"
            ? `Every candidate was rated for relevance to your search by the model (${thousands(sample.selection.triaged)} items rated), then ranked in code by relevance, community engagement and recency.`
            : sample?.selection?.method === "relevance+qualifiers"
              ? `Every candidate was rated for relevance to your search by the model (${thousands(sample.selection.triaged)} items rated) and checked against the requirements below, then ranked in code by relevance, those requirements, community engagement and recency.`
              : sample?.selection?.method === "relevance-off"
                ? sample?.selection?.requirements?.length
                  ? "The relevance filter was turned off for this run, so items were ranked in code by community engagement and recency only. Your brief's requirements about who posted, below, were not applied."
                  : "The relevance filter was turned off for this run, so items were ranked in code by community engagement and recency."
                : "Items were ranked in code by community engagement and recency; the relevance rating was unavailable for this run."}{" "}
          No single community takes more than half the sample while others have items
          to offer.
        </Row>
        {sample?.selection?.requirements?.length ? (
          <Row
            label={sample.selection.requirements.length === 1 ? "Requirement" : "Requirements"}
          >
            {sample.selection.method === "relevance+qualifiers" ? (
              <>
                <p className="m-0">
                  Your brief asked for more than a topic, so every rated candidate was also
                  checked against{" "}
                  {sample.selection.requirements.length === 1 ? "it" : "each of these"}.
                </p>
                <ul className="m-0 mt-2 list-disc pl-4">
                  {sample.selection.requirements.map((requirement, index) => (
                    <li key={index}>{requirementCopy(requirement)}</li>
                  ))}
                </ul>
                <p className="m-0 mt-2">
                  Threads that showed a requirement were analysed first. Threads that
                  contradicted one were pushed behind everything else rather than removed: the
                  call comes from a model reading a short preview, and a wrong call would throw
                  away a good thread.
                  {sample.selection.qualifier_analysed
                    ? qualifierAnalysedCopy(sample.selection.qualifier_analysed)
                    : ""}
                </p>
                <p className="m-0 mt-2">
                  Seniority and funding stage are whatever the author wrote about themselves,
                  not anything this tool verified: this is self-reported, and the system is
                  trusting a stranger&apos;s claim about their own company.
                </p>
              </>
            ) : (
              <p className="m-0">
                Your brief also described who should have posted:{" "}
                {sample.selection.requirements.map((requirement) => requirement.text).join("; ")}
                . These requirements were not applied because the relevance filter was off for
                this run, so the sample was ranked on community engagement and recency only.
              </p>
            )}
          </Row>
        ) : null}
        <Row label="Truncation">
          When more was fetched than the sample can hold, the top-ranked posts and
          comments are the ones analysed.
          {sample?.capacity_limited
            ? " This run was capped by the current analysis capacity tier."
            : ""}
        </Row>
        <Row label="Exclusions">
          Posts and comments deleted or removed by their authors or moderators are
          skipped, as are duplicates.
        </Row>
        <Row label="Freshness">
          Newest item in this sample: {range?.newest_iso || "n/a"}, roughly{" "}
          {lagHours ?? 0} hours behind live Reddit when this report was generated.
        </Row>
        <Row label="Numbers">
          Topic volumes and sentiment percentages are computed from per item
          classifications, not estimated by the model.
        </Row>
        <Row label="Citations">Every evidence pill resolves to the archived source.</Row>
        <Row label="Model">{bundle.model_label}</Row>
        <Row label="Generated">
          {bundle.generated_at} in {bundle.elapsed_seconds} seconds.
        </Row>
      </dl>
    </Drawer>
  );
}
