import { z } from "zod";

const envSchema = z.object({
  SUPABASE_URL: z.url(),
  SUPABASE_ANON_KEY: z.string().min(20),
  SUPABASE_SERVICE_ROLE_KEY: z.string().min(20),
  ADMIN_SESSION_HMAC_SECRET: z.string().min(32),
  ADMIN_ORIGIN: z.url(),
  ADMIN_TRUST_PROXY: z.enum(["true", "false"]).default("false"),
  // Optional on purpose. It is needed only to issue API keys, and making it
  // required would stop the whole dashboard from booting on a deployment that
  // never issues one. Absence is reported at the point of use instead.
  API_KEY_PEPPER: z.string().min(16).optional(),
});

export type AdminEnv = {
  supabaseUrl: string;
  supabaseAnonKey: string;
  supabaseServiceRoleKey: string;
  sessionHmacSecret: string;
  adminOrigin: string;
  trustProxy: boolean;
  secureCookies: boolean;
  apiKeyPepper: string | null;
};

let cached: AdminEnv | undefined;

export function getAdminEnv(): AdminEnv {
  if (cached) return cached;

  const parsed = envSchema.safeParse(process.env);
  if (!parsed.success) {
    throw new Error("Coda admin server environment is not configured");
  }

  const origin = new URL(parsed.data.ADMIN_ORIGIN);
  if (origin.pathname !== "/" || origin.search || origin.hash) {
    throw new Error("ADMIN_ORIGIN must be an origin without a path, query, or fragment");
  }

  cached = {
    supabaseUrl: parsed.data.SUPABASE_URL.replace(/\/$/, ""),
    supabaseAnonKey: parsed.data.SUPABASE_ANON_KEY,
    supabaseServiceRoleKey: parsed.data.SUPABASE_SERVICE_ROLE_KEY,
    sessionHmacSecret: parsed.data.ADMIN_SESSION_HMAC_SECRET,
    adminOrigin: origin.origin,
    trustProxy: parsed.data.ADMIN_TRUST_PROXY === "true",
    secureCookies: origin.protocol === "https:",
    apiKeyPepper: parsed.data.API_KEY_PEPPER ?? null,
  };
  return cached;
}

/** Test-only cache reset; deliberately not exported from the package surface. */
export function resetAdminEnvForTests(): void {
  cached = undefined;
}
