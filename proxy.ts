import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";

import { SUPABASE_PUBLIC_KEY, SUPABASE_URL, supabaseConfigured } from "@/lib/supabase/config";

/**
 * Two jobs on every page request: keep the session fresh, and let nobody who is
 * signed out see anything.
 *
 * Named `proxy.ts` with a `proxy` export because this is Next.js 16, where the
 * `middleware.ts` convention was renamed. The runtime is `nodejs` and cannot be
 * configured, which suits us: nothing here needs the edge.
 *
 * Refreshing is not incidental to gating. A Supabase access token lives about an
 * hour, and only a server that can write cookies can rotate it, which a Server
 * Component cannot do. If this file did not run, a reader would be signed out
 * an hour into a session with no way to notice until an API call failed.
 */

/**
 * Everything is behind sign-in. Three exceptions, all structural rather than
 * product decisions.
 *
 * `/sign-in` obviously cannot require a session. `/auth/...` is the OAuth
 * callback, which runs before there is one and is the thing that creates it:
 * gating it would deadlock the flow it completes. `/opengraph-image` is the
 * generated share card, fetched by crawlers (Slack, iMessage, X) that have no
 * way to authenticate, so gating it would silently break every link preview the
 * app produces. It leaks nothing: the card is a static drawing of the product
 * name and tagline with no report data in it.
 */
// Each pattern is anchored at both ends. `/opengraph-image` in particular is an
// exact match rather than a prefix: an unanchored /^\/opengraph-image/ would
// make /opengraph-image-anything public too, and only the one generated share
// card should be reachable without a session.
const PUBLIC_PATHS = [/^\/sign-in(?:\/.*)?$/, /^\/auth(?:\/.*)?$/, /^\/opengraph-image$/];

function isPublic(pathname: string): boolean {
  return PUBLIC_PATHS.some((pattern) => pattern.test(pathname));
}

/** Where to send the visitor back to once they have signed in. */
function signInUrl(request: NextRequest): URL {
  const url = request.nextUrl.clone();
  const wanted = `${request.nextUrl.pathname}${request.nextUrl.search}`;
  url.pathname = "/sign-in";
  url.search = "";
  // A share link is the case that makes this worth carrying: /r/<id> opened by
  // somebody not yet signed in should land on that report afterwards, not on the
  // landing page with the link lost.
  if (wanted && wanted !== "/") url.searchParams.set("next", wanted);
  return url;
}

export async function proxy(request: NextRequest) {
  // `supabaseResponse` is reassigned by setAll below, so it is `let` on purpose.
  let supabaseResponse = NextResponse.next({ request });

  // A deployment with no Supabase credentials cannot verify anybody, and the one
  // thing it must not do is decide that means everybody is allowed in. The pages
  // stay shut and the sign-in page, which throws a named error, is what explains
  // why. Checked before the client is built because building it would throw here
  // instead, on every request, including the one for /sign-in.
  if (!supabaseConfigured) {
    return isPublic(request.nextUrl.pathname)
      ? supabaseResponse
      : NextResponse.redirect(signInUrl(request));
  }

  const supabase = createServerClient(SUPABASE_URL, SUPABASE_PUBLIC_KEY, {
    cookies: {
      getAll() {
        return request.cookies.getAll();
      },
      setAll(cookiesToSet, headers) {
        for (const { name, value } of cookiesToSet) request.cookies.set(name, value);
        supabaseResponse = NextResponse.next({ request });
        for (const { name, value, options } of cookiesToSet) {
          supabaseResponse.cookies.set(name, value, options);
        }
        // The library asks for these (no-store, and friends) whenever it sets an
        // auth cookie, and they are not optional: a cached response that carries
        // a Set-Cookie for a rotated token would hand one reader's session to
        // the next reader who hit the same CDN node.
        for (const [key, value] of Object.entries(headers)) {
          supabaseResponse.headers.set(key, value);
        }
      },
    },
  });

  // Nothing between createServerClient and getClaims. Code in that gap is the
  // documented cause of readers being logged out at random, because the client
  // has to be the thing that observes the request's cookies first.
  //
  // getClaims rather than getSession: it verifies the token's signature instead
  // of trusting what the cookie says, which is the difference between a gate and
  // a suggestion. It costs nothing per request in the common case: for an
  // asymmetric token it checks the signature locally against the project's
  // published keys, which supabase-js caches process-wide rather than per client,
  // so the fetch happens once per cold start. A project still on the legacy
  // shared secret cannot be verified locally at all, and there getClaims falls
  // back to asking the auth server, which is slower but still a real check.
  const { data } = await supabase.auth.getClaims();
  const signedIn = Boolean(data?.claims?.sub);

  if (!signedIn && !isPublic(request.nextUrl.pathname)) {
    return NextResponse.redirect(signInUrl(request));
  }

  // A signed-in reader has no use for the door, and leaving it reachable means
  // the back button after signing in lands on a form that immediately bounces.
  if (signedIn && request.nextUrl.pathname.startsWith("/sign-in")) {
    const next = request.nextUrl.searchParams.get("next");
    const url = request.nextUrl.clone();
    // Only a path, never an absolute URL: `?next=https://elsewhere.example` on a
    // link would otherwise turn our own sign-in page into an open redirect.
    url.pathname = next && next.startsWith("/") && !next.startsWith("//") ? next : "/";
    url.search = "";
    return NextResponse.redirect(url);
  }

  // Returned exactly as it is, cookies included. A fresh NextResponse here would
  // drop the rotated token that setAll just wrote, which puts the browser and
  // the server out of sync and ends the session early.
  return supabaseResponse;
}

export const config = {
  /**
   * `api` is deliberately excluded, and it is the one subtle thing here.
   *
   * In production vercel.json rewrites /api/:path* straight to the Python
   * function, so this proxy never sees those requests and could not protect them
   * if it tried. api/index.py verifies the access token itself, on every route.
   * Guarding /api here in development only would mean local runs exercise a code
   * path production does not have, and a missing check in the Python layer would
   * surface for the first time in production. Excluding it keeps both
   * environments honest: FastAPI is the only thing in front of the API, always.
   *
   * The extension list keeps static assets (notably /logo.svg, which the sign-in
   * page needs before there is a session) out of the proxy entirely.
   *
   * `[^/?]*\.(ext)$` rather than `[^?]*\.(ext)`: the exemption is a single-segment
   * filename at the root that ENDS in an allowed extension, so it cannot be used
   * to slip a gated page past the proxy. Without the `$` anchor, /x.png/r/<id>
   * matched (an allowed extension anywhere in the path); without restricting the
   * segment to `[^/?]`, /r/<id>.css matched (a report path wearing a static
   * suffix). Both now run the proxy. `.json` is still not exempt at all — the
   * `js(?!on)` guard excludes it — so /r/<id>.json is gated like any page.
   */
  matcher: [
    "/((?!_next|api|[^/?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)$).*)",
  ],
};
