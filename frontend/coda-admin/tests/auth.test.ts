import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { resetAdminEnvForTests } from "@/lib/env";
import { hmacToken } from "@/lib/crypto";

const callAdminRpc = vi.fn();
const createSupabaseAuthClient = vi.fn();

vi.mock("@/lib/supabase", () => ({
  callAdminRpc,
  createSupabaseAuthClient,
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
  ADMIN_SESSION_HMAC_SECRET: "b".repeat(64),
  ADMIN_ORIGIN: "https://www.coda.eracoda.ai",
  ADMIN_TRUST_PROXY: "true",
};

describe("admin session authentication", () => {
  beforeEach(() => {
    Object.assign(process.env, ENV);
    resetAdminEnvForTests();
    callAdminRpc.mockReset();
    createSupabaseAuthClient.mockReset();
  });

  it("rejects a request without an opaque session", async () => {
    const { requireAdmin } = await import("@/lib/auth");
    const request = new NextRequest("https://www.coda.eracoda.ai/admin/api/users");
    await expect(requireAdmin(request)).rejects.toMatchObject({ status: 401 });
    expect(callAdminRpc).not.toHaveBeenCalled();
  });

  it("hashes the cookie before validating it with the database", async () => {
    callAdminRpc.mockResolvedValue({
      valid: true,
      adminId: "11111111-1111-4111-8111-111111111111",
      authUserId: "22222222-2222-4222-8222-222222222222",
      email: "owner@example.com",
      role: "owner",
    });
    const { requireAdmin } = await import("@/lib/auth");
    const rawToken = "t".repeat(43);
    const request = new NextRequest("https://www.coda.eracoda.ai/admin/api/users", {
      headers: { cookie: `__Secure-coda-admin-session=${rawToken}` },
    });
    const context = await requireAdmin(request);
    expect(context.admin.role).toBe("owner");
    expect(callAdminRpc).toHaveBeenCalledWith("admin_validate_session", {
      p_token_hash: hmacToken(rawToken, ENV.ADMIN_SESSION_HMAC_SECRET),
      p_touch: true,
    });
  });

  it("rejects mismatched CSRF and insufficient roles before mutation", async () => {
    const { requireAdmin } = await import("@/lib/auth");
    const rawToken = "t".repeat(43);
    const badCsrf = new NextRequest("https://www.coda.eracoda.ai/admin/api/plans/free/limits", {
      method: "PATCH",
      headers: {
        origin: ENV.ADMIN_ORIGIN,
        cookie: `__Secure-coda-admin-session=${rawToken}; __Secure-coda-admin-csrf=cookie-value`,
        "x-csrf-token": "header-value",
      },
    });
    await expect(requireAdmin(badCsrf, { csrf: true })).rejects.toMatchObject({ status: 403 });

    callAdminRpc.mockResolvedValue({
      valid: true,
      adminId: "11111111-1111-4111-8111-111111111111",
      authUserId: "22222222-2222-4222-8222-222222222222",
      email: "viewer@example.com",
      role: "viewer",
    });
    const viewer = new NextRequest("https://www.coda.eracoda.ai/admin/api/plans/free/limits", {
      method: "PATCH",
      headers: {
        origin: ENV.ADMIN_ORIGIN,
        cookie: `__Secure-coda-admin-session=${rawToken}; __Secure-coda-admin-csrf=same-value`,
        "x-csrf-token": "same-value",
      },
    });
    await expect(
      requireAdmin(viewer, { csrf: true, roles: ["owner", "operator"] }),
    ).rejects.toMatchObject({ status: 403, code: "insufficient_role" });
  });

  it("revokes the temporary Supabase session before issuing an opaque session", async () => {
    const sequence: string[] = [];
    callAdminRpc.mockImplementation(async (functionName: string) => {
      sequence.push(functionName);
      if (functionName === "admin_check_login_rate_limit") return { allowed: true };
      if (functionName === "admin_create_session") {
        return {
          valid: true,
          admin_id: "11111111-1111-4111-8111-111111111111",
          auth_user_id: "22222222-2222-4222-8222-222222222222",
          email: "owner@example.com",
          role: "owner",
        };
      }
      return {};
    });
    createSupabaseAuthClient.mockReturnValue({
      auth: {
        signInWithPassword: vi.fn(async () => {
          sequence.push("supabase_sign_in");
          return {
            data: { user: { id: "22222222-2222-4222-8222-222222222222" } },
            error: null,
          };
        }),
        signOut: vi.fn(async () => {
          sequence.push("supabase_global_sign_out");
          return { error: null };
        }),
      },
    });

    const { loginAdmin } = await import("@/lib/auth");
    const request = new NextRequest(`${ENV.ADMIN_ORIGIN}/admin/api/auth/login`, {
      method: "POST",
      headers: { origin: ENV.ADMIN_ORIGIN, "x-real-ip": "203.0.113.7" },
    });
    await expect(loginAdmin("owner@example.com", "not-logged", request)).resolves.toMatchObject({
      admin: { role: "owner" },
    });
    expect(sequence).toEqual([
      "admin_check_login_rate_limit",
      "supabase_sign_in",
      "supabase_global_sign_out",
      "admin_record_login_attempt",
      "admin_create_session",
    ]);
  });

  it("never creates an opaque session when Supabase revocation fails", async () => {
    callAdminRpc.mockResolvedValue({ allowed: true });
    createSupabaseAuthClient.mockReturnValue({
      auth: {
        signInWithPassword: vi.fn(async () => ({
          data: { user: { id: "22222222-2222-4222-8222-222222222222" } },
          error: null,
        })),
        signOut: vi.fn(async () => ({ error: new Error("unavailable") })),
      },
    });

    const { loginAdmin } = await import("@/lib/auth");
    const request = new NextRequest(`${ENV.ADMIN_ORIGIN}/admin/api/auth/login`, {
      method: "POST",
      headers: { origin: ENV.ADMIN_ORIGIN, "x-real-ip": "203.0.113.7" },
    });
    await expect(loginAdmin("owner@example.com", "not-logged", request)).rejects.toMatchObject({
      status: 401,
      code: "invalid_credentials",
    });
    expect(callAdminRpc.mock.calls.map(([name]) => name)).not.toContain("admin_create_session");
  });
});
