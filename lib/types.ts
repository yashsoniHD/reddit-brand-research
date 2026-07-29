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
 *
 * Later additive fields, all optional: quoted phrases inside the query become
 * hard keyword filters (`Inputs.phrases`, `SampleInfo.filter`, and `author`,
 * `author_flair_text`, `link_flair_text`, `upvote_ratio`, `phrase_match` on
 * `RedditItem`). A brief can also decompose into a topic plus up to three
 * requirements about who posted a thread (`SelectionInfo.topic`, `.pool`,
 * `.requirements`, `.qualifier_counts`, `.qualifier_analysed`, and
 * `qualifier_tier` on `RedditItem`). None of this bumps BUNDLE_VERSION; see
 * `isBundleV4`.
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
  /**
   * Optional: absent on every bundle written before the archive's own
   * author fields were captured. Never rendered; see the privacy note on
   * `author` in the project plan, capturing it is fine, showing it is not.
   */
  author?: string;
  /** Optional, same reasons as `author`. */
  author_flair_text?: string;
  /** Posts only. Optional, same reasons as `author`. */
  link_flair_text?: string;
  /** Posts only. Optional, same reasons as `author`. */
  upvote_ratio?: number;
  /**
   * Whether this item matched every active phrase filter. Optional: absent
   * when no phrases were active, and on every bundle written before phrase
   * filtering existed.
   */
  phrase_match?: boolean;
  /**
   * Which selection tier this item landed in when the brief carried
   * requirements: "confirmed" met at least one and violated none, "violated"
   * contradicted at least one, "unconfirmed" is everything else (by far the
   * most common, since most posts never say who the author is). Optional:
   * absent when the brief had no requirements, and on every bundle written
   * before qualifier-aware triage existed.
   */
  qualifier_tier?: "confirmed" | "unconfirmed" | "violated";
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
  /**
   * A longer passage from the source item, when the engine sent one. Optional:
   * bundles written before excerpts existed carry only `text`.
   */
  excerpt?: string;
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

/** A shape the ideas can be written for. None chosen means "any". */
export type ContentType = "text" | "carousel" | "video" | "poll" | "article";

/** How the communities for this run were chosen. */
export type DiscoveryMode = "manual" | "model";

export interface Inputs {
  query: string;
  profile: string;
  /** Only the communities that actually contributed data. */
  subreddits: string[];
  /** The requested time range. Optional: bundles from before it returned as an
   * advanced setting do not carry it; the engine default is 30. */
  days?: number;
  /**
   * The formats the ideas were pinned to. Optional: absent on every run that
   * asked for a mix, which is any run that chose none of them or all of them.
   */
  content_types?: ContentType[];
  /**
   * The single format runs were pinned to before the setting took several.
   * Read only: stored history and shared bundles still carry it, and it means
   * the same as a one-item `content_types`. Never written by the engine now.
   */
  content_type?: ContentType;
  /** Optional: absent on bundles written before the engine recorded it. */
  discovery?: DiscoveryMode;
  /**
   * Quoted phrases parsed out of `query`, in the order they first appeared.
   * Optional: absent when the query had none, and on every bundle written
   * before phrase filtering existed.
   */
  phrases?: string[];
}

/**
 * One requirement decomposed from the brief, describing who should (or
 * should not) have posted a thread, plus how the analysed sample treated it.
 *
 * `text` is always a positive sentence regardless of `kind`: a must-not is
 * normalised to something like "The author is not a beginner or a solo
 * founder", so a reader never has to mentally re-negate it. `kind` survives
 * only so copy can say "you asked for" (require) versus "you ruled out"
 * (exclude). `met`, `violated` and `unknown` are counts of triaged
 * candidates, not of the final analysed sample; `unknown` is the common case,
 * since most Reddit posts never state who the author is.
 */
export interface RequirementInfo {
  text: string;
  kind: "require" | "exclude";
  met: number;
  violated: number;
  unknown: number;
}

/** How the analysed sample was chosen from the fetched pool. */
export interface SelectionInfo {
  /**
   * "relevance+engagement" when the triage call rated candidates on topic
   * only; "relevance+qualifiers" when the brief also carried requirements
   * about who posted, so candidates were rated against those too;
   * "deterministic-fallback" when selection ran on engagement and recency
   * alone.
   */
  method: string;
  /** Candidates the model actually rated (0 on fallback). */
  triaged: number;
  /**
   * How many candidates were rated 0 (unrelated), 1 (maybe), 2 (on-topic).
   * Optional: a deterministic-fallback run rates nothing and may omit it.
   */
  relevance_counts?: Record<"0" | "1" | "2", number>;
  /**
   * What the brief's topic was read as, once separated from any
   * requirements. Optional: present only alongside `requirements`.
   */
  topic?: string;
  /** Candidates considered for triage. Optional, same reasons as `topic`. */
  pool?: number;
  /**
   * The brief's decomposed requirements, in the order they were asked.
   * Optional: absent when the brief carried none, and on every bundle written
   * before qualifier-aware triage existed.
   */
  requirements?: RequirementInfo[];
  /**
   * How every triaged candidate landed against the requirements as a whole:
   * confirmed met at least one and violated none, violated contradicted at
   * least one, unconfirmed is everything else. Optional, same reasons as
   * `requirements`.
   */
  qualifier_counts?: { confirmed: number; unconfirmed: number; violated: number };
  /**
   * The same three counts, but over only the items that made the final
   * analysed sample rather than every triaged candidate. Optional, same
   * reasons as `requirements`.
   */
  qualifier_analysed?: { confirmed: number; unconfirmed: number; violated: number };
}

/**
 * How the quoted-phrase filter played out for this run.
 *
 * `exact` means only threads containing every phrase were read. `loose` means
 * too few did, so the archive-matched pool was kept and the phrase treated as
 * a preference rather than a filter. `unfiltered` means even that came back
 * too thin, so the filter was dropped entirely and the run covers recent
 * activity instead.
 */
export interface FilterInfo {
  phrases: string[];
  mode: "exact" | "loose" | "unfiltered";
  posts_matched: number;
  comments_matched: number;
  /** Pool size before the filter decision was made. */
  posts_scanned: number;
  comments_scanned: number;
  /** Phrases refused before fetching, e.g. too short or too long. */
  dropped: string[];
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
  /** Present only when a quoted-phrase filter was active for this run. */
  filter?: FilterInfo;
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
  /**
   * A short generated title for the run. Optional: bundles written before the
   * engine produced one fall back to the query.
   */
  report_title?: string;
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
  /**
   * The same two numbers over the analysed sample rather than the whole fetch.
   * Optional: older bundles carry only the fetch-wide totals.
   */
  analysed?: { upvotes: number; comments: number };
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
  /** Alias some engine versions emit for `lag_hours`. Read both, prefer this. */
  data_lag_hours?: number;
  trend: TrendPoint[];
}

export interface Bundle {
  bundle_version: number;
  /**
   * The id this run was saved under, when it was saved at all.
   *
   * Not written by the engine: the NDJSON terminal line carries it *alongside*
   * the bundle (`{"type": "report", "bundle": {...}, "report_id": "..."}`)
   * rather than inside it, so what the server stores is byte-for-byte what the
   * engine produced and re-exporting a saved report cannot smuggle an id into
   * the file. This field is where the client stashes it after the fact, which
   * is what carries it into history, into the export buttons and into the
   * draft dialog without threading a second value through every component.
   *
   * Absent whenever the save failed, whenever the database is not configured,
   * and on every history entry written before saved reports existed. Every
   * feature that depends on it is additive for exactly that reason.
   */
  report_id?: string;
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
  /** Omitted entirely when the user asked for a mix. */
  content_types?: ContentType[];
}

export type StreamLine =
  | { type: "progress"; message: string }
  /** `report_id` is present only when the run was saved. See Bundle.report_id. */
  | { type: "report"; bundle: Bundle; report_id?: string }
  | { type: "invalid"; errors: string[] }
  | { type: "error"; message: string };

/** GET /api/reports/{id}. Draft keys are the idea index as a string. */
export interface SavedReport {
  bundle: Bundle;
  drafts: Record<string, string>;
}

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
  /**
   * Where to file the finished post. Both are sent together or not at all, and
   * only for a report that was saved: with them the server stores the draft as
   * it hands it back, so a refresh returns it.
   */
  report_id?: string;
  idea_index?: number;
}

/** POST /api/drafts: an already-written post, saved against a stored report. */
export interface SaveDraftRequest {
  report_id: string;
  idea_index: number;
  post: string;
}

export type DraftResponse = { post: string } | { error: string };

export const MAX_DRAFT_EVIDENCE = 6;

/* ── Constants mirrored from the engine ───────────────────────────────────── */

export const MIN_QUERY_CHARS = 10;
export const MAX_QUERY_CHARS = 500;
export const MAX_PROFILE_CHARS = 500;
export const TIME_WINDOWS = [7, 14, 30] as const;
export const DEFAULT_DAYS = 30;
export const MAX_MANUAL_SUBREDDITS = 4;

/** Quoted-phrase filter limits, mirrored from `keywords.py`. */
export const MAX_PHRASES = 3;
export const MIN_PHRASE_CHARS = 3;
export const MAX_PHRASE_CHARS = 60;
export const MAX_PHRASE_WORDS = 8;

/** The checkbox group, in the order it renders. None ticked means "any". */
export const CONTENT_TYPE_CHOICES: { value: ContentType; label: string }[] = [
  { value: "text", label: "Text post" },
  { value: "carousel", label: "Carousel" },
  { value: "video", label: "Video script" },
  { value: "poll", label: "Poll" },
  { value: "article", label: "Long-form article" },
];

export function isContentType(value: string): value is ContentType {
  return CONTENT_TYPE_CHOICES.some((choice) => choice.value === value);
}

/**
 * The content types a URL asked for: comma-separated, deduped, menu order.
 *
 * A link written before the setting took several values carries one bare name,
 * which the split handles without a special case. Anything unrecognised is
 * dropped rather than refused, because a bad parameter should not stop the
 * rest of a shared search from loading.
 */
export function parseContentTypes(value: string | null): ContentType[] {
  const asked = new Set(
    (value ?? "").split(",").map((part) => part.trim().toLowerCase()),
  );
  return CONTENT_TYPE_CHOICES.filter((choice) => asked.has(choice.value)).map(
    (choice) => choice.value,
  );
}

/**
 * The formats a stored bundle's ideas were written for.
 *
 * A bundle from before the setting took several values carries the single
 * `content_type`, and saved history is full of them, so both fields are read
 * and the older one renders as a list of one.
 */
export function bundleContentTypes(inputs?: Inputs): ContentType[] {
  if (inputs?.content_types && inputs.content_types.length > 0) {
    return inputs.content_types;
  }
  return inputs?.content_type ? [inputs.content_type] : [];
}

export const SENTIMENT_LABELS: Record<SentimentKey, string> = {
  pos: "Positive",
  neu: "Neutral",
  neg: "Negative",
};

export const SENTIMENT_ORDER: SentimentKey[] = ["pos", "neu", "neg"];

function isObject(value: unknown): boolean {
  return typeof value === "object" && value !== null;
}

/**
 * Narrow an unknown payload before anything renders it.
 *
 * The check reaches one level into `stats`, because every section below the
 * ideas reads `stats.sentiment`, `stats.date_range` and `stats.engagement`
 * directly. A bundle missing any of them would pass a shallow guard and then
 * crash the report halfway down the page, which is a far worse failure than
 * refusing it at the door.
 */
export function isBundleV4(value: unknown): value is Bundle {
  if (!isObject(value)) return false;
  const candidate = value as Partial<Bundle>;
  if (candidate.bundle_version !== BUNDLE_VERSION) return false;
  if (!isObject(candidate.inputs)) return false;
  if (!isObject(candidate.analysis)) return false;
  if (!isObject(candidate.sample)) return false;
  if (!isObject(candidate.stats)) return false;
  const stats = candidate.stats as Partial<Stats>;
  return (
    isObject(stats.sentiment) && isObject(stats.date_range) && isObject(stats.engagement)
  );
}
