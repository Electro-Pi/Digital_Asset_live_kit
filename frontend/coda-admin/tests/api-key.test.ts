import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { createHash } from "node:crypto";
import { resetAdminEnvForTests } from "@/lib/env";
import { apiKeyCreateSchema } from "@/lib/validation";

const ENV = {
  SUPABASE_URL: "https://example.supabase.co",
  SUPABASE_ANON_KEY: "anon-key-that-is-long-enough",
  SUPABASE_SERVICE_ROLE_KEY: "service-key-that-is-long-enough",
  ADMIN_SESSION_HMAC_SECRET: "c".repeat(64),
  ADMIN_ORIGIN: "https://www.coda.eracoda.ai",
  ADMIN_TRUST_PROXY: "true",
  API_KEY_PEPPER: "p".repeat(32),
};

describe("api key generation", () => {
  beforeEach(() => {
    resetAdminEnvForTests();
    Object.assign(process.env, ENV);
  });

  afterEach(() => {
    resetAdminEnvForTests();
    delete process.env.API_KEY_PEPPER;
  });

  it("mints a key with the shared coda_sk_live_ format", async () => {
    const { generateApiKey } = await import("@/lib/api-key");
    const { raw, prefix } = generateApiKey();

    expect(raw.startsWith("coda_sk_live_")).toBe(true);
    // 24 random bytes -> 32 base64url characters, with no padding.
    expect(raw).toHaveLength("coda_sk_live_".length + 32);
    expect(raw).toMatch(/^coda_sk_live_[A-Za-z0-9_-]{32}$/);
    // The stored prefix keeps four characters of the secret for display.
    expect(prefix).toBe(raw.slice(0, "coda_sk_live_".length + 4));
  });

  it("does not repeat itself", async () => {
    const { generateApiKey } = await import("@/lib/api-key");
    const keys = new Set(Array.from({ length: 200 }, () => generateApiKey().raw));
    expect(keys.size).toBe(200);
  });

  // The gateway recomputes this digest independently. If the two ever diverge,
  // every key issued here silently reads as invalid at the gateway, so pin the
  // exact construction rather than just "some hash".
  it("hashes as sha256(pepper || raw), matching coda-next and the TTS gateway", async () => {
    const { hashApiKey } = await import("@/lib/api-key");
    const raw = "coda_sk_live_abcdefghijklmnopqrstuvwxyz012345";

    expect(hashApiKey(raw)).toBe(
      createHash("sha256").update(ENV.API_KEY_PEPPER + raw).digest("hex"),
    );
    expect(hashApiKey(raw)).toMatch(/^[0-9a-f]{64}$/);
  });

  it("refuses to issue a key when the pepper is missing, rather than issuing a dead one", async () => {
    resetAdminEnvForTests();
    const { API_KEY_PEPPER: _omitted, ...withoutPepper } = ENV;
    for (const key of Object.keys(ENV)) delete process.env[key];
    Object.assign(process.env, withoutPepper);

    const { hashApiKey } = await import("@/lib/api-key");
    expect(() => hashApiKey("coda_sk_live_whatever")).toThrowError(/API_KEY_PEPPER/);
  });

  it("masks a prefix without revealing the secret", async () => {
    const { maskApiKey } = await import("@/lib/api-key");
    expect(maskApiKey("coda_sk_live_ab12")).toBe("coda_sk_live_ab12••••••••");
  });
});

describe("api key request validation", () => {
  const valid = {
    email: "Partner@Example.com",
    label: "  Partner integration  ",
    scopes: ["agent", "agent", "tts"],
    maxConcurrent: 4,
    expiresAt: null,
  };

  it("normalises email and label, and de-duplicates scopes", () => {
    expect(apiKeyCreateSchema.parse(valid)).toEqual({
      email: "partner@example.com",
      label: "Partner integration",
      scopes: ["agent", "tts"],
      maxConcurrent: 4,
      expiresAt: null,
    });
  });

  it("defaults to a single concurrent session and no expiry", () => {
    const parsed = apiKeyCreateSchema.parse({
      email: "a@b.com",
      label: "x",
      scopes: ["agent"],
    });
    expect(parsed.maxConcurrent).toBe(1);
    expect(parsed.expiresAt).toBeNull();
  });

  it.each([
    ["an unknown scope", { scopes: ["admin"] }],
    ["no scopes at all", { scopes: [] }],
    ["a concurrency above the database ceiling", { maxConcurrent: 101 }],
    ["a concurrency below one", { maxConcurrent: 0 }],
    ["a fractional concurrency", { maxConcurrent: 1.5 }],
    ["a malformed email", { email: "not-an-email" }],
    ["an empty label", { label: "   " }],
    ["a label past 60 characters", { label: "x".repeat(61) }],
    ["an unknown field", { somethingElse: true }],
  ])("rejects %s", (_name, patch) => {
    expect(() => apiKeyCreateSchema.parse({ ...valid, ...patch })).toThrowError();
  });
});
