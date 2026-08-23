import type { NextRequest } from "next/server";
import { requireAdmin } from "@/lib/auth";
import { listAudit } from "@/lib/data";
import { dataResponse, routeError } from "@/lib/http";
import { requestId } from "@/lib/request";
import { optionalQuery, pageParams, uuidSchema } from "@/lib/validation";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  const id = requestId(request);
  try {
    const context = await requireAdmin(request);
    const query = request.nextUrl.searchParams;
    const actorValue = optionalQuery(query, "actor", 36);
    const actor = actorValue ? uuidSchema.parse(actorValue) : null;
    return dataResponse(
      await listAudit({
        tokenHash: context.tokenHash,
        search: optionalQuery(query, "search"),
        action: optionalQuery(query, "action", 100),
        actor,
        ...pageParams(query),
      }),
    );
  } catch (error) {
    return routeError(error, id);
  }
}
