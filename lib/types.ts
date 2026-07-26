/**
 * Report bundle v2.
 *
 * Hand-derived from the Python engine so the two cannot drift silently:
 * field names and nesting come from `pipeline.run_report`, the numeric shapes
 * from `stats.py`, the per-item shapes from `reddit_data.fetch_posts` and
 * `reddit_data.fetch_comments`, and the idea/claim shapes from
 * `analysis.sanitize_synthesis`.
 *
 * The bundle is the single unit of state on this side too: it is what the
 * stream delivers, what history stores, and what every section renders.
 */

export const BUNDLE_VERSION = 2;

export type PlatformKey = "linkedin" | "instagram" | "both";
export type IdeaPlatform = "linkedin" | "instagram";
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
  platform: PlatformKey;
  platform_label: string;
  /** Only the communities that actually contributed data. */
  subreddits: string[];
  days: number;
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
  platform: IdeaPlatform;
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

/* ── Request and stream contract (see Addendum 2, "API contract") ─────────── */

export interface ReportRequest {
  query: string;
  profile: string;
  platform: PlatformKey;
  days: number;
}

export type StreamLine =
  | { type: "progress"; message: string }
  | { type: "report"; bundle: Bundle }
  | { type: "invalid"; errors: string[] }
  | { type: "error"; message: string };

export type SampleResponse =
  | { available: true; bundle: Bundle }
  | { available: false };

/* ── Constants mirrored from the engine ───────────────────────────────────── */

export const MIN_QUERY_CHARS = 10;
export const MAX_QUERY_CHARS = 300;
export const MAX_PROFILE_CHARS = 500;
export const TIME_WINDOWS = [7, 14, 30] as const;

export const PLATFORM_LABELS: Record<PlatformKey, string> = {
  linkedin: "LinkedIn",
  instagram: "Instagram",
  both: "LinkedIn + Instagram",
};

export const IDEA_PLATFORM_LABELS: Record<IdeaPlatform, string> = {
  linkedin: "LinkedIn",
  instagram: "Instagram",
};

export const SENTIMENT_LABELS: Record<SentimentKey, string> = {
  pos: "Positive",
  neu: "Neutral",
  neg: "Negative",
};

export const SENTIMENT_ORDER: SentimentKey[] = ["pos", "neu", "neg"];

/** Narrow an unknown payload before anything renders it. */
export function isBundleV2(value: unknown): value is Bundle {
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
