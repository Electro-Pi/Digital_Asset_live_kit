import type { NextRequest } from "next/server";
import { requireAdmin } from "@/lib/auth";
import { listPlans } from "@/lib/data";
import { dataResponse, routeError } from "@/lib/http";
import { requestId } from "@/lib/request";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  const id = requestId(request);
  try {
    const context = await requireAdmin(request);
    return dataResponse(await listPlans(context.tokenHash));
  } catch (error) {
    return routeError(error, id);
  }
}
