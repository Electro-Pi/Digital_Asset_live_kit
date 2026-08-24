import "server-only";
import type { NextRequest, NextResponse } from "next/server";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { AdminApiError } from "./http";
import { BASE_PATH } from "./base-path";
import { constantTimeEqual, hmacToken, randomOpaqueToken } from "./crypto";
import { getAdminEnv } from "./env";
import { requireExactOrigin, requestIp, requestUserAgent } from "./request";
import { AdminDatabaseError, callAdminRpc, createSupabaseAuthClient } from "./supabase";

// `__Secure-`, not `__Host-`: the dashboard shares an origin with the public
// site, so these must be scoped with `Path=/admin` and `__Host-` forbids any
// path but `/`. The prefix that was given up guaranteed no sibling name could
// overwrite the cookie; what the path buys instead is that public pages never
// receive the session at all, and that script running on them cannot read the
// CSRF token out of `document.cookie` and forge a mutation. That trade is worth
// making, because a token they cannot read is worth more than a token they
// cannot replace — an opaque session they plant still has to exist in the
// database to authenticate anything.
export const PRODUCTION_SESSION_COOKIE = "__Secure-coda-admin-session";
export const DEVELOPMENT_SESSION_COOKIE = "coda-admin-session";
export const PRODUCTION_CSRF_COOKIE = "__Secure-coda-admin-csrf";
export const DEVELOPMENT_CSRF_COOKIE = "coda-admin-csrf";
export const SESSION_MAX_AGE_SECONDS = 8 * 60 * 60;

export type AdminRole = "owner" | "operator" | "viewer";

export type AdminIdentity = {
  id: string;
  authUserId: string;
  email: string;
  role: AdminRole;
};

export type AdminRequestContext = {
  admin: AdminIdentity;
  tokenHash: string;
  csrfHash: string | null;
  ip: string;
};

type JsonRecord = Record<string, unknown>;

function asRecord(value: unknown): JsonRecord | null {
  if (Array.isArray(value) && value.length === 1) return asRecord(value[0]);
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as JsonRecord)
    : null;
}

function stringFrom(record: JsonRecord, ...keys: string[]): string | null {
  for (const key of keys) {
    if (typeof record[key] === "string" && record[key]) return record[key] as string;
  }
  return null;
}

function identityFrom(value: unknown, fallback?: { authUserId?: string; email?: string }): AdminIdentity | null {
  const outer = asRecord(value);
  const record = outer && asRecord(outer.admin) ? asRecord(outer.admin)! : outer;
  if (!record || record.valid === false || outer?.valid === false) return null;

  const role = stringFrom(record, "role") as AdminRole | null;
  const id = stringFrom(record, "adminId", "admin_id", "sessionId", "session_id", "id");
  const authUserId =
    stringFrom(record, "authUserId", "auth_user_id", "userId", "user_id") ??
    fallback?.authUserId ??
    null;
  const email = stringFrom(record, "email") ?? fallback?.email ?? null;
  if (!id || !authUserId || !email || !role || !["owner", "operator", "viewer"].includes(role)) {
    return null;
  }
  return { id, authUserId, email, role };
}

export function sessionCookieName(): string {
  return getAdminEnv().secureCookies ? PRODUCTION_SESSION_COOKIE : DEVELOPMENT_SESSION_COOKIE;
}

export function csrfCookieName(): string {
  return getAdminEnv().secureCookies ? PRODUCTION_CSRF_COOKIE : DEVELOPMENT_CSRF_COOKIE;
}

function cookieOptions(httpOnly: boolean) {
  return {
    httpOnly,
    secure: getAdminEnv().secureCookies,
    sameSite: "strict" as const,
    path: BASE_PATH,
    maxAge: SESSION_MAX_AGE_SECONDS,
  };
}

export function setAuthCookies(response: NextResponse, token: string, csrfToken: string): void {
  response.cookies.set(sessionCookieName(), token, cookieOptions(true));
  response.cookies.set(csrfCookieName(), csrfToken, cookieOptions(false));
}

export function clearAuthCookies(response: NextResponse): void {
  response.cookies.set(sessionCookieName(), "", { ...cookieOptions(true), maxAge: 0 });
  response.cookies.set(csrfCookieName(), "", { ...cookieOptions(false), maxAge: 0 });
}

export async function checkLoginRateLimit(email: string, ip: string): Promise<{ allowed: boolean; retryAfter: number }> {
  const data = await callAdminRpc<unknown>("admin_check_login_rate_limit", {
    p_email: email,
    p_ip: ip,
  });
  if (typeof data === "boolean") return { allowed: data, retryAfter: 900 };
  const record = asRecord(data);
  return {
    allowed: record?.allowed !== false,
    retryAfter:
      typeof record?.retryAfter === "number"
        ? record.retryAfter
        : typeof record?.retry_after === "number"
          ? record.retry_after
          : typeof record?.retry_after_seconds === "number"
            ? record.retry_after_seconds
          : 900,
  };
}

export async function recordLoginAttempt(email: string, ip: string, succeeded: boolean): Promise<void> {
  await callAdminRpc("admin_record_login_attempt", {
    p_email: email,
    p_ip: ip,
    p_succeeded: succeeded,
  });
}

export async function loginAdmin(
  email: string,
  password: string,
  request: NextRequest,
): Promise<{ admin: AdminIdentity; token: string; csrfToken: string }> {
  requireExactOrigin(request);
  const ip = requestIp(request);
  const rate = await checkLoginRateLimit(email, ip);
  if (!rate.allowed) {
    throw new AdminApiError(429, "rate_limited", "Too many login attempts. Try again later", rate.retryAfter);
  }

  const authClient = createSupabaseAuthClient();
  const { data: authData, error: authError } = await authClient.auth.signInWithPassword({ email, password });
  if (authError || !authData.user) {
    await recordLoginAttempt(email, ip, false);
    throw new AdminApiError(401, "invalid_credentials", "Invalid username or password");
  }

  const signOutResult = await authClient.auth.signOut({ scope: "global" });
  if (signOutResult.error) {
    console.error("[coda-admin] temporary authentication session revocation failed");
    throw new AdminApiError(401, "invalid_credentials", "Invalid username or password");
  }
  try {
    await recordLoginAttempt(email, ip, true);
  } catch {
    console.error("[coda-admin] successful login reservation could not be completed");
    throw new AdminApiError(401, "invalid_credentials", "Invalid username or password");
  }

  const token = randomOpaqueToken();
  const csrfToken = randomOpaqueToken();
  const env = getAdminEnv();

  let result: unknown;
  try {
    result = await callAdminRpc("admin_create_session", {
      p_auth_user: authData.user.id,
      p_token_hash: hmacToken(token, env.sessionHmacSecret),
      p_csrf_hash: hmacToken(csrfToken, env.sessionHmacSecret),
      p_ip: ip,
      p_user_agent: requestUserAgent(request),
    });
  } catch (error) {
    // Once Supabase accepted the password, no response detail may reveal
    // whether allowlisting or session issuance failed.
    console.error("[coda-admin] opaque admin session issuance failed");
    throw new AdminApiError(401, "invalid_credentials", "Invalid username or password");
  }

  const admin = identityFrom(result, { authUserId: authData.user.id, email });
  if (!admin) {
    throw new AdminApiError(401, "invalid_credentials", "Invalid username or password");
  }
  return { admin, token, csrfToken };
}

export async function requireAdmin(
  request: NextRequest,
  options: { csrf?: boolean; roles?: readonly AdminRole[] } = {},
): Promise<AdminRequestContext> {
  const token = request.cookies.get(sessionCookieName())?.value;
  if (!token || token.length < 32 || token.length > 256) {
    throw new AdminApiError(401, "authentication_required", "Authentication is required");
  }

  let csrfHash: string | null = null;
  if (options.csrf) {
    csrfHash = requireValidCsrf(request);
  }

  const tokenHash = hmacToken(token, getAdminEnv().sessionHmacSecret);
  const admin = await validateAdminTokenHash(tokenHash);
  if (options.roles && !options.roles.includes(admin.role)) {
    throw new AdminApiError(403, "insufficient_role", "Your role cannot perform this action");
  }
  return { admin, tokenHash, csrfHash, ip: requestIp(request) };
}

export function requireValidCsrf(request: NextRequest): string {
  requireExactOrigin(request);
  const cookieToken = request.cookies.get(csrfCookieName())?.value ?? "";
  const headerToken = request.headers.get("x-csrf-token") ?? "";
  if (!cookieToken || !headerToken || !constantTimeEqual(cookieToken, headerToken)) {
    throw new AdminApiError(403, "csrf_rejected", "The security token is invalid");
  }
  return hmacToken(cookieToken, getAdminEnv().sessionHmacSecret);
}

async function validateAdminTokenHash(tokenHash: string): Promise<AdminIdentity> {
  let result: unknown;
  try {
    result = await callAdminRpc("admin_validate_session", {
      p_token_hash: tokenHash,
      p_touch: true,
    });
  } catch (error) {
    if (
      error instanceof AdminDatabaseError &&
      ["P0001", "28000"].includes(error.databaseCode ?? "")
    ) {
      throw new AdminApiError(401, "invalid_session", "The session is no longer valid");
    }
    if (error instanceof AdminDatabaseError && error.databaseCode === "42501") {
      throw new AdminApiError(403, "admin_access_denied", "Administrative access is denied");
    }
    throw error;
  }
  const admin = identityFrom(result);
  if (!admin) throw new AdminApiError(401, "invalid_session", "The session is no longer valid");
  return admin;
}

/**
 * Authenticate protected server-rendered layouts before any admin HTML is sent.
 * `redirect()` is given an in-app path; Next prefixes the basePath onto the
 * Location header it emits.
 */
export async function requireAdminPage(): Promise<AdminIdentity> {
  const cookieStore = await cookies();
  const token = cookieStore.get(sessionCookieName())?.value;
  if (!token || token.length < 32 || token.length > 256) redirect("/login");
  try {
    return await validateAdminTokenHash(hmacToken(token, getAdminEnv().sessionHmacSecret));
  } catch {
    redirect("/login");
  }
}

export async function revokeAdminSession(tokenHash: string): Promise<void> {
  await callAdminRpc("admin_revoke_session", {
    p_token_hash: tokenHash,
    p_reason: "logout",
  });
}
