import type { NextRequest } from "next/server";
import { requireAdmin } from "@/lib/auth";
import { listPodcastSeats } from "@/lib/data";
import { dataResponse, routeError } from "@/lib/http";
import { requestId } from "@/lib/request";
import { PODCAST_SEAT_STATUSES, enumQuery, optionalQuery, pageParams } from "@/lib/validation";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  const id = requestId(request);
  try {
    const context = await requireAdmin(request);
    const query = request.nextUrl.searchParams;
    return dataResponse(
      await listPodcastSeats({
        tokenHash: context.tokenHash,
        search: optionalQuery(query, "search"),
        status: enumQuery(query, "status", PODCAST_SEAT_STATUSES),
        day: optionalQuery(query, "day", 24),
        ...pageParams(query),
      }),
    );
  } catch (error) {
    return routeError(error, id);
  }
}
