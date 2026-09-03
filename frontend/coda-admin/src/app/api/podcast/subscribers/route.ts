import type { NextRequest } from "next/server";
import { requireAdmin } from "@/lib/auth";
import { listPodcastSubscribers } from "@/lib/data";
import { dataResponse, routeError } from "@/lib/http";
import { requestId } from "@/lib/request";
import { enumQuery, optionalQuery, pageParams } from "@/lib/validation";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  const id = requestId(request);
  try {
    const context = await requireAdmin(request);
    const query = request.nextUrl.searchParams;
    return dataResponse(
      await listPodcastSubscribers({
        tokenHash: context.tokenHash,
        search: optionalQuery(query, "search"),
        status: enumQuery(query, "status", ["active", "unsubscribed", "bounced"] as const),
        ...pageParams(query),
      }),
    );
  } catch (error) {
    return routeError(error, id);
  }
}
