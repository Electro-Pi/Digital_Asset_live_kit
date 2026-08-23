import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { resetAdminEnvForTests } from "@/lib/env";

const callAdminRpc = vi.fn();

vi.mock("@/lib/supabase", () => ({
  callAdminRpc,
  createSupabaseAuthClient: vi.fn(),
  AdminDatabaseError: class AdminDatabaseError extends Error {
    constructor(
      public readonly functionName: string,
      public readonly databaseCode: string | undefined,
      message: string,
    ) {
      super(message);
    }
  },
}));

const ENV = {
  SUPABASE_URL: "https://example.supabase.co",
  SUPABASE_ANON_KEY: "anon-key-that-is-long-enough",
  SUPABASE_SERVICE_ROLE_KEY: "service-key-that-is-long-enough",
  ADMIN_SESSION_HMAC_SECRET: "c".repeat(64),
  ADMIN_ORIGIN: "https://www.coda.eracoda.ai",
  ADMIN_TRUST_PROXY: "true",
};

const TOKEN = "t".repeat(43);
const CSRF = "csrf-value";
const SESSION_COOKIE = `__Secure-coda-admin-session=${TOKEN}`;

describe("protected API routes", () => {
  beforeEach(() => {
    Object.assign(process.env, ENV);
    resetAdminEnvForTests();
    callAdminRpc.mockReset();
  });

  it("returns 401 before querying user data when no session exists", async () => {
    const { GET } = await import("@/app/api/users/route");
    const response = await GET(new NextRequest(`${ENV.ADMIN_ORIGIN}/admin/api/users`));
    expect(response.status).toBe(401);
    expect(await response.json()).toMatchObject({ error: { code: "authentication_required" } });
    expect(callAdminRpc).not.toHaveBeenCalled();
  });

  it("returns 401 for a forged session cookie", async () => {
    callAdminRpc.mockResolvedValue({ valid: false });
    const { GET } = await import("@/app/api/users/route");
    const response = await GET(
      new NextRequest(`${ENV.ADMIN_ORIGIN}/admin/api/users`, { headers: { cookie: SESSION_COOKIE } }),
    );
    expect(response.status).toBe(401);
    expect(await response.json()).toMatchObject({ error: { code: "invalid_session" } });
    expect(callAdminRpc).toHaveBeenCalledTimes(1);
  });

  it("rejects a mutation without exact-Origin CSRF before any RPC", async () => {
    const { PATCH } = await import("@/app/api/plans/[planId]/limits/route");
    const response = await PATCH(
      new NextRequest(`${ENV.ADMIN_ORIGIN}/admin/api/plans/free/limits`, {
        method: "PATCH",
        headers: { cookie: SESSION_COOKIE, "content-type": "application/json" },
        body: JSON.stringify({ agentMinutes: 10, ttsRequests: 10, sttRequests: 10, llmRequests: 10 }),
      }),
      { params: Promise.resolve({ planId: "free" }) },
    );
    expect(response.status).toBe(403);
    expect(await response.json()).toMatchObject({ error: { code: "origin_rejected" } });
    expect(callAdminRpc).not.toHaveBeenCalled();
  });

  it("returns 403 when a viewer attempts a valid-CSRF mutation", async () => {
    callAdminRpc.mockResolvedValue({
      valid: true,
      adminId: "11111111-1111-4111-8111-111111111111",
      authUserId: "22222222-2222-4222-8222-222222222222",
      email: "viewer@example.com",
      role: "viewer",
    });
    const { PATCH } = await import("@/app/api/plans/[planId]/limits/route");
    const response = await PATCH(
      new NextRequest(`${ENV.ADMIN_ORIGIN}/admin/api/plans/free/limits`, {
        method: "PATCH",
        headers: {
          origin: ENV.ADMIN_ORIGIN,
          cookie: `${SESSION_COOKIE}; __Secure-coda-admin-csrf=${CSRF}`,
          "x-csrf-token": CSRF,
          "content-type": "application/json",
        },
        body: JSON.stringify({ agentMinutes: 10, ttsRequests: 10, sttRequests: 10, llmRequests: 10 }),
      }),
      { params: Promise.resolve({ planId: "free" }) },
    );
    expect(response.status).toBe(403);
    expect(await response.json()).toMatchObject({ error: { code: "insufficient_role" } });
    expect(callAdminRpc).toHaveBeenCalledTimes(1);
  });

  it("clears cookies after a trusted logout even when revocation fails", async () => {
    callAdminRpc.mockImplementation(async (functionName: string) => {
      if (functionName === "admin_validate_session") {
        return {
          valid: true,
          adminId: "11111111-1111-4111-8111-111111111111",
          authUserId: "22222222-2222-4222-8222-222222222222",
          email: "owner@example.com",
          role: "owner",
        };
      }
      throw new Error("database unavailable");
    });
    const { POST } = await import("@/app/api/auth/logout/route");
    const response = await POST(
      new NextRequest(`${ENV.ADMIN_ORIGIN}/admin/api/auth/logout`, {
        method: "POST",
        headers: {
          origin: ENV.ADMIN_ORIGIN,
          cookie: `${SESSION_COOKIE}; __Secure-coda-admin-csrf=${CSRF}`,
          "x-csrf-token": CSRF,
        },
      }),
    );
    expect(response.status).toBe(200);
    const setCookies = response.headers.getSetCookie().join("\n");
    expect(setCookies).toContain("__Secure-coda-admin-session=;");
    expect(setCookies).toContain("__Secure-coda-admin-csrf=;");
    expect(setCookies).toContain("Max-Age=0");
    // A clearing cookie only matches the one it is clearing if the path agrees.
    for (const cookie of response.headers.getSetCookie()) {
      expect(cookie).toContain("Path=/admin");
    }
  });

  it("scopes issued cookies to the mount so public pages never receive them", async () => {
    callAdminRpc.mockImplementation(async (functionName: string) => {
      if (functionName === "admin_check_login_rate_limit") return { allowed: true };
      if (functionName === "admin_create_session") {
        return {
          valid: true,
          adminId: "11111111-1111-4111-8111-111111111111",
          authUserId: "22222222-2222-4222-8222-222222222222",
          email: "owner@example.com",
          role: "owner",
        };
      }
      return {};
    });
    const { createSupabaseAuthClient } = await import("@/lib/supabase");
    vi.mocked(createSupabaseAuthClient).mockReturnValue({
      auth: {
        signInWithPassword: vi.fn(async () => ({
          data: { user: { id: "22222222-2222-4222-8222-222222222222" } },
          error: null,
        })),
        signOut: vi.fn(async () => ({ error: null })),
      },
    } as unknown as ReturnType<typeof createSupabaseAuthClient>);

    const { POST } = await import("@/app/api/auth/login/route");
    const response = await POST(
      new NextRequest(`${ENV.ADMIN_ORIGIN}/admin/api/auth/login`, {
        method: "POST",
        headers: {
          origin: ENV.ADMIN_ORIGIN,
          "content-type": "application/json",
          "x-real-ip": "203.0.113.7",
        },
        body: JSON.stringify({ username: "owner@example.com", password: "p".repeat(24) }),
      }),
    );

    expect(response.status).toBe(200);
    const cookies = response.headers.getSetCookie();
    expect(cookies).toHaveLength(2);
    for (const cookie of cookies) {
      // Path=/admin is what keeps the session off the public site's requests and
      // the CSRF token out of document.cookie on pages the dashboard does not own.
      expect(cookie).toContain("Path=/admin");
      expect(cookie).not.toContain("Path=/;");
      expect(cookie).toContain("Secure");
      expect(cookie).toContain("SameSite=strict");
    }
    expect(cookies.find((cookie) => cookie.startsWith("__Secure-coda-admin-session="))).toContain(
      "HttpOnly",
    );
    expect(
      cookies.find((cookie) => cookie.startsWith("__Secure-coda-admin-csrf=")),
    ).not.toContain("HttpOnly");
  });

  it("does not clear cookies for a cross-origin logout request", async () => {
    const { POST } = await import("@/app/api/auth/logout/route");
    const response = await POST(
      new NextRequest(`${ENV.ADMIN_ORIGIN}/admin/api/auth/logout`, {
        method: "POST",
        headers: {
          origin: "https://evil.example",
          cookie: `${SESSION_COOKIE}; __Secure-coda-admin-csrf=${CSRF}`,
          "x-csrf-token": CSRF,
        },
      }),
    );
    expect(response.status).toBe(403);
    expect(response.headers.getSetCookie()).toEqual([]);
    expect(callAdminRpc).not.toHaveBeenCalled();
  });
});
