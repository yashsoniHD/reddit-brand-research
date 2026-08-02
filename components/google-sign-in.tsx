"use client";

import { Loader2 } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { supabaseBrowser } from "@/lib/supabase/client";

const FAILED =
  "Sign-in could not be started just now. Please try again in a moment.";

/**
 * Google's wordmark G, inlined.
 *
 * An <img> pointing at Google's CDN would need img-src loosened in the CSP for
 * one 16px icon, and lucide has no brand marks. The four paths are the official
 * asset's own geometry, so it reads as the button every visitor has seen before.
 */
function GoogleMark() {
  return (
    <svg aria-hidden="true" viewBox="0 0 18 18" className="size-4" focusable="false">
      <path
        fill="#4285F4"
        d="M17.64 9.2c0-.64-.06-1.25-.16-1.84H9v3.48h4.84a4.14 4.14 0 0 1-1.8 2.72v2.26h2.91c1.7-1.57 2.69-3.88 2.69-6.62Z"
      />
      <path
        fill="#34A853"
        d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.91-2.26c-.81.54-1.84.86-3.05.86-2.34 0-4.32-1.58-5.03-3.7H.96v2.34A9 9 0 0 0 9 18Z"
      />
      <path
        fill="#FBBC05"
        d="M3.97 10.72a5.41 5.41 0 0 1 0-3.44V4.94H.96a9 9 0 0 0 0 8.12l3.01-2.34Z"
      />
      <path
        fill="#EA4335"
        d="M9 3.58c1.32 0 2.5.45 3.44 1.35l2.58-2.59A9 9 0 0 0 .96 4.94l3.01 2.34C4.68 5.16 6.66 3.58 9 3.58Z"
      />
    </svg>
  );
}

/**
 * The only control on the sign-in page.
 *
 * `redirectTo` points at our own /auth/callback rather than at a page, because
 * the code Google sends back has to be exchanged for a session server-side; see
 * app/auth/callback/route.ts. `next` rides along so a visitor who arrived on a
 * share link lands back on that report instead of the landing page.
 */
export function GoogleSignIn({ next }: { next?: string }) {
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const start = async () => {
    setError(null);
    setStarting(true);
    try {
      const callback = new URL("/auth/callback", window.location.origin);
      if (next) callback.searchParams.set("next", next);

      const { error: failure } = await supabaseBrowser().auth.signInWithOAuth({
        provider: "google",
        options: { redirectTo: callback.toString() },
      });
      // On success the browser is already navigating to Google, so `starting`
      // is deliberately left true: the button must not flick back to its resting
      // state during the redirect.
      if (failure) {
        setError(FAILED);
        setStarting(false);
      }
    } catch {
      // Thrown rather than returned when Supabase is not configured at all.
      setError(FAILED);
      setStarting(false);
    }
  };

  return (
    <div className="flex w-full max-w-[22rem] flex-col gap-3">
      <Button
        type="button"
        variant="outline"
        size="lg"
        onClick={() => void start()}
        disabled={starting}
        aria-busy={starting}
        className="h-11 w-full gap-2.5 rounded-xl px-5 text-[0.9375rem] font-medium"
      >
        {starting ? (
          <Loader2 aria-hidden="true" strokeWidth={2} className="size-4 animate-spin" />
        ) : (
          <GoogleMark />
        )}
        {starting ? "Taking you to Google…" : "Continue with Google"}
      </Button>
      {error ? (
        <p role="alert" className="t-small text-center text-destructive">
          {error}
        </p>
      ) : null}
    </div>
  );
}
