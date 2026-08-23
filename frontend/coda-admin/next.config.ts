import type { NextConfig } from "next";
import { BASE_PATH } from "./src/lib/base-path";

// The dashboard shares an origin with the public site, so it can no longer rely
// on a vhost of its own to attach these. They are set here instead, which keeps
// one copy of them and applies them to every response Next serves, including the
// login page and the `_next` assets. Nginx deliberately adds no security headers
// of its own on /admin: a second copy of any of these would either be merged
// (two CSPs are intersected) or ignored (a repeated X-Frame-Options).
const securityHeaders = [
  {
    key: "Content-Security-Policy",
    value:
      "default-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'; object-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; upgrade-insecure-requests",
  },
  { key: "Referrer-Policy", value: "no-referrer" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), payment=(), usb=()" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "X-Robots-Tag", value: "noindex, nofollow, noarchive" },
  { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
  { key: "Cross-Origin-Resource-Policy", value: "same-origin" },
  // Host-scoped on purpose. `includeSubDomains` here would have committed every
  // name under coda.eracoda.ai to HTTPS, which is not this dashboard's call to
  // make now that it answers on the public site's own hostname.
  { key: "Strict-Transport-Security", value: "max-age=31536000" },
] as const;

const nextConfig: NextConfig = {
  // Mounts the whole app — pages, API routes and assets — under /admin. Next
  // prefixes `source` below with it too, so this stays "/:path*".
  basePath: BASE_PATH,
  poweredByHeader: false,
  productionBrowserSourceMaps: false,
  reactStrictMode: true,
  async headers() {
    return [{ source: "/:path*", headers: [...securityHeaders] }];
  },
};

export default nextConfig;
