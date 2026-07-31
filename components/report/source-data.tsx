"use client";

import { ChevronDown, ChevronUp } from "lucide-react";
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
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
          horizontal scrollbar because of a wide data grid.

          No fill of its own. This renders inside an open Drawer, which is
          already `--card`, so the `bg-card` that used to be here was a card
          plate on a card: a hand-rolled `.surface` minus the shadow, painting
          nothing. The border and the radius are the whole job. */}
      <div className="overflow-x-auto rounded-xl border">
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
                  {/* `-my-1 py-1.5` takes the target past WCAG 2.2 SC 2.5.8's
                      24px floor without changing the row height: an 11px label
                      on its own rendered a control about 14px tall. */}
                  <button
                    type="button"
                    onClick={() => toggle(column.key)}
                    className={`h-quiet -my-1 inline-flex items-center gap-1 rounded py-1.5 transition-colors hover:text-foreground ${
                      sortKey === column.key
                        ? "text-[var(--brand-ink)]"
                        : "text-muted-foreground"
                    }`}
                  >
                    {column.label}
                    {/* A Lucide chevron, not a unicode triangle: every other
                        indicator in the app is a stroked icon, and the glyph
                        was rendering at an off-scale 9px. */}
                    {sortKey === column.key ? (
                      direction === "asc" ? (
                        <ChevronUp aria-hidden="true" strokeWidth={2.25} className="size-3" />
                      ) : (
                        <ChevronDown
                          aria-hidden="true"
                          strokeWidth={2.25}
                          className="size-3"
                        />
                      )
                    ) : null}
                  </button>
                </th>
              ))}
              <th scope="col" className="px-3 py-2.5 text-right">
                <span className="h-quiet">Link</span>
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
                    className={`t-small px-3 py-2.5 align-top ${
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
        // The real Button, not a fourth hand-rolled one at its own height and
        // its own hover opacity.
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => setVisible(visible + PAGE)}
          className="num mt-3"
        >
          Show {Math.min(PAGE, sorted.length - visible)} more of {sorted.length}
        </Button>
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
      <p className="mb-4 max-w-[70ch] text-xs leading-relaxed text-muted-foreground">
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
