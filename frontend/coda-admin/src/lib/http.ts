import { NextResponse } from "next/server";
import { ZodError } from "zod";
import { AdminDatabaseError } from "./supabase";

export class AdminApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
    public readonly retryAfter?: number,
  ) {
    super(message);
    this.name = "AdminApiError";
  }
}

const NO_STORE_HEADERS = {
  "Cache-Control": "private, no-store, max-age=0, must-revalidate",
  Pragma: "no-cache",
  "X-Content-Type-Options": "nosniff",
} as const;

export function dataResponse<T>(data: T, init?: ResponseInit): NextResponse {
  const response = NextResponse.json({ data }, init);
  for (const [key, value] of Object.entries(NO_STORE_HEADERS)) response.headers.set(key, value);
  return response;
}

export function errorResponse(
  status: number,
  code: string,
  message: string,
  options?: { retryAfter?: number; details?: unknown },
): NextResponse {
  const response = NextResponse.json(
    { error: { code, message, ...(options?.details === undefined ? {} : { details: options.details }) } },
    { status },
  );
  for (const [key, value] of Object.entries(NO_STORE_HEADERS)) response.headers.set(key, value);
  if (options?.retryAfter) response.headers.set("Retry-After", String(options.retryAfter));
  return response;
}

export function routeError(error: unknown, requestId?: string): NextResponse {
  if (error instanceof AdminApiError) {
    return errorResponse(error.status, error.code, error.message, { retryAfter: error.retryAfter });
  }

  if (error instanceof ZodError) {
    return errorResponse(400, "validation_failed", "The request contains invalid values", {
      details: error.issues.map((issue) => ({ path: issue.path.join("."), message: issue.message })),
    });
  }

  if (error instanceof AdminDatabaseError) {
    if (error.databaseCode === "28000") {
      return errorResponse(401, "invalid_session", "The session is no longer valid");
    }
    if (error.databaseCode === "42501") {
      return errorResponse(403, "admin_access_denied", "Administrative access is denied");
    }
    if (error.databaseCode === "22023") {
      return errorResponse(400, "invalid_request", "The request contains invalid values");
    }
    if (error.databaseCode === "P0002") {
      return errorResponse(404, "not_found", "The requested record was not found");
    }
  }

  console.error("[coda-admin] request failed", { requestId, error });
  return errorResponse(500, "internal_error", "The request could not be completed");
}
