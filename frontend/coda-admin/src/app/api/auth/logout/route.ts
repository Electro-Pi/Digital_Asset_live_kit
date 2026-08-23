import type { NextRequest } from "next/server";
import { clearAuthCookies, requireAdmin, requireValidCsrf, revokeAdminSession } from "@/lib/auth";
import { dataResponse, routeError } from "@/lib/http";
import { requestId } from "@/lib/request";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request: NextRequest) {
  const id = requestId(request);
  try {
    // A cross-origin request can never force a logout. Once the exact Origin
    // and double-submit CSRF token are established, logout is intentionally
    // idempotent: clear browser credentials even if the DB session expired or
    // revocation is temporarily unavailable.
    requireValidCsrf(request);
    try {
      const context = await requireAdmin(request);
      await revokeAdminSession(context.tokenHash);
    } catch {
      // Best effort. The opaque cookie is still removed below.
    }
    const response = dataResponse({ ok: true });
    clearAuthCookies(response);
    return response;
  } catch (error) {
    return routeError(error, id);
  }
}
