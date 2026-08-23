import type { NextRequest } from "next/server";
import { requireAdmin } from "@/lib/auth";
import { updatePlanLimits } from "@/lib/data";
import { dataResponse, routeError } from "@/lib/http";
import { readJsonBody, requestId } from "@/lib/request";
import { planIdSchema, planLimitsSchema } from "@/lib/validation";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function PATCH(
  request: NextRequest,
  context: { params: Promise<{ planId: string }> },
) {
  const id = requestId(request);
  try {
    const auth = await requireAdmin(request, { csrf: true, roles: ["owner", "operator"] });
    const { planId } = await context.params;
    const body = planLimitsSchema.parse(await readJsonBody(request));
    return dataResponse(
      await updatePlanLimits({
        tokenHash: auth.tokenHash,
        csrfHash: auth.csrfHash!,
        planId: planIdSchema.parse(planId),
        ...body,
        requestId: id,
        ip: auth.ip,
      }),
    );
  } catch (error) {
    return routeError(error, id);
  }
}
