import { NextResponse } from "next/server";

import { supabaseServer } from "@/lib/supabase/server";

/**
 * The origin to build the post-sign-in redirect on.
 *
 * In production this route runs behind Vercel's proxy, so the URL it was invoked
 * with (`origin`) names an internal host, and the browser-facing host arrives in
 * `x-forwarded-host`. That header is attacker-influenceable, and trusting it
 * blindly would turn a genuine sign-in into an open redirect to any host of the
 * attacker's choosing. So it is honoured only when it matches a host known to be
 * ours: the project's production domain or this deployment's own URL, both
 * Vercel-provided (VERCEL_PROJECT_PRODUCTION_URL is the production custom domain
 * when one is configured, VERCEL_URL / VERCEL_BRANCH_URL cover previews).
 * Anything else, and every non-Vercel or development run, falls back to the
 * request's own verified origin.
 */
function redirectOrigin(origin: string, forwardedHost: string | null): string {
  if (process.env.NODE_ENV === "development" || !forwardedHost) return origin;
  const allowedHosts = new Set(
    [
      process.env.VERCEL_PROJECT_PRODUCTION_URL,
      process.env.VERCEL_URL,
      process.env.VERCEL_BRANCH_URL,
    ].filter((host): host is string => Boolean(host)),
  );
  return allowedHosts.has(forwardedHost) ? `https://${forwardedHost}` : origin;
}

/**
 * Where Google sends the visitor back to, and the only place a session is made.
 *
 * The browser started the flow with `signInWithOAuth`, which is PKCE: the code
 * verifier was stored in a cookie, and the `code` in this URL is worthless
 * without it. Exchanging the two here, on the server, is what writes the session
 * cookies the proxy and api/index.py then read. It cannot happen in the browser,
 * because a cookie written by client JavaScript would not be there for the very
 * first server render after this redirect.
 *
 * Public in proxy.ts by necessity: this route runs before there is a session and
 * is the thing that creates one.
 */
export async function GET(request: Request) {
  const { searchParams, origin } = new URL(request.url);
  const code = searchParams.get("code");

  // Only ever a path on this site. An absolute `next` would make this route an
  // open redirect that launders its destination through a real sign-in, and
  // "//evil.example" is a protocol-relative URL, which is absolute too.
  const requested = searchParams.get("next") ?? "/";
  const next = requested.startsWith("/") && !requested.startsWith("//") ? requested : "/";

  // Google's own refusal (the visitor pressed Cancel, or the app is not approved)
  // arrives as `error`, not as a missing code, and it is not a bug to report as
  // one: the visitor is simply back where they started.
  const denied = searchParams.get("error");

  if (code && !denied) {
    try {
      const supabase = await supabaseServer();
      const { error } = await supabase.auth.exchangeCodeForSession(code);
      const base = redirectOrigin(origin, request.headers.get("x-forwarded-host"));
      if (!error) {
        // `x-forwarded-host` rather than `origin` in production: this function
        // sits behind Vercel's proxy, so the URL it was invoked with names an
        // internal host, and redirecting to that would send the browser nowhere.
        // Validated against known Vercel hosts (see redirectOrigin) so a spoofed
        // header cannot make this an open redirect.
        return NextResponse.redirect(`${base}${next}`);
      }
      // A single OAuth code is spent exactly once. A duplicate arrival of this
      // callback (a browser prefetch of the redirect target, a double
      // navigation, or the visitor refreshing this URL) tries to spend it again,
      // which Supabase reports as flow_state_already_used. If the first arrival
      // already established a session, that error is not a failure: send the
      // now-signed-in visitor on to where they were going instead of bouncing
      // them back to the sign-in page.
      const { data } = await supabase.auth.getClaims();
      if (data?.claims?.sub) {
        return NextResponse.redirect(`${base}${next}`);
      }
    } catch {
      // A misconfigured deployment (no Supabase credentials) throws here rather
      // than returning an error. It falls through to the same place.
    }
  }

  // One destination for every failure, and it is the sign-in page rather than a
  // dedicated error page: the only useful next action is to try again, and the
  // flag is what lets that page say so instead of looking like a fresh visit.
  const failed = new URL("/sign-in", origin);
  failed.searchParams.set("error", "1");
  if (next !== "/") failed.searchParams.set("next", next);
  return NextResponse.redirect(failed);
}
