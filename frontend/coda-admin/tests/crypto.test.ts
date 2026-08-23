import { describe, expect, it } from "vitest";
import { constantTimeEqual, hmacToken, randomOpaqueToken } from "@/lib/crypto";

describe("opaque admin credentials", () => {
  it("generates 32-byte URL-safe tokens", () => {
    const token = randomOpaqueToken();
    expect(token).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(randomOpaqueToken()).not.toBe(token);
  });

  it("hashes tokens deterministically without retaining the token", () => {
    const digest = hmacToken("opaque-token", "a".repeat(64));
    expect(digest).toMatch(/^[0-9a-f]{64}$/);
    expect(digest).toBe(hmacToken("opaque-token", "a".repeat(64)));
    expect(digest).not.toContain("opaque-token");
  });

  it("compares CSRF values safely", () => {
    expect(constantTimeEqual("same", "same")).toBe(true);
    expect(constantTimeEqual("same", "different")).toBe(false);
    expect(constantTimeEqual("short", "much-longer")).toBe(false);
  });
});
