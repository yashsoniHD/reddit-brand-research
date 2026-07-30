import type { Metadata, Viewport } from "next";
import { Inter } from "next/font/google";

import "./globals.css";

/**
 * Self-hosted by next/font, so the strict same-origin CSP holds and there is
 * no third-party request on first paint. `display: swap` keeps the hero
 * readable while the file lands.
 */
const inter = Inter({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-inter",
});

const NAME = "Reddit Content Ideas";
const TAGLINE =
  "Turn what people actually say on Reddit into content you can publish";
const DESCRIPTION =
  "Type what you are looking for. We find the Reddit communities, search them for the threads that match, and hand back content ideas with the hook already written and every claim linked to the thread it came from. Written for the channels you actually post on: LinkedIn, Instagram, TikTok, X, YouTube or your newsletter.";

/**
 * The absolute base every relative metadata URL resolves against, notably the
 * generated Open Graph image.
 *
 * Without it Next.js warns at build time and falls back to
 * http://localhost:3000, so a shared link from production would point a
 * crawler at the sharer's own machine. VERCEL_PROJECT_PRODUCTION_URL is the
 * stable production domain Vercel sets on every build and every invocation,
 * production and preview alike, and it arrives as a bare host with no scheme.
 * Preview deployments deliberately resolve to the production domain too: the
 * image is identical on both, and a preview URL in a shared card would rot.
 * The literal is the fallback for a local `next build`, which sets no Vercel
 * variables at all.
 */
const SITE_URL = process.env.VERCEL_PROJECT_PRODUCTION_URL
  ? `https://${process.env.VERCEL_PROJECT_PRODUCTION_URL}`
  : "https://reddit-content-ideas.vercel.app";

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  title: {
    default: `${NAME}: ${TAGLINE}`,
    template: `%s · ${NAME}`,
  },
  description: DESCRIPTION,
  applicationName: NAME,
  keywords: [
    "content ideas",
    "Reddit research",
    "content research",
    "audience research",
    "social listening",
    "creator research",
  ],
  openGraph: {
    type: "website",
    siteName: NAME,
    title: `${NAME}: ${TAGLINE}`,
    description: DESCRIPTION,
    locale: "en_GB",
  },
  twitter: {
    card: "summary_large_image",
    title: `${NAME}: ${TAGLINE}`,
    description: DESCRIPTION,
  },
  icons: {
    icon: [{ url: "/logo.svg", type: "image/svg+xml" }],
    apple: [{ url: "/logo.svg", type: "image/svg+xml" }],
  },
};

export const viewport: Viewport = {
  // Both themes are real designs, so the browser chrome should follow suit.
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f7f7f5" },
    { media: "(prefers-color-scheme: dark)", color: "#0b0b0c" },
  ],
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className={`${inter.variable} h-full antialiased`}>
      <body className="flex min-h-full flex-col">{children}</body>
    </html>
  );
}
