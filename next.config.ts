import type { NextConfig } from "next";

/**
 * In development the Python API runs separately (uvicorn api.index:app on
 * :8000) and /api/* is proxied to it. In production Next.js and the Python
 * functions share a domain, so the rewrite must not exist: it would shadow the
 * real deployment routing.
 */
const API_DEV_ORIGIN = process.env.API_DEV_ORIGIN ?? "http://127.0.0.1:8000";

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
  "connect-src 'self'",
  "frame-ancestors 'none'",
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
