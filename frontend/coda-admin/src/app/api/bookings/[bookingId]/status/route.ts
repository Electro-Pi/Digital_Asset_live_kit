import type { NextRequest } from "next/server";
import { requireAdmin } from "@/lib/auth";
import { updateBookingStatus } from "@/lib/data";
import { dataResponse, routeError } from "@/lib/http";
import { readJsonBody, requestId } from "@/lib/request";
import { bookingStatusSchema, uuidSchema } from "@/lib/validation";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function PATCH(
  request: NextRequest,
  context: { params: Promise<{ bookingId: string }> },
) {
  const id = requestId(request);
  try {
    const auth = await requireAdmin(request, { csrf: true, roles: ["owner", "operator"] });
    const { bookingId } = await context.params;
    const body = bookingStatusSchema.parse(await readJsonBody(request));
    return dataResponse(
      await updateBookingStatus({
        tokenHash: auth.tokenHash,
        csrfHash: auth.csrfHash!,
        bookingId: uuidSchema.parse(bookingId),
        status: body.status,
        requestId: id,
        ip: auth.ip,
      }),
    );
  } catch (error) {
    return routeError(error, id);
  }
}
