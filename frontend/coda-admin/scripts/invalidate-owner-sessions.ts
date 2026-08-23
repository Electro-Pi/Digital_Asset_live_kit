import { createClient } from "@supabase/supabase-js";
import { z } from "zod";

const input = z.object({
  SUPABASE_URL: z.url(),
  SUPABASE_SERVICE_ROLE_KEY: z.string().min(20),
  ADMIN_OWNER_USER_ID: z.uuid(),
}).safeParse(process.env);

if (!input.success) {
  process.stderr.write("Session invalidation input is invalid.\n");
  process.exitCode = 1;
} else {
  const supabase = createClient(
    input.data.SUPABASE_URL,
    input.data.SUPABASE_SERVICE_ROLE_KEY,
    { auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false } },
  );
  const { data, error } = await supabase.rpc("admin_invalidate_credentials", {
    p_auth_user: input.data.ADMIN_OWNER_USER_ID,
  });
  if (error || data !== true) {
    process.stderr.write("Opaque admin sessions could not be invalidated. Keep the service stopped.\n");
    process.exitCode = 1;
  } else {
    process.stdout.write("All opaque sessions for the admin owner were invalidated.\n");
  }
}
