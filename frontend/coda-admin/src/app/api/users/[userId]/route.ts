import type { NextRequest } from "next/server";
import { requireAdmin } from "@/lib/auth";
import { getUser } from "@/lib/data";
import { dataResponse, routeError } from "@/lib/http";
import { requestId } from "@/lib/request";
import { uuidSchema } from "@/lib/validation";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(
  request: NextRequest,
  context: { params: Promise<{ userId: string }> },
) {
  const id = requestId(request);
  try {
    const auth = await requireAdmin(request);
    const { userId } = await context.params;
    return dataResponse(await getUser(auth.tokenHash, uuidSchema.parse(userId)));
  } catch (error) {
    return routeError(error, id);
  }
}
