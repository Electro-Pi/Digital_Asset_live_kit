import type { NextRequest } from "next/server";
import { requireAdmin } from "@/lib/auth";
import { deletePodcastEpisode, savePodcastEpisode } from "@/lib/data";
import { dataResponse, routeError } from "@/lib/http";
import { readJsonBody, requestId } from "@/lib/request";
import { podcastEpisodeSchema, uuidSchema } from "@/lib/validation";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function PATCH(
  request: NextRequest,
  context: { params: Promise<{ episodeId: string }> },
) {
  const id = requestId(request);
  try {
    const auth = await requireAdmin(request, { csrf: true, roles: ["owner", "operator"] });
    const { episodeId } = await context.params;
    const episode = podcastEpisodeSchema.parse(await readJsonBody(request));
    return dataResponse(
      await savePodcastEpisode({
        tokenHash: auth.tokenHash,
        csrfHash: auth.csrfHash!,
        episodeId: uuidSchema.parse(episodeId),
        episode,
        requestId: id,
        ip: auth.ip,
      }),
    );
  } catch (error) {
    return routeError(error, id);
  }
}

export async function DELETE(
  request: NextRequest,
  context: { params: Promise<{ episodeId: string }> },
) {
  const id = requestId(request);
  try {
    const auth = await requireAdmin(request, { csrf: true, roles: ["owner", "operator"] });
    const { episodeId } = await context.params;
    return dataResponse(
      await deletePodcastEpisode({
        tokenHash: auth.tokenHash,
        csrfHash: auth.csrfHash!,
        episodeId: uuidSchema.parse(episodeId),
        requestId: id,
        ip: auth.ip,
      }),
    );
  } catch (error) {
    return routeError(error, id);
  }
}
