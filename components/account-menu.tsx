"use client";

import { useEffect, useState } from "react";

import { supabaseBrowser } from "@/lib/supabase/client";
import { clearHistory } from "@/lib/storage";

/**
 * Who is signed in, and the way out. The quietest thing in the header.
 *
 * A monogram rather than the Google profile photo: the photo lives on
 * lh3.googleusercontent.com, and showing it would mean loosening img-src in the
 * production CSP for one 24px circle. The initial carries the same "this is your
 * account" signal, and the email beside it is the part that actually identifies
 * the session.
 */
function monogram(email: string): string {
  const first = email.trim()[0];
  return first ? first.toUpperCase() : "?";
}

export function AccountMenu() {
  // Starts empty and fills in after mount. There is no server-rendered value to
  // match: this component renders inside a client page that the proxy has
  // already gated, so the question is never "is there a session" but "which
  // one", and asking costs one render.
  const [email, setEmail] = useState<string>("");
  const [signingOut, setSigningOut] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const supabase = supabaseBrowser();

    // getSession, not getClaims or getUser: this address is a label, and the
    // session cookie already holds it, so reading it costs nothing. Cookie-borne
    // user data must never decide access, and here it decides nothing at all:
    // proxy.ts verifies the token before this page renders, and api/index.py
    // verifies it again on every request. The worst a tampered cookie buys is a
    // wrong initial in the reader's own header.
    void supabase.auth.getSession().then(({ data }) => {
      if (!cancelled) setEmail(data.session?.user?.email ?? "");
    });

    // Signing out in another tab has to empty this one too, or the header keeps
    // advertising a session that no longer exists.
    const { data: subscription } = supabase.auth.onAuthStateChange((_event, session) => {
      if (!cancelled) setEmail(session?.user?.email ?? "");
    });

    return () => {
      cancelled = true;
      subscription.subscription.unsubscribe();
    };
  }, []);

  const signOut = async () => {
    setSigningOut(true);
    try {
      // Clear this user's saved history first, while the session cookie is still
      // present so the per-user key resolves. The reload below drops the
      // in-memory copy; this removes the persisted one, so nothing survives for
      // the next person to sign in on a shared browser.
      clearHistory();
      await supabaseBrowser().auth.signOut();
    } finally {
      // A full navigation rather than a router push: the page holds a report, a
      // draft cache and a history list, all of which belong to the session that
      // just ended, and reloading is the only way to be certain none of it
      // survives. The proxy turns "/" into /sign-in for a request with no
      // session, so this needs no destination of its own.
      window.location.assign("/");
    }
  };

  return (
    <div className="flex items-center gap-1.5">
      {email ? (
        <span className="flex items-center gap-2 pl-1 text-sm text-muted-foreground">
          <span
            aria-hidden="true"
            className="flex size-6 shrink-0 items-center justify-center rounded-full bg-[var(--brand-soft)] text-[0.6875rem] font-semibold text-[var(--brand-ink)]"
          >
            {monogram(email)}
          </span>
          {/* Hidden under sm, where the header has room for the wordmark, the
              history button and nothing else. The monogram stays. */}
          <span className="hidden max-w-[14rem] truncate sm:inline">{email}</span>
        </span>
      ) : null}
      <button
        type="button"
        onClick={() => void signOut()}
        disabled={signingOut}
        className="flex h-8 items-center rounded-lg border border-transparent px-2.5 text-sm font-medium text-muted-foreground transition-colors hover:border-border hover:bg-card hover:text-foreground disabled:cursor-not-allowed disabled:opacity-45"
      >
        {signingOut ? "Signing out…" : "Sign out"}
      </button>
    </div>
  );
}
