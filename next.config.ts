import type { NextConfig } from "next";

/**
 * In development the Python API runs separately (uvicorn api.index:app on
 * :8000) and /api/* is proxied to it. In production Next.js and the Python
 * functions share a domain, so the rewrite must not exist: it would shadow the
 * real deployment routing.
 */
const API_DEV_ORIGIN = process.env.API_DEV_ORIGIN ?? "http://127.0.0.1:8000";

/**
 * The one foreign origin the browser is allowed to talk to: this project's own
 * Supabase host, for token refresh and the start of the Google OAuth flow.
 *
 * Derived from NEXT_PUBLIC_SUPABASE_URL at config time and reduced to a literal
 * scheme-plus-host, rather than written as `https://*.supabase.co`. The wildcard
 * would allow every Supabase project on the internet as a fetch target, which is
 * a meaningful difference for a CSP whose whole job is to name what may be
 * reached. Anything that is not an https URL is dropped rather than trusted, so
 * a malformed variable cannot widen the policy; it costs sign-in in that
 * deployment, which is the failure worth having.
 */
const SUPABASE_ORIGIN = (() => {
  try {
    const url = new URL(process.env.NEXT_PUBLIC_SUPABASE_URL ?? "");
    return url.protocol === "https:" ? url.origin : "";
  } catch {
    return "";
  }
})();

// A conservative baseline: everything this app actually needs (same-origin
// scripts/styles/fetches, inline styles from Tailwind/shadcn, data: images)
// is allowed, and everything else (third-party scripts, framing, foreign
// connect targets) is not. Tightening this further would mean auditing every
// asset source; loosening it should require a specific, named reason.
const CONTENT_SECURITY_POLICY = [
  "default-src 'self'",
  // Next.js App Router streams the RSC payload to the browser as a series of
  // inline `self.__next_f.push(...)` <script> tags. Without 'unsafe-inline'
  // the browser blocks them (script-src-elem), React never receives the flight
  // data, and the page ships as dead HTML that never hydrates. Removing this
  // requires per-request nonces, which would opt every page out of static
  // prerendering, so it stays until there is a reason to pay that cost.
  "script-src 'self' 'unsafe-inline'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data:",
  // Same-origin plus the Supabase project above. Note what is NOT here: no
  // Google origin, because signing in is a top-level navigation to Google and
  // back, not a fetch, and no avatar host, because the account affordance draws
  // a monogram rather than loading the Google profile photo.
  ["connect-src 'self'", SUPABASE_ORIGIN].filter(Boolean).join(" "),
  "frame-ancestors 'none'",
  // Neither of these inherits from default-src, so both have to be named. Without
  // form-action a script (or injected markup) could point a <form> at any host;
  // without base-uri an injected <base> tag could rewrite every relative URL on
  // the page. 'self' keeps forms and the document base on this origin.
  "form-action 'self'",
  "base-uri 'self'",
].join("; ");

const nextConfig: NextConfig = {
  poweredByHeader: false,
  async headers() {
    const headers = [
      { key: "X-Frame-Options", value: "DENY" },
      { key: "X-Content-Type-Options", value: "nosniff" },
      { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
    ];
    // Next.js dev mode needs eval and inline scripts for HMR and source maps,
    // which this CSP forbids. Production keeps the full policy.
    if (process.env.NODE_ENV !== "development") {
      headers.push({ key: "Content-Security-Policy", value: CONTENT_SECURITY_POLICY });
    }
    return [{ source: "/:path*", headers }];
  },
  async rewrites() {
    if (process.env.NODE_ENV !== "development") return [];
    return [{ source: "/api/:path*", destination: `${API_DEV_ORIGIN}/api/:path*` }];
  },
};

export default nextConfig;
