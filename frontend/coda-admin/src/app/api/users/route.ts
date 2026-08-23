import type { NextRequest } from "next/server";
import { requireAdmin } from "@/lib/auth";
import { listUsers } from "@/lib/data";
import { dataResponse, routeError } from "@/lib/http";
import { requestId } from "@/lib/request";
import { enumQuery, optionalQuery, pageParams } from "@/lib/validation";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const USER_SORTS: Record<string, string> = {
  createdAt: "created_at",
  created_at: "created_at",
  lastSignInAt: "last_sign_in_at",
  last_sign_in_at: "last_sign_in_at",
  lastActiveAt: "last_active_at",
  last_active_at: "last_active_at",
  email: "email",
  name: "name",
  plan: "plan",
  usage: "usage",
};

export async function GET(request: NextRequest) {
  const id = requestId(request);
  try {
    const context = await requireAdmin(request);
    const query = request.nextUrl.searchParams;
    const pagination = pageParams(query);
    const requestedSort = optionalQuery(query, "sort", 40) ?? "created_at";
    return dataResponse(
      await listUsers({
        tokenHash: context.tokenHash,
        search: optionalQuery(query, "search"),
        plan: optionalQuery(query, "plan", 64),
        status: optionalQuery(query, "status", 64),
        sort: USER_SORTS[requestedSort] ?? "created_at",
        direction:
          enumQuery(query, "order", ["asc", "desc"] as const, null) ??
          enumQuery(query, "direction", ["asc", "desc"] as const, "desc") ??
          "desc",
        ...pagination,
      }),
    );
  } catch (error) {
    return routeError(error, id);
  }
}
