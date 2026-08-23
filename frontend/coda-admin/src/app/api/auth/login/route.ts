import type { NextRequest } from "next/server";
import { loginAdmin, setAuthCookies } from "@/lib/auth";
import { dataResponse, routeError } from "@/lib/http";
import { readJsonBody, requestId } from "@/lib/request";
import { loginSchema } from "@/lib/validation";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request: NextRequest) {
  const id = requestId(request);
  try {
    const body = loginSchema.parse(await readJsonBody(request));
    const result = await loginAdmin(body.username, body.password, request);
    const response = dataResponse({ admin: result.admin });
    setAuthCookies(response, result.token, result.csrfToken);
    return response;
  } catch (error) {
    return routeError(error, id);
  }
}
