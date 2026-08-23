import { expect, test } from "@playwright/test";

// Paths here are written as the browser sees them, mount included, because
// these run against a real server and are the only check that the built app
// actually answers where nginx forwards it.
test("unauthenticated dashboard visitors are redirected to login", async ({ page }) => {
  // The mount root is below the proxy matcher, which needs a path segment after
  // the prefix; the layout's own session check catches it and redirects with no
  // `next`, which LoginForm reads as "/".
  await page.goto("/admin");
  await expect(page).toHaveURL(/\/admin\/login$/);
});

test("a deep link survives the trip through login", async ({ page }) => {
  await page.goto("/admin/users?plan=pro");
  // Mount-relative: router.replace() adds the /admin back after signing in.
  await expect(page).toHaveURL(/\/admin\/login\?next=%2Fusers%3Fplan%3Dpro$/);
});

test("the dashboard does not answer at the origin root", async ({ request }) => {
  const response = await request.get("/api/health", { maxRedirects: 0 });
  expect(response.status()).toBe(404);
});

test("health endpoint returns no environment or database details", async ({ request }) => {
  const response = await request.get("/admin/api/health");
  expect(response.ok()).toBe(true);
  expect(await response.json()).toEqual({ data: { status: "ok" } });
  expect(response.headers()["cache-control"]).toContain("no-store");
});

test("dashboard responses carry their own security headers", async ({ request }) => {
  // Nginx adds none on this path, so a regression here would ship the dashboard
  // with the public site's (absent) header policy.
  const response = await request.get("/admin/login");
  const headers = response.headers();
  expect(headers["content-security-policy"]).toContain("frame-ancestors 'none'");
  expect(headers["x-frame-options"]).toBe("DENY");
  expect(headers["x-robots-tag"]).toContain("noindex");
});
