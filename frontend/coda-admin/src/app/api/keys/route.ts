import type { NextRequest } from "next/server";
import { requireAdmin } from "@/lib/auth";
import { createApiKey, listApiKeys } from "@/lib/data";
import { dataResponse, routeError } from "@/lib/http";
import { readJsonBody, requestId } from "@/lib/request";
import {
  API_KEY_SCOPES,
  API_KEY_STATUSES,
  apiKeyCreateSchema,
  enumQuery,
  optionalQuery,
  pageParams,
} from "@/lib/validation";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/** Reads a date filter, ignoring anything unparseable rather than failing the page. */
function instantQuery(query: URLSearchParams, key: string): string | null {
  const raw = optionalQuery(query, key, 40);
  if (!raw) return null;
  const parsed = new Date(raw);
  return Number.isNaN(parsed.getTime()) ? null : parsed.toISOString();
}

export async function GET(request: NextRequest) {
  const id = requestId(request);
  try {
    // Listing is a read, so viewers see it; the RPC enforces the same set.
    const context = await requireAdmin(request);
    const query = request.nextUrl.searchParams;
    const pagination = pageParams(query);
    return dataResponse(
      await listApiKeys({
        tokenHash: context.tokenHash,
        search: optionalQuery(query, "search"),
        scope: enumQuery(query, "scope", API_KEY_SCOPES, null),
        status: enumQuery(query, "status", API_KEY_STATUSES, null),
        from: instantQuery(query, "from"),
        to: instantQuery(query, "to"),
        ...pagination,
      }),
    );
  } catch (error) {
    return routeError(error, id);
  }
}

/**
 * Issue a key. The response carries the raw value, and it is the only time it
 * ever exists outside the operator's clipboard — GET never returns it and the
 * database holds only its peppered digest.
 */
export async function POST(request: NextRequest) {
  const id = requestId(request);
  try {
    const auth = await requireAdmin(request, { csrf: true, roles: ["owner", "operator"] });
    const body = apiKeyCreateSchema.parse(await readJsonBody(request));
    return dataResponse(
      await createApiKey({
        tokenHash: auth.tokenHash,
        csrfHash: auth.csrfHash!,
        ...body,
        requestId: id,
        ip: auth.ip,
      }),
      { status: 201 },
    );
  } catch (error) {
    return routeError(error, id);
  }
}
