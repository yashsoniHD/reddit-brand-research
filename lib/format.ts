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
  return `${query} · ${inputs.days ?? ""}d`;
}

/** A stable identity for a bundle, used as a history key. */
export function bundleKey(bundle: Bundle): string {
  const inputs = bundle.inputs ?? {};
  return [
    bundle.generated_ts ?? 0,
    bundle.generated_at ?? "",
    inputs.query ?? "",
    (inputs.subreddits ?? []).join(","),
    inputs.days ?? "",
  ].join("|");
}
