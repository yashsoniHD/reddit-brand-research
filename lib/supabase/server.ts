import { createServerClient } from "@supabase/ssr";
import { cookies } from "next/headers";

import { SUPABASE_PUBLIC_KEY, SUPABASE_URL, assertSupabaseConfigured } from "./config";

/**
 * A Supabase client for server-side Next.js: the OAuth callback route, and any
 * Server Component or Route Handler that needs to know who is signed in.
 *
 * Built per request and never cached at module scope. A serverless instance
 * serves many requests, so a shared client would carry one visitor's cookies
 * into the next visitor's request.
 *
 * Only `getAll` and `setAll` are implemented, which is not a preference: the
 * chunked cookie format @supabase/ssr writes cannot be read or removed one name
 * at a time, and the single-cookie `get`/`set`/`remove` methods are deprecated
 * for exactly that reason.
 */
export async function supabaseServer() {
  assertSupabaseConfigured();
  const cookieStore = await cookies();

  return createServerClient(SUPABASE_URL, SUPABASE_PUBLIC_KEY, {
    cookies: {
      getAll() {
        return cookieStore.getAll();
      },
      setAll(cookiesToSet) {
        try {
          for (const { name, value, options } of cookiesToSet) {
            cookieStore.set(name, value, options);
          }
        } catch {
          // A Server Component's cookie store is read-only, and a refresh that
          // happens during render therefore cannot write the rotated token back.
          // That is survivable rather than swallowed: proxy.ts refreshes the
          // session on every request it sees, so the cookie the browser holds is
          // already current by the time any component renders.
        }
      },
    },
  });
}
