import type { NextRequest } from "next/server";
import { requireAdmin } from "@/lib/auth";
import { updatePodcastSeatStatus } from "@/lib/data";
import { dataResponse, routeError } from "@/lib/http";
import { readJsonBody, requestId } from "@/lib/request";
import { podcastSeatStatusSchema, uuidSchema } from "@/lib/validation";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function PATCH(
  request: NextRequest,
  context: { params: Promise<{ seatId: string }> },
) {
  const id = requestId(request);
  try {
    const auth = await requireAdmin(request, { csrf: true, roles: ["owner", "operator"] });
    const { seatId } = await context.params;
    const body = podcastSeatStatusSchema.parse(await readJsonBody(request));
    return dataResponse(
      await updatePodcastSeatStatus({
        tokenHash: auth.tokenHash,
        csrfHash: auth.csrfHash!,
        seatId: uuidSchema.parse(seatId),
        status: body.status,
        requestId: id,
        ip: auth.ip,
      }),
    );
  } catch (error) {
    return routeError(error, id);
  }
}
