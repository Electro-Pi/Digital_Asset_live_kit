import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { NextRequest } from "next/server";
import { resetAdminEnvForTests } from "@/lib/env";
import { AdminApiError } from "@/lib/http";
import { readJsonBody, requestIp, requireExactOrigin } from "@/lib/request";

const ENV = {
  SUPABASE_URL: "https://example.supabase.co",
  SUPABASE_ANON_KEY: "anon-key-that-is-long-enough",
  SUPABASE_SERVICE_ROLE_KEY: "service-key-that-is-long-enough",
  ADMIN_SESSION_HMAC_SECRET: "a".repeat(64),
  ADMIN_ORIGIN: "https://www.coda.eracoda.ai",
  ADMIN_TRUST_PROXY: "true",
};

describe("request boundary", () => {
  beforeEach(() => {
    Object.assign(process.env, ENV);
    resetAdminEnvForTests();
  });

  afterEach(() => resetAdminEnvForTests());

  it("requires the exact configured origin", () => {
    const accepted = new NextRequest("https://www.coda.eracoda.ai/admin/api/test", {
      headers: { origin: ENV.ADMIN_ORIGIN },
    });
    expect(() => requireExactOrigin(accepted)).not.toThrow();

    const rejected = new NextRequest("https://www.coda.eracoda.ai/admin/api/test", {
      headers: { origin: "https://evil.example" },
    });
    expect(() => requireExactOrigin(rejected)).toThrowError(AdminApiError);
  });

  it("trusts only a valid reverse-proxy address", () => {
    expect(requestIp(new Request("https://example.test", { headers: { "x-real-ip": "203.0.113.8" } }))).toBe(
      "203.0.113.8",
    );
    expect(requestIp(new Request("https://example.test", { headers: { "x-real-ip": "not-an-ip" } }))).toBe(
      "0.0.0.0",
    );
  });

  it("rejects non-JSON and oversized bodies", async () => {
    const text = new NextRequest("https://example.test", { method: "POST", body: "hello" });
    await expect(readJsonBody(text)).rejects.toMatchObject({ status: 415 });

    const huge = new NextRequest("https://example.test", {
      method: "POST",
      body: "{}",
      headers: { "content-type": "application/json", "content-length": "20000" },
    });
    await expect(readJsonBody(huge)).rejects.toMatchObject({ status: 413 });
  });
});
