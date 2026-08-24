import { z } from "zod";

// The migration RPCs intentionally use PostgreSQL `integer` arguments. The
// agent value is multiplied by 60 inside the transaction, so validate before
// conversion rather than relying on a database overflow error.
export const POSTGRES_INTEGER_MAX = 2_147_483_647;
export const MAX_AGENT_MINUTES = Math.floor(POSTGRES_INTEGER_MAX / 60);
export const MAX_REQUEST_LIMIT = POSTGRES_INTEGER_MAX;

const nonNegativeInteger = (maximum: number) => z.number().int().min(0).max(maximum);

const loginEmail = z.string().trim().toLowerCase().max(254).pipe(z.email());
const loginPassword = z.string().min(1).max(1024);

// `username` is the public contract. The temporary `email` alias keeps the UI
// and API deployable in either order; both represent the same dedicated Auth
// email and are normalized into a single internal shape.
export const loginSchema = z
  .union([
    z.strictObject({ username: loginEmail, password: loginPassword }),
    z.strictObject({ email: loginEmail, password: loginPassword }),
  ])
  .transform((value) => ({
    username: "username" in value ? value.username : value.email,
    password: value.password,
  }));

export const planLimitsSchema = z.strictObject({
  agentMinutes: nonNegativeInteger(MAX_AGENT_MINUTES),
  ttsRequests: nonNegativeInteger(MAX_REQUEST_LIMIT),
  sttRequests: nonNegativeInteger(MAX_REQUEST_LIMIT),
  llmRequests: nonNegativeInteger(MAX_REQUEST_LIMIT),
});

export const userLimitsSchema = z.strictObject({
  agentMinutes: nonNegativeInteger(MAX_AGENT_MINUTES).nullable(),
  ttsRequests: nonNegativeInteger(MAX_REQUEST_LIMIT).nullable(),
  sttRequests: nonNegativeInteger(MAX_REQUEST_LIMIT).nullable(),
  llmRequests: nonNegativeInteger(MAX_REQUEST_LIMIT).nullable(),
});

export const bookingStatusSchema = z.strictObject({
  status: z.enum(["new", "contacted", "scheduled", "done", "canceled"]),
});

export const uuidSchema = z.uuid();
export const planIdSchema = z.string().trim().min(1).max(64).regex(/^[a-z0-9_-]+$/i);
export const rangeSchema = z.enum(["week", "month", "year"]);

export function pageParams(searchParams: URLSearchParams): { page: number; pageSize: number } {
  return {
    page: z.coerce.number().int().min(1).catch(1).parse(searchParams.get("page") ?? 1),
    pageSize: z.coerce.number().int().min(1).max(100).catch(25).parse(searchParams.get("pageSize") ?? 25),
  };
}

export function optionalQuery(searchParams: URLSearchParams, key: string, max = 200): string | null {
  const value = searchParams.get(key)?.trim();
  return value ? value.slice(0, max) : null;
}

export function enumQuery<T extends readonly [string, ...string[]]>(
  searchParams: URLSearchParams,
  key: string,
  values: T,
  fallback: T[number] | null = null,
): T[number] | null {
  return z.enum(values).nullable().catch(fallback).parse(searchParams.get(key));
}

// ── API keys ────────────────────────────────────────────────────────────────

/** Scopes a key may carry, mirroring the api_keys_scopes_valid check constraint. */
export const API_KEY_SCOPES = ["tts", "agent", "stt", "llm"] as const;

export const apiKeyScopeSchema = z.enum(API_KEY_SCOPES);

export const apiKeyCreateSchema = z.strictObject({
  email: z.string().trim().toLowerCase().max(254).pipe(z.email()),
  label: z.string().trim().min(1).max(60),
  // Deduplicated because the constraint permits a repeated element but
  // `'agent' = any(scopes)` would then be satisfied twice for no reason.
  scopes: z
    .array(apiKeyScopeSchema)
    .min(1)
    .max(API_KEY_SCOPES.length)
    .transform((values) => [...new Set(values)]),
  // The database caps this at 100; keep the two in step.
  maxConcurrent: z.number().int().min(1).max(100).default(1),
  // Sent as an ISO instant. The RPC rejects anything already in the past, so
  // only the shape is checked here.
  expiresAt: z.iso.datetime({ offset: true }).nullable().default(null),
});

/** Tuple form, because enumQuery needs a readonly [string, ...string[]]. */
export const API_KEY_STATUSES = ["active", "revoked", "expired"] as const;

export const apiKeyStatusSchema = z.enum(API_KEY_STATUSES);
