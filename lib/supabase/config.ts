/**
 * The two values every Supabase client in this app is built from.
 *
 * Read as direct `process.env.X` member accesses rather than through a helper,
 * because that is the only form Next.js inlines into the browser bundle: a
 * dynamic lookup like `process.env[name]` compiles to `undefined` client-side
 * and the browser client would be built with an empty URL.
 *
 * The publishable key is preferred and the legacy `anon` key is the fallback.
 * Both are the public half of the pair and both are safe in the browser, which
 * is the whole reason they carry the NEXT_PUBLIC_ prefix; the secret and service
 * role keys are never read here, or anywhere else in this directory.
 */
export const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL ?? "";

export const SUPABASE_PUBLIC_KEY =
  process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY ??
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ??
  "";

export const supabaseConfigured = Boolean(SUPABASE_URL && SUPABASE_PUBLIC_KEY);

/**
 * Fail loudly rather than building a client that cannot work.
 *
 * A misconfigured deployment otherwise looks like a redirect loop: the proxy
 * finds no session, sends the visitor to /sign-in, the sign-in button cannot
 * reach Supabase, and nothing in any of that names the missing variable.
 */
export function assertSupabaseConfigured(): void {
  if (supabaseConfigured) return;
  throw new Error(
    "Supabase is not configured. NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY (or NEXT_PUBLIC_SUPABASE_ANON_KEY) must be set. Run `vercel env pull` to write them into .env.local.",
  );
}
