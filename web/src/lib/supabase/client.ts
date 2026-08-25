import { createBrowserClient } from "@supabase/ssr";

/**
 * Supabase client for use in Client Components. Only the anon/publishable
 * key is used here -- it is safe to expose to the browser by design.
 * Never import the service role key into any file under `src/`.
 */
export function createClient() {
  return createBrowserClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!
  );
}
