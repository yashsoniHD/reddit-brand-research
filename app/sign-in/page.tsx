import type { Metadata } from "next";

import { GoogleSignIn } from "@/components/google-sign-in";

/**
 * The one public page, and the only thing an unauthenticated visitor can reach.
 *
 * Deliberately not indexed: it is a door, not content, and a search result
 * pointing at a sign-in form for a tool nobody can browse is worse than no
 * result. The pitch lives in the page metadata and the share card instead.
 */
export const metadata: Metadata = {
  title: "Sign in",
  robots: { index: false, follow: false },
};

const RETURNING_FROM_A_FAILURE =
  "That sign-in did not complete. Please try again.";

export default async function SignInPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string; error?: string }>;
}) {
  const params = await searchParams;
  // Only ever a path on this site, checked here as well as in the callback and
  // the proxy: this value ends up in a redirect, and one careless hop is all an
  // open redirect needs.
  const wanted = params.next ?? "";
  const next = wanted.startsWith("/") && !wanted.startsWith("//") ? wanted : undefined;

  return (
    <main className="flex min-h-svh flex-col items-center justify-center gap-8 px-5 py-16">
      <div className="flex flex-col items-center gap-3 text-center">
        <span className="flex items-center gap-2.5 text-sm font-semibold tracking-[-0.012em] text-foreground">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/logo.svg" alt="" width={22} height={22} className="size-[22px]" />
          Reddit Content Ideas
        </span>
        <h1 className="t-title max-w-md text-balance text-foreground">
          Content ideas from real Reddit conversations.
        </h1>
        <p className="max-w-sm text-balance text-sm leading-relaxed text-muted-foreground">
          Sign in to run a search. Every idea comes back with the thread it came
          from.
        </p>
      </div>

      {params.error ? (
        <p role="alert" className="t-small max-w-sm text-balance text-center text-destructive">
          {RETURNING_FROM_A_FAILURE}
        </p>
      ) : null}

      <GoogleSignIn next={next} />
    </main>
  );
}
