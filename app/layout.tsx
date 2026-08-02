import type { Metadata } from "next";
import { Inter } from "next/font/google";

import { THEME_BOOT_SCRIPT } from "@/lib/theme";

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

/* One family for the whole product. A second, editorial serif was trialled on
   the two display tiers and rejected. If it is ever revisited, note that it has
   to come through next/font as Inter does: an @import or <link> to
   fonts.googleapis.com is a third-party request the CSP does not allow, so the
   font would simply never arrive in production. */

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

/* Both themes are real designs, so the browser chrome should follow suit, and
   `theme-color` is how that is said. It used to live here as a `viewport`
   export, which writes one tag per media query. That stopped being able to
   tell the truth the moment the theme became a choice: a media query cannot
   know the reader picked light on a dark machine, so the address bar would
   stay black above a white page. The tag is written by the boot script now,
   from whatever was actually resolved, and updated by the toggle. */

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    /* suppressHydrationWarning because the boot script below writes
       `data-theme` onto this element before React sees it, so the server's
       markup and the browser's disagree by exactly that attribute, by design.
       It suppresses the warning for this element's own attributes only, not
       for the tree underneath it. */
    <html
      lang="en"
      suppressHydrationWarning
      className={`${inter.variable} h-full antialiased`}
    >
      <head>
        {/* In <head>, ahead of the stylesheet and every byte of <body>: this
            has to have run before the first pixel or the reader watches the
            page repaint out from under them. dangerouslySetInnerHTML is the
            only way to emit an inline script from JSX, and the content is a
            module-level constant with no interpolated input. The production
            CSP already allows 'unsafe-inline' for script-src, which the RSC
            payload requires anyway, so this needs no policy change. */}
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOT_SCRIPT }} />
      </head>
      <body className="flex min-h-full flex-col">{children}</body>
    </html>
  );
}
