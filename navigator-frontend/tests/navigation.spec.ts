import { expect, test, type Page } from '@playwright/test';
import { locations } from '../src/data/mapData';
import {
  NAVIGATION_ROUTE_NARRATION_TOPIC,
  publishNavigationRouteNarration,
  type NavigationNarrationPublishOptions,
} from '../src/lib/livekitNavigation';
import { buildRouteNarration, createRoute } from '../src/lib/navigation';

test.beforeEach(async ({ page }) => {
  await page.route('**/api/leap-map-data', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      title: 'LEAP 2026',
      mapbox_access_token: 'test-token',
      center: { type: 'Point', coordinates: [46.387346, 25.253298] },
      zoom: 16.8,
      pitch: 30,
      bearing: 19.5,
      bg_tileset: '/test-tiles.json',
      booths_geojson: { type: 'FeatureCollection', features: [] },
      permanent_polygons: { type: 'FeatureCollection', features: [] },
      routes_geojson: { type: 'FeatureCollection', features: [] },
      threed_objects: [],
      pois: [],
      pinned_categories: [],
    }),
  }));
  await page.route('**/test-tiles.json', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ tilejson: '3.0.0', tiles: [] }),
  }));
});

async function rejectLiveVoice(page: Page) {
  await page.route('**/api/connection-details', (route) => route.fulfill({
    status: 503,
    contentType: 'application/json',
    body: JSON.stringify({
      error: 'voice_not_configured',
      message: 'Test voice session disabled.',
    }),
  }));
}

test('presents the complete planner catalog beside the live LEAP 3D map', async ({ page }) => {
  await page.goto('/');

  const nativeMap = page.getByTestId('native-leap-map');
  await expect(nativeMap).toBeVisible();
  await expect(page.locator('iframe')).toHaveCount(0);

  const configuredOptions = page.locator('select').first().locator('option:not([value=""])');
  await expect(configuredOptions).toHaveCount(locations.length);
  await page.locator('select').first().selectOption('registration');
  await expect(page.locator('select').first()).toHaveValue('registration');

  await page.getByRole('button', { name: 'ع' }).click();
  await expect(page.locator('.map-panel--native')).toHaveAttribute('aria-label', 'خريطة ليب ثلاثية الأبعاد');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
});

test('shows the complete ordered route without click-through controls', async ({ page }, testInfo) => {
  await rejectLiveVoice(page);
  await page.goto('/');
  await expect(page.locator('h1')).toContainText('Find your way', { timeout: 15_000 });

  const selects = page.locator('select');
  await selects.nth(0).selectOption('registration');
  await selects.nth(1).selectOption('info-hub');
  await page.getByRole('button', { name: 'Show full route & start Coda' }).click();

  const expectedRoute = createRoute('registration', 'info-hub', 'en')!;
  await expect(page.locator('.map-route-ready')).toBeVisible();
  await expect(page.locator('.route-summary')).toContainText('INFO HUB');
  await expect(page.locator('.route-directions')).toContainText('Every direction, in order');
  await expect(page.locator('.route-direction')).toHaveCount(expectedRoute.steps.length, { timeout: 15_000 });
  for (const [index, direction] of expectedRoute.steps.entries()) {
    await expect(page.locator('.route-direction').nth(index)).toContainText(
      direction.instruction,
      { timeout: 15_000 },
    );
  }
  await expect(page.locator('.route-panel button')).toHaveCount(0);
  await expect(page.getByRole('button', { name: /^(next step|previous|repeat|repeat step)$/i })).toHaveCount(0);

  // Chromium's emulated mobile renderer intermittently crashes while capturing
  // this very tall diagnostic image after all assertions have passed. Keep the
  // artifact on desktop; mobile behavior is still fully asserted above.
  if (testInfo.project.name === 'desktop-chromium') {
    await page.screenshot({ path: testInfo.outputPath('route.png'), fullPage: true });
  }
});

test('switches the route planner and guidance to Arabic', async ({ page }) => {
  await rejectLiveVoice(page);
  await page.goto('/');
  await page.getByRole('button', { name: 'ع' }).click();
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');

  const selects = page.locator('select');
  await selects.nth(0).selectOption('registration');
  await selects.nth(1).selectOption('agriculture-food-tech');
  await page.getByRole('button', { name: 'اعرض المسار الكامل وابدأ مع كودا' }).click();

  const expectedRoute = createRoute('registration', 'agriculture-food-tech', 'ar')!;
  await expect(page.locator('.map-route-ready')).toBeVisible();
  await expect(page.locator('.route-summary')).toContainText('الزراعة وتقنية الغذاء');
  await expect(page.locator('.route-directions')).toContainText('كل التعليمات بالترتيب');
  await expect(page.locator('.route-direction')).toHaveCount(expectedRoute.steps.length, { timeout: 15_000 });
  for (const [index, direction] of expectedRoute.steps.entries()) {
    await expect(page.locator('.route-direction').nth(index)).toContainText(
      direction.instruction,
      { timeout: 15_000 },
    );
  }
  await expect(page.locator('.route-panel button')).toHaveCount(0);
});

test('publishes one complete deterministic narration packet reliably and in route order', async () => {
  const route = createRoute('registration', 'leader-pass-investors', 'en')!;
  const narration = buildRouteNarration(route, 'en');

  expect(narration).toEqual({
    version: 1,
    type: 'narrate_navigation_route',
    language: 'en',
    startId: route.startId,
    destinationId: route.destinationId,
    distanceMeters: route.distanceMeters,
    durationMinutes: route.durationMinutes,
    steps: route.steps.map((step) => ({
      id: step.id,
      instruction: step.instruction,
      ...(step.note ? { note: step.note } : {}),
    })),
  });
  expect(new Set(narration.steps.map((step) => step.id)).size).toBe(narration.steps.length);
  expect(new TextEncoder().encode(JSON.stringify(narration)).byteLength).toBeLessThanOrEqual(14_000);

  const publications: Array<{ payload: Uint8Array<ArrayBuffer>; options: NavigationNarrationPublishOptions }> = [];
  await publishNavigationRouteNarration(async (payload, options) => {
    publications.push({ payload, options });
  }, narration);

  expect(publications).toHaveLength(1);
  expect(publications[0].options).toEqual({
    reliable: true,
    topic: NAVIGATION_ROUTE_NARRATION_TOPIC,
  });
  expect(JSON.parse(new TextDecoder().decode(publications[0].payload))).toEqual(narration);
});

test('starts Coda through the LiveKit token route without browser speech APIs', async ({ page }) => {
  await page.addInitScript(() => {
    const instrumentedWindow = window as typeof window & { __browserSpeechCalls: number };
    instrumentedWindow.__browserSpeechCalls = 0;
    Object.defineProperty(window, 'SpeechRecognition', {
      configurable: true,
      value: class {
        constructor() {
          instrumentedWindow.__browserSpeechCalls += 1;
        }
      },
    });
    Object.defineProperty(window, 'webkitSpeechRecognition', {
      configurable: true,
      value: class {
        constructor() {
          instrumentedWindow.__browserSpeechCalls += 1;
        }
      },
    });
    Object.defineProperty(window, 'SpeechSynthesisUtterance', {
      configurable: true,
      value: class {
        constructor() {
          instrumentedWindow.__browserSpeechCalls += 1;
        }
      },
    });
  });

  let requestBody: Record<string, unknown> | undefined;
  await page.route('**/api/connection-details', async (route) => {
    requestBody = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({
      status: 503,
      contentType: 'application/json',
      body: JSON.stringify({
        error: 'test_connection_stopped',
        message: 'Stopped after verifying the LiveKit token request.',
      }),
    });
  });

  await page.goto('/');
  await page.getByRole('button', { name: 'Start Coda conversation' }).click();

  await expect.poll(() => requestBody).toEqual({
    language: 'en',
    voice: 'female',
  });
  const browserSpeechCalls = await page.evaluate(
    () => (window as typeof window & { __browserSpeechCalls: number }).__browserSpeechCalls,
  );
  expect(browserSpeechCalls).toBe(0);
  await expect(page.locator('.voice-copy')).toContainText('Coda could not connect');
});
