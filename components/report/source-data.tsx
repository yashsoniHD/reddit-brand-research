"use client";

import { useMemo, useState } from "react";

import { ExternalLink } from "@/components/external-link";
import { Section } from "@/components/report/section";
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
      <div className="overflow-x-auto rounded-xl border bg-card">
        <table className="w-full min-w-[44rem] text-left text-sm">
          <thead>
            <tr className="border-b">
              {columns.map((column) => (
                <th
                  key={column.key}
                  scope="col"
                  className={`px-3 py-2 font-medium ${column.numeric ? "text-right" : ""}`}
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
                    className="inline-flex items-center gap-1 rounded text-xs tracking-wide text-muted-foreground uppercase hover:text-foreground"
                  >
                    {column.label}
                    <span aria-hidden="true" className="text-[10px]">
                      {sortKey === column.key ? (direction === "asc" ? "▲" : "▼") : ""}
                    </span>
                  </button>
                </th>
              ))}
              <th scope="col" className="px-3 py-2 text-right">
                <span className="text-xs tracking-wide text-muted-foreground uppercase">
                  Link
                </span>
              </th>
            </tr>
          </thead>
          <tbody>
            {shown.map((item) => (
              <tr key={`${item.kind}-${item.id}`} className="border-b last:border-b-0">
                {columns.map((column) => (
                  <td
                    key={column.key}
                    className={`px-3 py-2 align-top ${
                      column.numeric ? "text-right tabular-nums" : ""
                    } ${column.wide ? "max-w-[28rem] min-w-[18rem]" : "whitespace-nowrap"}`}
                  >
                    {column.key === "text"
                      ? truncate(item.title || item.body, 220)
                      : column.key === "date"
                        ? item.date
                        : String(cellValue(item, column.key))}
                  </td>
                ))}
                <td className="px-3 py-2 text-right align-top whitespace-nowrap">
                  {item.url ? <ExternalLink href={item.url}>Open</ExternalLink> : null}
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
          className="mt-3 rounded-lg border px-3 py-1.5 text-sm font-medium hover:bg-secondary"
        >
          Show {Math.min(PAGE, sorted.length - visible)} more of {sorted.length}
        </button>
      ) : (
        <p className="mt-3 text-xs text-muted-foreground">
          Showing all {sorted.length} rows.
        </p>
      )}
    </div>
  );
}

export function SourceData({ bundle }: { bundle: Bundle }) {
  return (
    <Section
      title="Source data"
      description="Every post and comment the report was built from. Sort any column, open any row on Reddit."
    >
      <Tabs defaultValue="posts">
        <TabsList>
          <TabsTrigger value="posts">Posts ({bundle.posts.length})</TabsTrigger>
          <TabsTrigger value="comments">Comments ({bundle.comments.length})</TabsTrigger>
        </TabsList>
        <TabsContent value="posts" className="mt-4">
          <SourceTable
            rows={bundle.posts}
            columns={POST_COLUMNS}
            emptyMessage="No posts were fetched for this run."
          />
        </TabsContent>
        <TabsContent value="comments" className="mt-4">
          <SourceTable
            rows={bundle.comments}
            columns={COMMENT_COLUMNS}
            emptyMessage="No comments were fetched for this run."
          />
        </TabsContent>
      </Tabs>
    </Section>
  );
}
