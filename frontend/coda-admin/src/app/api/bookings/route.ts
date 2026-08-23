import type { NextRequest } from "next/server";
import { requireAdmin } from "@/lib/auth";
import { listBookings } from "@/lib/data";
import { dataResponse, routeError } from "@/lib/http";
import { requestId } from "@/lib/request";
import { enumQuery, optionalQuery, pageParams } from "@/lib/validation";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const BOOKING_SORTS: Record<string, string> = {
  createdAt: "created_at",
  created_at: "created_at",
  updatedAt: "updated_at",
  updated_at: "updated_at",
  name: "name",
  email: "email",
  status: "status",
};

export async function GET(request: NextRequest) {
  const id = requestId(request);
  try {
    const context = await requireAdmin(request);
    const query = request.nextUrl.searchParams;
    const pagination = pageParams(query);
    const requestedSort = optionalQuery(query, "sort", 40) ?? "created_at";
    return dataResponse(
      await listBookings({
        tokenHash: context.tokenHash,
        search: optionalQuery(query, "search"),
        status: optionalQuery(query, "status", 64),
        source: optionalQuery(query, "source", 64),
        sort: BOOKING_SORTS[requestedSort] ?? "created_at",
        direction: enumQuery(query, "direction", ["asc", "desc"] as const, "desc") ?? "desc",
        ...pagination,
      }),
    );
  } catch (error) {
    return routeError(error, id);
  }
}
