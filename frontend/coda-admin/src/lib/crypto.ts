import { createHmac, randomBytes, timingSafeEqual } from "node:crypto";

export function randomOpaqueToken(): string {
  return randomBytes(32).toString("base64url");
}

export function hmacToken(value: string, secret: string): string {
  return createHmac("sha256", secret).update(value, "utf8").digest("hex");
}

export function constantTimeEqual(left: string, right: string): boolean {
  const leftBytes = Buffer.from(left, "utf8");
  const rightBytes = Buffer.from(right, "utf8");
  return leftBytes.length === rightBytes.length && timingSafeEqual(leftBytes, rightBytes);
}
