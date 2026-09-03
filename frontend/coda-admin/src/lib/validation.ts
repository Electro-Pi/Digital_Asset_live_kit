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

// Mirrors the ceiling in admin_grant_credits. Duplicated deliberately so a
// mistyped amount is refused in the browser rather than after a round trip,
// but the database remains the authority — never relax one without the other.
export const MAX_CREDIT_GRANT = 100_000_000;

export const creditGrantSchema = z.strictObject({
  amount: z.number().int().min(1).max(MAX_CREDIT_GRANT),
  reason: z.string().trim().min(1).max(120).default("admin_grant"),
  // Idempotency token. The ledger is uniquely indexed on it, so a retried or
  // double-clicked grant is recognised and refused rather than applied twice.
  ref: z.string().trim().min(1).max(120).nullable().default(null),
});

/** Tuple form, because enumQuery needs a readonly [string, ...string[]]. */
export const API_KEY_STATUSES = ["active", "revoked", "expired"] as const;

export const apiKeyStatusSchema = z.enum(API_KEY_STATUSES);

// ── Podcast ─────────────────────────────────────────────────────────────────

export const PODCAST_EPISODE_STATUSES = ["draft", "scheduled", "archived"] as const;
export const PODCAST_SEAT_STATUSES = ["new", "confirmed", "done", "canceled"] as const;

const optionalText = (max: number) =>
  z
    .string()
    .trim()
    .max(max)
    .nullable()
    .default(null)
    .transform((value) => (value ? value : null));

// Storage and the RPC both refuse anything else; keeping the check here means a
// pasted http:// link is rejected in the browser rather than after a round trip.
const httpsUrl = (max: number) => z.string().trim().max(max).regex(/^https:\/\/\S+$/);

export const podcastEpisodeSchema = z.strictObject({
  titleAr: z.string().trim().min(1).max(180),
  titleEn: z.string().trim().min(1).max(180),
  summaryAr: optionalText(1200),
  summaryEn: optionalText(1200),
  guestName: optionalText(160),
  videoUrl: httpsUrl(2048),
  // Set only for objects this dashboard uploaded; a pasted external link has no
  // storage path, and deleting such an episode must not try to remove one.
  videoPath: optionalText(400),
  thumbnailUrl: httpsUrl(2048).nullable().default(null),
  durationSeconds: z.number().int().min(0).max(86_400).nullable().default(null),
  // Sent as an instant with an explicit offset. The dashboard builds it from a
  // Riyadh wall-clock date and time with a fixed +03:00, so what the operator
  // typed is what Postgres stores.
  publishAt: z.iso.datetime({ offset: true }),
  status: z.enum(PODCAST_EPISODE_STATUSES).default("scheduled"),
});

export const podcastSeatStatusSchema = z.strictObject({
  status: z.enum(PODCAST_SEAT_STATUSES),
});

export const PODCAST_VIDEO_EXTENSIONS = ["mp4", "webm", "mov", "m4v"] as const;
export const PODCAST_IMAGE_EXTENSIONS = ["jpg", "jpeg", "png", "webp", "avif"] as const;

export const podcastUploadSchema = z
  .strictObject({
    kind: z.enum(["video", "thumbnail"]),
    extension: z.string().trim().toLowerCase().max(8).regex(/^[a-z0-9]+$/),
  })
  .refine(
    (value) =>
      value.kind === "video"
        ? (PODCAST_VIDEO_EXTENSIONS as readonly string[]).includes(value.extension)
        : (PODCAST_IMAGE_EXTENSIONS as readonly string[]).includes(value.extension),
    { message: "Unsupported file type", path: ["extension"] },
  );
