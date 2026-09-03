import type { NextRequest } from "next/server";
import { requireAdmin } from "@/lib/auth";
import { listPodcastEpisodes, savePodcastEpisode } from "@/lib/data";
import { dataResponse, routeError } from "@/lib/http";
import { readJsonBody, requestId } from "@/lib/request";
import { enumQuery, optionalQuery, pageParams, podcastEpisodeSchema } from "@/lib/validation";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  const id = requestId(request);
  try {
    const context = await requireAdmin(request);
    const query = request.nextUrl.searchParams;
    return dataResponse(
      await listPodcastEpisodes({
        tokenHash: context.tokenHash,
        search: optionalQuery(query, "search"),
        // 'live' and 'upcoming' split the scheduled ones by whether their
        // instant has passed; the RPC derives both from publish_at.
        status: enumQuery(
          query,
          "status",
          ["draft", "scheduled", "archived", "live", "upcoming"] as const,
        ),
        ...pageParams(query),
      }),
    );
  } catch (error) {
    return routeError(error, id);
  }
}

export async function POST(request: NextRequest) {
  const id = requestId(request);
  try {
    const auth = await requireAdmin(request, { csrf: true, roles: ["owner", "operator"] });
    const episode = podcastEpisodeSchema.parse(await readJsonBody(request));
    return dataResponse(
      await savePodcastEpisode({
        tokenHash: auth.tokenHash,
        csrfHash: auth.csrfHash!,
        episodeId: null,
        episode,
        requestId: id,
        ip: auth.ip,
      }),
      { status: 201 },
    );
  } catch (error) {
    return routeError(error, id);
  }
}
