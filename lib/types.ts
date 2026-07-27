/**
 * Report bundle v4.
 *
 * Hand-derived from the Python engine so the two cannot drift silently:
 * field names and nesting come from `pipeline.run_report`, the numeric shapes
 * from `stats.py`, the per-item shapes from `reddit_data.fetch_posts` and
 * `reddit_data.fetch_comments`, and the idea/claim shapes from
 * `analysis.sanitize_synthesis`.
 *
 * v4 removes the user-facing time window: every run reads the engine's fixed
 * fetch window (30 days) and a relevance-aware selector decides what gets
 * analysed, so inputs carry only {query, profile}. The window the report
 * actually covered lives in stats.date_range, computed from real timestamps.
 *
 * The bundle is the single unit of state on this side too: it is what the
 * stream delivers, what history stores, and what every section renders.
 */

export const BUNDLE_VERSION = 4;

export type SentimentKey = "pos" | "neu" | "neg";

/** Posts, comments and analysed items all share this shape. */
export interface RedditItem {
  kind: "post" | "comment";
  id: string;
  subreddit: string;
  title: string;
  body: string;
  score: number;
  /** Reply count. Present on posts only. */
  comments?: number;
  url: string;
  created_utc: number;
  /** YYYY-MM-DD */
  date: string;
}

/** Resolved server-side by `stats.citations_for`, never by the model. */
export interface Citation {
  index: number;
  text: string;
  url: string;
  date: string;
  score: number;
  type: string;
  subreddit: string;
}

export interface Community {
  name: string;
  reason: string;
  subscribers: number;
  /** False when the community was discovered but contributed no data. */
  used: boolean;
}

export interface Discovery {
  communities: Community[];
  /** Every candidate the model proposed, before validation. */
  considered: string[];
}

export interface Inputs {
  query: string;
  profile: string;
  /** Only the communities that actually contributed data. */
  subreddits: string[];
  /** The requested time range. Optional: bundles from before it returned as an
   * advanced setting do not carry it; the engine default is 30. */
  days?: number;
}

/** How the analysed sample was chosen from the fetched pool. */
export interface SelectionInfo {
  /**
   * "relevance+engagement" when the triage call rated candidates;
   * "deterministic-fallback" when selection ran on engagement and recency alone.
   */
  method: string;
  /** Candidates the model actually rated (0 on fallback). */
  triaged: number;
  /** How many candidates were rated 0 (unrelated), 1 (maybe), 2 (on-topic). */
  relevance_counts: Record<"0" | "1" | "2", number>;
}

export interface SampleInfo {
  posts_fetched: number;
  comments_fetched: number;
  posts_analysed: number;
  comments_analysed: number;
  items_analysed: number;
  posts_truncated: boolean;
  comments_truncated: boolean;
  low_data: boolean;
  capacity_limited: boolean;
  selection: SelectionInfo;
}

export interface Topic {
  id: number | string;
  label: string;
  count: number;
  /** Percent of analysed items, one decimal place. */
  share: number;
  finding: string;
  citations: Citation[];
}

export interface Claim {
  text: string;
  citations: Citation[];
}

export interface ContentIdea {
  title: string;
  /** The literal opening line, written to stop the scroll. May be empty. */
  hook: string;
  format: string;
  angle: string;
  rationale: string;
  addresses: string;
  citations: Citation[];
}

/** One entry per classified item: `i` is the 1-based item index. */
export interface ItemSentiment {
  i: number;
  s: SentimentKey;
}

export interface Analysis {
  executive_summary: string;
  topics: Topic[];
  pain_points: Claim[];
  questions: Claim[];
  content_ideas: ContentIdea[];
  item_sentiments: ItemSentiment[];
}

export interface SentimentStats {
  counts: Record<SentimentKey, number>;
  percentages: Record<SentimentKey, number>;
  classified: number;
  unclassified: number;
}

export interface EngagementStats {
  score: number;
  comments: number;
}

export interface DateRange {
  oldest: string;
  newest: string;
  newest_ts: number;
  /** Absent when no post in the sample carried a date. */
  newest_iso?: string;
  label: string;
  short_label: string;
}

/** [date, count] pairs, oldest first, zero-filled across the window. */
export type TrendPoint = [string, number];

export interface Stats {
  sentiment: SentimentStats;
  engagement: EngagementStats;
  date_range: DateRange;
  lag_hours: number;
  trend: TrendPoint[];
}

export interface Bundle {
  bundle_version: number;
  /** "YYYY-MM-DD HH:MM UTC" */
  generated_at: string;
  generated_ts: number;
  elapsed_seconds: number;
  is_sample: boolean;
  model: string;
  model_label: string;
  source: string;
  inputs: Inputs;
  discovery: Discovery;
  notices: string[];
  posts: RedditItem[];
  comments: RedditItem[];
  items: RedditItem[];
  sample: SampleInfo;
  analysis: Analysis;
  stats: Stats;
}

/* ── Request and stream contract (Addendum 2, amended by Addendum 3) ──────── */

export interface ReportRequest {
  query: string;
  profile: string;
  /** Advanced settings; every field optional and defaulted server-side. */
  days?: number;
  /** Comma-separated community names; skips discovery when set. */
  subreddits?: string;
  relevance_filter?: boolean;
}

export type StreamLine =
  | { type: "progress"; message: string }
  | { type: "report"; bundle: Bundle }
  | { type: "invalid"; errors: string[] }
  | { type: "error"; message: string };

export type SampleResponse =
  | { available: true; bundle: Bundle }
  | { available: false };

/* ── Draft contract (Addendum 3 item 2) ───────────────────────────────────── */

/** The idea fields the drafting call needs. Citations go over as excerpts. */
export interface DraftIdea {
  title: string;
  hook: string;
  format: string;
  angle: string;
  addresses: string;
}

export interface DraftRequest {
  idea: DraftIdea;
  /** Up to MAX_DRAFT_EVIDENCE source excerpts. */
  evidence: string[];
  query: string;
  profile: string;
}

export type DraftResponse = { post: string } | { error: string };

export const MAX_DRAFT_EVIDENCE = 6;

/* ── Constants mirrored from the engine ───────────────────────────────────── */

export const MIN_QUERY_CHARS = 10;
export const MAX_QUERY_CHARS = 300;
export const MAX_PROFILE_CHARS = 500;
export const TIME_WINDOWS = [7, 14, 30] as const;
export const DEFAULT_DAYS = 30;
export const MAX_MANUAL_SUBREDDITS = 4;

export const SENTIMENT_LABELS: Record<SentimentKey, string> = {
  pos: "Positive",
  neu: "Neutral",
  neg: "Negative",
};

export const SENTIMENT_ORDER: SentimentKey[] = ["pos", "neu", "neg"];

/** Narrow an unknown payload before anything renders it. */
export function isBundleV4(value: unknown): value is Bundle {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<Bundle>;
  return (
    candidate.bundle_version === BUNDLE_VERSION &&
    typeof candidate.inputs === "object" &&
    candidate.inputs !== null &&
    typeof candidate.analysis === "object" &&
    candidate.analysis !== null &&
    typeof candidate.stats === "object" &&
    candidate.stats !== null &&
    typeof candidate.sample === "object" &&
    candidate.sample !== null
  );
}
