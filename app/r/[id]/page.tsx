import type { Metadata } from "next";

import { SavedReport } from "@/components/saved-report";

/**
 * A saved report, reachable by its link and nothing else.
 *
 * The id is the only credential, so this page must never be indexed: a crawler
 * that found one link would otherwise put the report in a search result. The
 * page has no server-rendered content anyway (the bundle is fetched in the
 * browser, see components/saved-report.tsx), so there is nothing to lose.
 */
export const metadata: Metadata = {
  title: "Shared report",
  robots: { index: false, follow: false },
};

export default async function SavedReportPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <SavedReport id={id} />;
}
