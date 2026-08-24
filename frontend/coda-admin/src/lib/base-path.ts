/**
 * The dashboard is mounted on a path of the public origin rather than on its
 * own subdomain, so every URL it emits has to carry this prefix.
 *
 * Next applies it automatically to `<Link>`, `useRouter()`, `redirect()`,
 * `headers()` sources, the proxy matcher, and the `_next` asset URLs. It does
 * NOT apply to raw `fetch()`, `window.location`, or cookie paths, because those
 * never pass through the router — those call `adminPath()` below.
 *
 * Nothing here is secret, so the literal is safe to inline into the client
 * bundle. `next.config.ts` imports the same constant, which is what keeps the
 * server mount and the client-side prefixing from drifting apart.
 */
export const BASE_PATH = "/admin";

/** Absolute in-app path → absolute browser path. `/` maps to the mount itself. */
export function adminPath(path: string): string {
  return path === "/" ? BASE_PATH : `${BASE_PATH}${path}`;
}

/**
 * Browser path → in-app path, i.e. the inverse of `adminPath`.
 *
 * The `next=` parameter is always stored basePath-relative so that whatever
 * consumes it — `router.replace()`, which re-adds the prefix — cannot end up
 * doubling it. Paths outside the mount are returned unchanged; the caller is
 * responsible for rejecting anything it did not expect.
 */
export function stripBasePath(path: string): string {
  if (path === BASE_PATH) return "/";
  return path.startsWith(`${BASE_PATH}/`) ? path.slice(BASE_PATH.length) : path;
}
