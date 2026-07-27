import type { Bundle } from "./types";

/** 12,400 */
export function thousands(value: number | undefined | null): string {
  const number = Number(value);
  if (!Number.isFinite(number)) return "0";
  return Math.trunc(number).toLocaleString("en-GB");
}

/** 12.4k rather than 12400, so a metric row stays scannable. */
export function compact(value: number | undefined | null): string {
  const number = Number(value);
  if (!Number.isFinite(number)) return "0";
  const whole = Math.trunc(number);
  if (Math.abs(whole) < 10000) return whole.toLocaleString("en-GB");
  if (Math.abs(whole) < 1000000) {
    return `${(whole / 1000).toFixed(1).replace(/\.0$/, "")}k`;
  }
  return `${(whole / 1000000).toFixed(1).replace(/\.0$/, "")}m`;
}

/** Collapse whitespace and cut to a link-label length. */
export function truncate(text: string | undefined | null, limit: number): string {
  const flat = String(text ?? "").split(/\s+/).filter(Boolean).join(" ");
  if (flat.length <= limit) return flat;
  return `${flat.slice(0, limit).trimEnd()}…`;
}

/**
 * Cut to a length without splitting a word.
 *
 * `truncate` is for labels, where a hard cut mid-word is acceptable noise. This
 * one is for headings, where "Productivity struggles freelance desig…" reads as
 * a bug rather than as an abbreviation.
 */
export function truncateWords(text: string | undefined | null, limit: number): string {
  const flat = String(text ?? "").split(/\s+/).filter(Boolean).join(" ");
  if (flat.length <= limit) return flat;
  const cut = flat.slice(0, limit);
  const lastSpace = cut.lastIndexOf(" ");
  // A single word longer than the limit has no boundary to fall back to.
  const body = lastSpace > limit * 0.5 ? cut.slice(0, lastSpace) : cut;
  return `${body.replace(/[,;:.\-–]+$/, "").trimEnd()}…`;
}

export const REPORT_TITLE_CHARS = 72;

/**
 * The heading a report is filed under: the engine's generated title when there
 * is one, otherwise the query itself, cut at a word.
 */
export function reportTitle(bundle: Bundle): string {
  const generated = String(bundle.analysis?.report_title ?? "").trim();
  if (generated) return truncateWords(generated, REPORT_TITLE_CHARS);
  const query = String(bundle.inputs?.query ?? "").trim();
  return query ? truncateWords(query, REPORT_TITLE_CHARS) : "Content ideas report";
}

/** 12.5 stays 12.5, 12.0 becomes 12. */
export function shareText(value: number | undefined | null): string {
  const number = Number(value);
  if (!Number.isFinite(number)) return "0";
  return String(Math.round(number * 10) / 10);
}

export const MAX_LABEL_QUERY_CHARS = 30;

/** Mirrors `pipeline.run_label` so history reads the same as the CLI. */
export function runLabel(bundle: Bundle): string {
  const inputs = bundle.inputs ?? {};
  let query = String(inputs.query ?? "Report").split(/\s+/).filter(Boolean).join(" ");
  if (query.length > MAX_LABEL_QUERY_CHARS) {
    query = `${query.slice(0, MAX_LABEL_QUERY_CHARS - 1).trimEnd()}…`;
  }
  return query;
}

/** Where a saved report lives. One definition, so the route, the copy button
 *  and the history drawer cannot disagree about it. */
export function reportPath(reportId: string): string {
  return `/r/${reportId}`;
}

/** The link to hand somebody else. Falls back to the bare path when there is
 *  no window to read an origin from, which only happens on the server. */
export function reportUrl(reportId: string): string {
  const path = reportPath(reportId);
  if (typeof window === "undefined") return path;
  return new URL(path, window.location.origin).toString();
}

/** A stable identity for a bundle, used as a history key. */
export function bundleKey(bundle: Bundle): string {
  const inputs = bundle.inputs ?? {};
  return [
    bundle.generated_ts ?? 0,
    bundle.generated_at ?? "",
    inputs.query ?? "",
    (inputs.subreddits ?? []).join(","),
  ].join("|");
}
