// POST /api/podcast/upload-url — mint a one-shot URL the browser uploads to.
//
// The file itself never passes through this app. Only an authenticated operator
// can ask for a URL, it names exactly one object path, and it expires by itself,
// so possessing one grants nothing beyond writing that single object.
import type { NextRequest } from "next/server";
import { requireAdmin } from "@/lib/auth";
import { createPodcastUploadUrl } from "@/lib/data";
import { dataResponse, routeError } from "@/lib/http";
import { readJsonBody, requestId } from "@/lib/request";
import { podcastUploadSchema } from "@/lib/validation";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request: NextRequest) {
  const id = requestId(request);
  try {
    await requireAdmin(request, { csrf: true, roles: ["owner", "operator"] });
    const body = podcastUploadSchema.parse(await readJsonBody(request));
    return dataResponse(await createPodcastUploadUrl(body));
  } catch (error) {
    return routeError(error, id);
  }
}
