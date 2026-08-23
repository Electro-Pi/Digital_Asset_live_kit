import { createClient } from "@supabase/supabase-js";
import { z } from "zod";

const inputSchema = z.object({
  SUPABASE_URL: z.url(),
  SUPABASE_SERVICE_ROLE_KEY: z.string().min(20),
  ADMIN_OWNER_EMAIL: z.string().trim().toLowerCase().pipe(z.email()),
  ADMIN_OWNER_PASSWORD: z
    .string()
    .min(24, "ADMIN_OWNER_PASSWORD must contain at least 24 characters")
    .max(1024),
});

async function main(): Promise<void> {
  const parsed = inputSchema.safeParse(process.env);
  if (!parsed.success) {
    process.stderr.write(`Provisioning input is invalid: ${parsed.error.issues.map((issue) => issue.message).join("; ")}\n`);
    process.exitCode = 1;
    return;
  }

  const { SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, ADMIN_OWNER_EMAIL, ADMIN_OWNER_PASSWORD } = parsed.data;
  const supabase = createClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, {
    auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false },
  });

  const { data, error: createError } = await supabase.auth.admin.createUser({
    email: ADMIN_OWNER_EMAIL,
    password: ADMIN_OWNER_PASSWORD,
    email_confirm: true,
  });
  if (createError || !data.user) {
    throw new Error(
      "The dedicated Auth user could not be created. Provisioning never reuses or modifies an existing account.",
    );
  }

  const { data: bootstrap, error: bootstrapError } = await supabase.rpc("admin_bootstrap_owner", {
    p_auth_user: data.user.id,
  });
  if (bootstrapError || !bootstrap) {
    throw new Error(
      `The Auth user was created (${data.user.id}), but the owner allowlist was not bootstrapped. Investigate before retrying.`,
    );
  }

  process.stdout.write(`Coda admin owner provisioned for ${ADMIN_OWNER_EMAIL}.\n`);
}

main().catch((error: unknown) => {
  const message = error instanceof Error ? error.message : "Owner provisioning failed";
  process.stderr.write(`${message}\n`);
  process.exitCode = 1;
});
