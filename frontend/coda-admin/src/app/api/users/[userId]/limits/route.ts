import type { NextRequest } from "next/server";
import { requireAdmin } from "@/lib/auth";
import { updateUserLimits } from "@/lib/data";
import { dataResponse, routeError } from "@/lib/http";
import { readJsonBody, requestId } from "@/lib/request";
import { userLimitsSchema, uuidSchema } from "@/lib/validation";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function PATCH(
  request: NextRequest,
  context: { params: Promise<{ userId: string }> },
) {
  const id = requestId(request);
  try {
    const auth = await requireAdmin(request, { csrf: true, roles: ["owner", "operator"] });
    const { userId } = await context.params;
    const body = userLimitsSchema.parse(await readJsonBody(request));
    return dataResponse(
      await updateUserLimits({
        tokenHash: auth.tokenHash,
        csrfHash: auth.csrfHash!,
        userId: uuidSchema.parse(userId),
        ...body,
        requestId: id,
        ip: auth.ip,
      }),
    );
  } catch (error) {
    return routeError(error, id);
  }
}
