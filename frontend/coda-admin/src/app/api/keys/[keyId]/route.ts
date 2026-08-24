import type { NextRequest } from "next/server";
import { requireAdmin } from "@/lib/auth";
import { revokeApiKey } from "@/lib/data";
import { dataResponse, routeError } from "@/lib/http";
import { requestId } from "@/lib/request";
import { uuidSchema } from "@/lib/validation";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * Revoke a key. The row is kept — only `revoked_at` is stamped — so the audit
 * trail and this key's past usage stay attributable after the fact.
 */
export async function DELETE(
  request: NextRequest,
  context: { params: Promise<{ keyId: string }> },
) {
  const id = requestId(request);
  try {
    const auth = await requireAdmin(request, { csrf: true, roles: ["owner", "operator"] });
    const { keyId } = await context.params;
    return dataResponse(
      await revokeApiKey({
        tokenHash: auth.tokenHash,
        csrfHash: auth.csrfHash!,
        keyId: uuidSchema.parse(keyId),
        requestId: id,
        ip: auth.ip,
      }),
    );
  } catch (error) {
    return routeError(error, id);
  }
}
