import { describe, expect, it } from "vitest";
import { NextRequest } from "next/server";
import nextConfig from "../next.config";
import { BASE_PATH, adminPath, stripBasePath } from "@/lib/base-path";
import { proxy } from "@/proxy";

const NEXT_CONFIG_INIT = { nextConfig: { basePath: BASE_PATH } };

describe("path mount", () => {
  it("mounts the built application on the same prefix the client code assumes", () => {
    // A mismatch here is invisible in development and fatal in production: the
    // server would answer on one prefix while every fetch went to the other.
    expect(nextConfig.basePath).toBe(BASE_PATH);
  });

  it("maps in-app paths onto the mount without doubling it", () => {
    expect(adminPath("/")).toBe("/admin");
    expect(adminPath("/api/users?page=2")).toBe("/admin/api/users?page=2");
    expect(stripBasePath("/admin")).toBe("/");
    expect(stripBasePath("/admin/users")).toBe("/users");
    expect(stripBasePath(adminPath("/audit"))).toBe("/audit");
  });

  it("leaves paths outside the mount alone", () => {
    // The public site owns these; silently rewriting one would turn a link to
    // somebody else's page into a link into the dashboard.
    expect(stripBasePath("/pricing")).toBe("/pricing");
    expect(stripBasePath("/administration")).toBe("/administration");
  });
});

describe("unauthenticated proxy redirect", () => {
  it("keeps the mount and records a mount-relative return path", () => {
    const request = new NextRequest(
      "https://www.coda.eracoda.ai/admin/users?plan=pro",
      NEXT_CONFIG_INIT,
    );
    const response = proxy(request);
    const location = new URL(response.headers.get("location") ?? "");

    expect(response.status).toBe(307);
    expect(location.pathname).toBe("/admin/login");
    // Mount-relative, because LoginForm hands it to router.replace(), which
    // adds the prefix back on.
    expect(location.searchParams.get("next")).toBe("/users?plan=pro");
  });

  it("does not redirect the login page or a request that carries a session", () => {
    const login = proxy(new NextRequest("https://www.coda.eracoda.ai/admin/login", NEXT_CONFIG_INIT));
    expect(login.headers.get("location")).toBeNull();

    const authenticated = proxy(
      new NextRequest("https://www.coda.eracoda.ai/admin/audit", {
        ...NEXT_CONFIG_INIT,
        headers: { cookie: `__Secure-coda-admin-session=${"t".repeat(43)}` },
      }),
    );
    expect(authenticated.headers.get("location")).toBeNull();
    expect(authenticated.headers.get("cache-control")).toContain("no-store");
  });
});
