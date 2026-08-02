"use client";

import { createBrowserClient } from "@supabase/ssr";
import type { SupabaseClient } from "@supabase/supabase-js";

import { SUPABASE_PUBLIC_KEY, SUPABASE_URL, assertSupabaseConfigured } from "./config";

/**
 * Named explicitly rather than inferred with `ReturnType<typeof
 * createBrowserClient>`: that helper is overloaded (the deprecated cookie shape
 * is a second signature), so ReturnType resolves against the last overload and
 * the client's own types collapse into `any` at every call site.
 */
type BrowserClient = SupabaseClient;

/**
 * One Supabase client for the whole browser session.
 *
 * Memoised rather than created per call, and this matters beyond allocation: the
 * client owns the token-refresh timer and the auth state listeners, so a second
 * instance means two refreshers racing each other over the same cookies. Built
 * lazily so that nothing runs during prerender, where there is no document to
 * read cookies from.
 *
 * `createBrowserClient` from @supabase/ssr rather than `createClient` from
 * supabase-js: the session has to live in cookies, not localStorage, or the
 * server (proxy.ts, the OAuth callback route) cannot see it and every page load
 * would look signed out.
 */
let client: BrowserClient | undefined;

export function supabaseBrowser(): BrowserClient {
  if (!client) {
    assertSupabaseConfigured();
    client = createBrowserClient(SUPABASE_URL, SUPABASE_PUBLIC_KEY);
  }
  return client;
}

/**
 * The access token for the current session, or null when there is none.
 *
 * `getSession` and not `getUser`: this is the browser, the token is what we are
 * about to send to our own API, and `getUser` would spend a network round trip
 * per request asking Supabase to confirm what api/index.py is about to verify
 * anyway. `getSession` also refreshes an expired token on the way through, which
 * is the behaviour that keeps a long-running report from failing halfway.
 *
 * Resolves to null rather than throwing: a caller with no token still sends the
 * request, and lets the API answer 401. That keeps one place deciding what
 * "signed out" means.
 */
export async function accessToken(): Promise<string | null> {
  try {
    const { data } = await supabaseBrowser().auth.getSession();
    return data.session?.access_token ?? null;
  } catch {
    return null;
  }
}
