import "server-only";
import { createHash, randomBytes } from "node:crypto";
import { getAdminEnv } from "./env";
import { AdminApiError } from "./http";

// This file MUST stay byte-compatible with coda-next's
// src/lib/auth/apiKey.ts and with TTS-Endpoint-api's hash_customer_key().
// All three compute sha256(pepper || raw) over the same key format; a
// divergence anywhere does not fail loudly, it simply makes every key issued
// here read as "invalid" at the gateway, which is a miserable thing to debug.
const PREFIX = "coda_sk_live_";

// Bytes of entropy in the secret half. 24 bytes -> 32 base64url characters.
const SECRET_BYTES = 24;

// How much of the key is kept in the clear, for display. The stored prefix is
// PREFIX plus this many characters.
const VISIBLE_CHARS = 4;

export function generateApiKey(): { raw: string; prefix: string } {
  const raw = PREFIX + randomBytes(SECRET_BYTES).toString("base64url");
  return { raw, prefix: raw.slice(0, PREFIX.length + VISIBLE_CHARS) };
}

/**
 * Digest a key for storage. The pepper is a server-only secret shared with
 * coda-next and the TTS gateway; it is what stops a stolen database dump from
 * being brute-forced offline, since the hashes alone are otherwise a plain
 * sha256 over a known alphabet.
 */
export function hashApiKey(raw: string): string {
  const pepper = getAdminEnv().apiKeyPepper;
  if (!pepper) {
    // A 500 rather than a validation error: the operator did nothing wrong,
    // the deployment is misconfigured. Silently issuing an unusable key would
    // be worse — it would look like the gateway was broken.
    throw new AdminApiError(
      500,
      "pepper_not_configured",
      "API_KEY_PEPPER is not set on this deployment, so keys cannot be issued",
    );
  }
  return createHash("sha256").update(pepper + raw).digest("hex");
}

/** Display form for a stored prefix: coda_sk_live_ab•••••••• */
export function maskApiKey(prefix: string): string {
  return prefix + "•".repeat(8);
}
