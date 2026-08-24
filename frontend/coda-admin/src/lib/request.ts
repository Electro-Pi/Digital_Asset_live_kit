import { isIP } from "node:net";
import type { NextRequest } from "next/server";
import { AdminApiError } from "./http";
import { getAdminEnv } from "./env";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export function requestId(request: Request): string {
  const supplied = request.headers.get("x-request-id")?.trim();
  return supplied && UUID_RE.test(supplied) ? supplied : crypto.randomUUID();
}

export function requestIp(request: Request): string {
  if (!getAdminEnv().trustProxy) return "0.0.0.0";
  const candidate = request.headers.get("x-real-ip")?.trim() ?? "";
  return isIP(candidate) ? candidate : "0.0.0.0";
}

export function requestUserAgent(request: Request): string {
  return (request.headers.get("user-agent") ?? "unknown").slice(0, 500);
}

export function requireExactOrigin(request: NextRequest): void {
  const originHeader = request.headers.get("origin");
  let origin: string;
  try {
    origin = originHeader ? new URL(originHeader).origin : "";
  } catch {
    origin = "";
  }
  if (origin !== getAdminEnv().adminOrigin) {
    throw new AdminApiError(403, "origin_rejected", "The request origin is not allowed");
  }
}

export async function readJsonBody(request: NextRequest, maximumBytes = 16_384): Promise<unknown> {
  const declaredLength = Number(request.headers.get("content-length") ?? "0");
  if (Number.isFinite(declaredLength) && declaredLength > maximumBytes) {
    throw new AdminApiError(413, "payload_too_large", "The request body is too large");
  }
  const contentType = request.headers.get("content-type")?.split(";", 1)[0]?.trim();
  if (contentType !== "application/json") {
    throw new AdminApiError(415, "unsupported_media_type", "A JSON request body is required");
  }
  try {
    const body = await request.text();
    if (Buffer.byteLength(body, "utf8") > maximumBytes) {
      throw new AdminApiError(413, "payload_too_large", "The request body is too large");
    }
    return JSON.parse(body) as unknown;
  } catch (error) {
    if (error instanceof AdminApiError) throw error;
    throw new AdminApiError(400, "invalid_json", "The request body is not valid JSON");
  }
}
