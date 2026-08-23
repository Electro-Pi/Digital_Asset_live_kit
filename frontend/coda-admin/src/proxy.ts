import { NextRequest, NextResponse } from "next/server";

const SESSION_COOKIE =
  process.env.ADMIN_ORIGIN?.startsWith("https://") === false
    ? "coda-admin-session"
    : "__Secure-coda-admin-session";

export function proxy(request: NextRequest): NextResponse {
  // `nextUrl.pathname` has the basePath stripped, so these comparisons stay
  // written in in-app terms; `nextUrl.clone()` remembers the prefix and puts it
  // back when the redirect URL is serialised. Building the URL from
  // `request.url` instead would drop the mount and send the browser to the
  // public site's /login.
  const { pathname } = request.nextUrl;
  if (pathname === "/login" || pathname.startsWith("/login/")) return NextResponse.next();

  // This is intentionally just a fast redirect. Protected API handlers and
  // database RPCs independently authenticate the opaque session on every call.
  if (!request.cookies.get(SESSION_COOKIE)?.value) {
    const login = request.nextUrl.clone();
    const requested = `${pathname}${request.nextUrl.search}`;
    login.pathname = "/login";
    login.search = "";
    login.searchParams.set("next", requested);
    return NextResponse.redirect(login);
  }

  const response = NextResponse.next();
  response.headers.set("Cache-Control", "private, no-store, max-age=0, must-revalidate");
  return response;
}

// Next prefixes each source with the basePath at build time, so these are
// written without it.
export const config = {
  matcher: ["/((?!api|_next/static|_next/image|favicon.ico|icon.png|apple-icon.png).*)"],
};
