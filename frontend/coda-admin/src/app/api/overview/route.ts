import type { NextRequest } from "next/server";
import { requireAdmin } from "@/lib/auth";
import { getOverview } from "@/lib/data";
import { dataResponse, routeError } from "@/lib/http";
import { requestId } from "@/lib/request";
import { rangeSchema } from "@/lib/validation";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  const id = requestId(request);
  try {
    const context = await requireAdmin(request);
    const range = rangeSchema.parse(request.nextUrl.searchParams.get("range") ?? "month");
    return dataResponse(await getOverview(context.tokenHash, range));
  } catch (error) {
    return routeError(error, id);
  }
}
