import type { NextConfig } from "next";

/**
 * In development the Python API runs separately (uvicorn api.index:app on
 * :8000) and /api/* is proxied to it. In production Next.js and the Python
 * functions share a domain, so the rewrite must not exist: it would shadow the
 * real deployment routing.
 */
const API_DEV_ORIGIN = process.env.API_DEV_ORIGIN ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  async rewrites() {
    if (process.env.NODE_ENV !== "development") return [];
    return [{ source: "/api/:path*", destination: `${API_DEV_ORIGIN}/api/:path*` }];
  },
};

export default nextConfig;
