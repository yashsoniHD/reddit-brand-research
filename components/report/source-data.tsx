"use client";

import { useMemo, useState } from "react";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { truncate } from "@/lib/format";
import type { Bundle, RedditItem } from "@/lib/types";

type SortKey = "subreddit" | "text" | "date" | "score" | "comments";
type Direction = "asc" | "desc";

const PAGE = 50;

interface Column {
  key: SortKey;
  label: string;
  numeric?: boolean;
  wide?: boolean;
}

const POST_COLUMNS: Column[] = [
  { key: "subreddit", label: "Subreddit" },
  { key: "text", label: "Title", wide: true },
  { key: "date", label: "Date" },
  { key: "score", label: "Score", numeric: true },
  { key: "comments", label: "Comments", numeric: true },
];

const COMMENT_COLUMNS: Column[] = [
  { key: "subreddit", label: "Subreddit" },
  { key: "text", label: "Comment", wide: true },
  { key: "date", label: "Date" },
  { key: "score", label: "Score", numeric: true },
];

function cellValue(item: RedditItem, key: SortKey): string | number {
  if (key === "subreddit") return item.subreddit ?? "";
  if (key === "text") return item.title || item.body || "";
  if (key === "date") return item.created_utc ?? 0;
  if (key === "score") return item.score ?? 0;
  return item.comments ?? 0;
}

function SourceTable({
  rows,
  columns,
  emptyMessage,
}: {
  rows: RedditItem[];
  columns: Column[];
  emptyMessage: string;
}) {
  const [sortKey, setSortKey] = useState<SortKey>("date");
  const [direction, setDirection] = useState<Direction>("desc");
  const [visible, setVisible] = useState(PAGE);

  const sorted = useMemo(() => {
    const copy = [...rows];
    copy.sort((a, b) => {
      const left = cellValue(a, sortKey);
      const right = cellValue(b, sortKey);
      let result: number;
      if (typeof left === "number" && typeof right === "number") {
        result = left - right;
      } else {
        result = String(left).localeCompare(String(right));
      }
      return direction === "asc" ? result : -result;
    });
    return copy;
  }, [rows, sortKey, direction]);

  if (rows.length === 0) {
    return <p className="text-sm text-muted-foreground">{emptyMessage}</p>;
  }

  const toggle = (key: SortKey) => {
    if (key === sortKey) {
      setDirection(direction === "asc" ? "desc" : "asc");
    } else {
      setSortKey(key);
      setDirection(key === "subreddit" || key === "text" ? "asc" : "desc");
    }
  };

  const shown = sorted.slice(0, visible);

  return (
    <div>
      {/* The table scrolls inside its own container: the page must never gain a
          horizontal scrollbar because of a wide data grid. */}
      <div className="overflow-x-auto rounded-xl border bg-card">
        <table className="w-full min-w-[44rem] text-left text-sm">
          <thead className="bg-[var(--hush)]">
            <tr className="border-b">
              {columns.map((column) => (
                <th
                  key={column.key}
                  scope="col"
                  className={`px-3 py-2.5 font-medium ${column.numeric ? "text-right" : ""}`}
                  aria-sort={
                    sortKey === column.key
                      ? direction === "asc"
                        ? "ascending"
                        : "descending"
                      : "none"
                  }
                >
                  <button
                    type="button"
                    onClick={() => toggle(column.key)}
                    className={`inline-flex items-center gap-1 rounded text-[0.6875rem] font-semibold tracking-[0.075em] uppercase transition-colors hover:text-foreground ${
                      sortKey === column.key
                        ? "text-[var(--brand-ink)]"
                        : "text-muted-foreground"
                    }`}
                  >
                    {column.label}
                    <span aria-hidden="true" className="text-[9px]">
                      {sortKey === column.key ? (direction === "asc" ? "▲" : "▼") : ""}
                    </span>
                  </button>
                </th>
              ))}
              <th scope="col" className="px-3 py-2.5 text-right">
                <span className="text-[0.6875rem] font-semibold tracking-[0.075em] text-muted-foreground uppercase">
                  Link
                </span>
              </th>
            </tr>
          </thead>
          <tbody>
            {shown.map((item) => (
              <tr
                key={`${item.kind}-${item.id}`}
                className="border-b transition-colors last:border-b-0 hover:bg-[var(--hush)]"
              >
                {columns.map((column) => (
                  <td
                    key={column.key}
                    className={`px-3 py-2.5 align-top text-[0.8125rem] leading-relaxed ${
                      column.numeric ? "num text-right" : ""
                    } ${column.wide ? "max-w-[28rem] min-w-[18rem]" : "whitespace-nowrap text-muted-foreground"}`}
                  >
                    {column.key === "text"
                      ? truncate(item.title || item.body, 220)
                      : column.key === "date"
                        ? item.date
                        : String(cellValue(item, column.key))}
                  </td>
                ))}
                <td className="px-3 py-2.5 text-right align-top whitespace-nowrap">
                  {item.url ? (
                    <a
                      href={item.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      title={`Open on Reddit: ${truncate(item.title || item.body, 80)}`}
                      className="inline-flex size-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-[var(--brand-soft)] hover:text-[var(--brand-ink)]"
                      aria-label={`Open on Reddit: ${truncate(item.title || item.body, 80)}`}
                    >
                      {/* The named entity does not resolve in JSX, so the
                          character itself is the only reliable arrow. */}
                      <span aria-hidden="true">{"↗"}</span>
                    </a>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {visible < sorted.length ? (
        <button
          type="button"
          onClick={() => setVisible(visible + PAGE)}
          className="num mt-3 h-9 rounded-lg border bg-card px-3.5 text-sm font-medium transition-colors hover:border-[var(--brand-line)] hover:bg-[var(--brand-soft)] hover:text-[var(--brand-ink)]"
        >
          Show {Math.min(PAGE, sorted.length - visible)} more of {sorted.length}
        </button>
      ) : (
        <p className="num mt-3 text-xs text-muted-foreground">
          Showing all {sorted.length} rows.
        </p>
      )}
    </div>
  );
}

export function SourceData({ bundle }: { bundle: Bundle }) {
  const posts = bundle.posts ?? [];
  const comments = bundle.comments ?? [];

  return (
    <div>
      <p className="mb-4 text-xs leading-relaxed text-muted-foreground">
        Every post and comment the report was built from. Sort any column, open any row
        on Reddit.
      </p>
      <Tabs defaultValue="posts">
        <TabsList className="h-9 rounded-lg">
          <TabsTrigger value="posts" className="num rounded-md px-3">
            Posts ({posts.length})
          </TabsTrigger>
          <TabsTrigger value="comments" className="num rounded-md px-3">
            Comments ({comments.length})
          </TabsTrigger>
        </TabsList>
        <TabsContent value="posts" className="mt-4">
          <SourceTable
            rows={posts}
            columns={POST_COLUMNS}
            emptyMessage="No posts were fetched for this run."
          />
        </TabsContent>
        <TabsContent value="comments" className="mt-4">
          <SourceTable
            rows={comments}
            columns={COMMENT_COLUMNS}
            emptyMessage="No comments were fetched for this run."
          />
        </TabsContent>
      </Tabs>
    </div>
  );
}
