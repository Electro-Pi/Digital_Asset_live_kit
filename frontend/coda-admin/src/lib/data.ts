import "server-only";
import { callAdminRpc, createSupabaseServiceClient } from "./supabase";
import { generateApiKey, hashApiKey } from "./api-key";
import { AdminApiError } from "./http";

export type PageInput = { page: number; pageSize: number };
export type Direction = "asc" | "desc";
type JsonRecord = Record<string, unknown>;

function camelKey(key: string): string {
  return key.replace(/_([a-z])/g, (_, letter: string) => letter.toUpperCase());
}

export function camelize(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(camelize);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as JsonRecord).map(([key, nested]) => [camelKey(key), camelize(nested)]),
    );
  }
  return value;
}

function unwrap(value: unknown): unknown {
  if (Array.isArray(value) && value.length === 1 && value[0] && typeof value[0] === "object") {
    return value[0];
  }
  return value;
}

function rpcData(value: unknown): unknown {
  const result = camelize(unwrap(value));
  if (result && typeof result === "object" && !Array.isArray(result) && "data" in result) {
    return (result as JsonRecord).data;
  }
  return result;
}

export async function getOverview(tokenHash: string, range: "week" | "month" | "year"): Promise<unknown> {
  return rpcData(
    await callAdminRpc("admin_overview", {
      p_token_hash: tokenHash,
      p_range: range,
    }),
  );
}

export async function listUsers(input: {
  tokenHash: string;
  search: string | null;
  plan: string | null;
  status: string | null;
  sort: string;
  direction: Direction;
  page: number;
  pageSize: number;
}): Promise<unknown> {
  return rpcData(
    await callAdminRpc("admin_list_users", {
      p_token_hash: input.tokenHash,
      p_search: input.search,
      p_plan: input.plan,
      p_status: input.status,
      p_sort: input.sort,
      p_direction: input.direction,
      p_page: input.page,
      p_page_size: input.pageSize,
    }),
  );
}

export async function getUser(tokenHash: string, userId: string): Promise<unknown> {
  return rpcData(
    await callAdminRpc("admin_get_user", {
      p_token_hash: tokenHash,
      p_user_id: userId,
    }),
  );
}

export async function updateUserLimits(input: {
  tokenHash: string;
  csrfHash: string;
  userId: string;
  agentMinutes: number | null;
  ttsRequests: number | null;
  sttRequests: number | null;
  llmRequests: number | null;
  requestId: string;
  ip: string;
}): Promise<unknown> {
  return rpcData(
    await callAdminRpc("admin_update_user_limits", {
      p_token_hash: input.tokenHash,
      p_csrf_hash: input.csrfHash,
      p_user_id: input.userId,
      p_agent_minutes: input.agentMinutes,
      p_tts_requests: input.ttsRequests,
      p_stt_requests: input.sttRequests,
      p_llm_requests: input.llmRequests,
      p_request_id: input.requestId,
      p_ip: input.ip,
    }),
  );
}

export async function listPlans(tokenHash: string): Promise<unknown> {
  const result = rpcData(
    await callAdminRpc("admin_list_plans", {
      p_token_hash: tokenHash,
    }),
  );
  return Array.isArray(result) ? { items: result, total: result.length } : result;
}

export async function updatePlanLimits(input: {
  tokenHash: string;
  csrfHash: string;
  planId: string;
  agentMinutes: number;
  ttsRequests: number;
  sttRequests: number;
  llmRequests: number;
  requestId: string;
  ip: string;
}): Promise<unknown> {
  return rpcData(
    await callAdminRpc("admin_update_plan_limits", {
      p_token_hash: input.tokenHash,
      p_csrf_hash: input.csrfHash,
      p_plan_id: input.planId,
      p_agent_minutes: input.agentMinutes,
      p_tts_requests: input.ttsRequests,
      p_stt_requests: input.sttRequests,
      p_llm_requests: input.llmRequests,
      p_request_id: input.requestId,
      p_ip: input.ip,
    }),
  );
}

export async function listBookings(input: {
  tokenHash: string;
  search: string | null;
  status: string | null;
  source: string | null;
  sort: string;
  direction: Direction;
  page: number;
  pageSize: number;
}): Promise<unknown> {
  return rpcData(
    await callAdminRpc("admin_list_bookings", {
      p_token_hash: input.tokenHash,
      p_search: input.search,
      p_status: input.status,
      p_source: input.source,
      p_sort: input.sort,
      p_direction: input.direction,
      p_page: input.page,
      p_page_size: input.pageSize,
    }),
  );
}

export async function updateBookingStatus(input: {
  tokenHash: string;
  csrfHash: string;
  bookingId: string;
  status: "new" | "contacted" | "scheduled" | "done" | "canceled";
  requestId: string;
  ip: string;
}): Promise<unknown> {
  return rpcData(
    await callAdminRpc("admin_update_booking_status", {
      p_token_hash: input.tokenHash,
      p_csrf_hash: input.csrfHash,
      p_booking_id: input.bookingId,
      p_status: input.status,
      p_request_id: input.requestId,
      p_ip: input.ip,
    }),
  );
}

export async function listAudit(input: {
  tokenHash: string;
  search: string | null;
  action: string | null;
  actor: string | null;
  page: number;
  pageSize: number;
}): Promise<unknown> {
  return rpcData(
    await callAdminRpc("admin_list_audit", {
      p_token_hash: input.tokenHash,
      p_search: input.search,
      p_action: input.action,
      p_actor: input.actor,
      p_page: input.page,
      p_page_size: input.pageSize,
    }),
  );
}

// ── API keys ────────────────────────────────────────────────────────────────

export async function listApiKeys(input: {
  tokenHash: string;
  search: string | null;
  scope: string | null;
  status: string | null;
  from: string | null;
  to: string | null;
  page: number;
  pageSize: number;
}): Promise<unknown> {
  const result = rpcData(
    await callAdminRpc("admin_list_api_keys", {
      p_token_hash: input.tokenHash,
      p_search: input.search,
      p_scope: input.scope,
      p_status: input.status,
      p_from: input.from,
      p_to: input.to,
      p_limit: input.pageSize,
      p_offset: (input.page - 1) * input.pageSize,
    }),
  );
  // The RPC pages by limit/offset; the dashboard's Pagination speaks
  // page/pageSize. Translate here so every list route answers one shape.
  const body = (result ?? {}) as JsonRecord;
  return {
    items: Array.isArray(body.items) ? body.items : [],
    total: typeof body.total === "number" ? body.total : 0,
    page: input.page,
    pageSize: input.pageSize,
    from: body.from ?? null,
    to: body.to ?? null,
  };
}

/**
 * Issue a key, creating the owner's Auth account first when the address is new.
 *
 * The raw key is returned exactly once, here, and is never stored: only
 * sha256(pepper || raw) reaches Postgres, so neither this dashboard nor a
 * stolen database dump can produce it again.
 *
 * Account creation goes through the Supabase Auth API rather than SQL because
 * that table's password and confirmation columns belong to Auth. The
 * on_auth_user_created trigger then provisions the profile and a free
 * subscription, so quota works for the new owner immediately.
 */
export async function createApiKey(input: {
  tokenHash: string;
  csrfHash: string;
  email: string;
  label: string;
  scopes: string[];
  maxConcurrent: number;
  expiresAt: string | null;
  requestId: string;
  ip: string;
}): Promise<unknown> {
  const email = input.email.trim().toLowerCase();

  // Look before creating. Creating first and reading the "already registered"
  // error would also work, but that message is not part of any contract and
  // has changed between GoTrue releases.
  let userId = await callAdminRpc<string | null>("admin_user_id_for_email", {
    p_token_hash: input.tokenHash,
    p_email: email,
  });

  if (!userId) {
    const { data, error } = await createSupabaseServiceClient().auth.admin.createUser({
      email,
      email_confirm: true,
    });
    if (error || !data?.user?.id) {
      // Losing a race against a concurrent signup is the expected failure
      // here, so re-read before giving up.
      userId = await callAdminRpc<string | null>("admin_user_id_for_email", {
        p_token_hash: input.tokenHash,
        p_email: email,
      });
      if (!userId) {
        throw new AdminApiError(
          502,
          "owner_provisioning_failed",
          "The owner account could not be created for that address",
        );
      }
    } else {
      userId = data.user.id;
    }
  }

  const { raw, prefix } = generateApiKey();

  // Should this RPC fail after an account was created above, the account is
  // left behind with no key. That is harmless and self-correcting: the lookup
  // at the top makes a retry reuse it rather than create a second one.
  const created = rpcData(
    await callAdminRpc("admin_create_api_key", {
      p_token_hash: input.tokenHash,
      p_csrf_hash: input.csrfHash,
      p_user_id: userId,
      p_label: input.label,
      p_scopes: input.scopes,
      p_key_hash: hashApiKey(raw),
      p_key_prefix: prefix,
      p_max_concurrent: input.maxConcurrent,
      p_expires_at: input.expiresAt,
      p_request_id: input.requestId,
      p_ip: input.ip,
    }),
  );

  return { ...(created as Record<string, unknown>), ownerEmail: email, key: raw };
}

export async function revokeApiKey(input: {
  tokenHash: string;
  csrfHash: string;
  keyId: string;
  requestId: string;
  ip: string;
}): Promise<unknown> {
  return rpcData(
    await callAdminRpc("admin_revoke_api_key", {
      p_token_hash: input.tokenHash,
      p_csrf_hash: input.csrfHash,
      p_key_id: input.keyId,
      p_request_id: input.requestId,
      p_ip: input.ip,
    }),
  );
}
