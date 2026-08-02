"use client";

import { DropdownMenu } from "radix-ui";
import { useEffect, useState } from "react";

import { supabaseBrowser } from "@/lib/supabase/client";
import { clearHistory } from "@/lib/storage";

/**
 * Who is signed in, and the way out. The quietest thing in the header.
 *
 * A monogram rather than the Google profile photo: the photo lives on
 * lh3.googleusercontent.com, and showing it would mean loosening img-src in the
 * production CSP for one 24px circle. The initial carries the same "this is your
 * account" signal.
 *
 * The address and the sign-out control sit behind that monogram now instead of
 * beside it. Spelled out inline they were the two widest things in a header
 * whose actual job is the wordmark and the reports button, and an address you
 * cannot act on is reference material rather than a control: it is worth
 * having, it is not worth the room. Folding them into a menu is also what made
 * space for the theme toggle without the right-hand side becoming a queue of
 * four unrelated things.
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
    <DropdownMenu.Root>
      <DropdownMenu.Trigger
        // The address goes in the accessible name because the visible label is
        // a single letter: "Y" tells a screen reader nothing about whose
        // account this is, and the menu that would answer that has not been
        // opened yet.
        aria-label={email ? `Account: ${email}` : "Account"}
        className="flex size-8 shrink-0 items-center justify-center rounded-full bg-[var(--brand-soft)] text-xs font-semibold text-[var(--brand-ink)] transition-colors hover:bg-[var(--accent)] data-[state=open]:bg-[var(--accent)]"
      >
        <span aria-hidden="true">{monogram(email)}</span>
      </DropdownMenu.Trigger>

      <DropdownMenu.Portal>
        <DropdownMenu.Content
          align="end"
          sideOffset={8}
          className="surface z-50 min-w-[13rem] overflow-hidden p-0 text-sm data-open:animate-in data-open:fade-in-0 data-open:zoom-in-95 data-closed:animate-out data-closed:fade-out-0"
        >
          {/* A label, not an item: there is nothing to do to your own address,
              and making it focusable would put a dead stop at the top of a
              two-item menu. */}
          <DropdownMenu.Label className="flex flex-col gap-0.5 border-b px-3 py-2.5">
            <span className="h-quiet">Signed in as</span>
            <span className="truncate text-[0.8125rem] text-foreground-soft">
              {email || "…"}
            </span>
          </DropdownMenu.Label>

          <div className="p-1">
            <DropdownMenu.Item
              disabled={signingOut}
              // onSelect rather than onClick: it is the one handler Radix fires
              // for Enter and Space as well as a pointer, and preventing the
              // default keeps the menu open while the request is in flight
              // rather than closing over a button that still says "Signing
              // out…".
              onSelect={(event) => {
                event.preventDefault();
                void signOut();
              }}
              className="flex cursor-pointer items-center rounded-md px-2.5 py-2 text-sm font-medium text-foreground-soft outline-none select-none data-highlighted:bg-[var(--hush)] data-highlighted:text-foreground data-disabled:pointer-events-none data-disabled:opacity-45"
            >
              {signingOut ? "Signing out…" : "Sign out"}
            </DropdownMenu.Item>
          </div>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}
