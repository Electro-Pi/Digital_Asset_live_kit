import type { NextRequest } from "next/server";
import { requireAdmin } from "@/lib/auth";
import { grantCredits } from "@/lib/data";
import { dataResponse, routeError } from "@/lib/http";
import { readJsonBody, requestId } from "@/lib/request";
import { creditGrantSchema, uuidSchema } from "@/lib/validation";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * Add credits to a user's balance.
 *
 * POST rather than PATCH because the body is a delta, not a new state: sending
 * it twice grants twice, which is why the RPC is idempotent on `ref`. Owners
 * and operators only — this spends real money, so viewers are excluded exactly
 * as they are from key issuance.
 */
export async function POST(
  request: NextRequest,
  context: { params: Promise<{ userId: string }> },
) {
  const id = requestId(request);
  try {
    const auth = await requireAdmin(request, { csrf: true, roles: ["owner", "operator"] });
    const { userId } = await context.params;
    const body = creditGrantSchema.parse(await readJsonBody(request));
    return dataResponse(
      await grantCredits({
        tokenHash: auth.tokenHash,
        csrfHash: auth.csrfHash!,
        userId: uuidSchema.parse(userId),
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
