import { expect, test } from '@playwright/test';
import {
  edges,
  locationDisplayName,
  locations,
  nodeById,
  nodes,
  PDF_ASSIGNED_BOOTH_COUNT,
  PDF_DESTINATION_COUNT,
  PDF_PUBLIC_PLACE_COUNT,
} from '../src/data/mapData';
import pdfPlaceCatalog from '../src/data/pdfPlaces.json' with { type: 'json' };
import { createRoute } from '../src/lib/navigation';

const EXPECTED_ASSIGNED_BOOTH_COUNT = 842;
const EXPECTED_PUBLIC_PLACE_COUNT = 233;
const EXPECTED_DESTINATION_COUNT = 1_075;
const publicPlaceIds = new Set(pdfPlaceCatalog.publicPlaces.map((place) => place.id));

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

test.describe('configured map destinations', () => {
  test('includes every assigned destination extracted from the PDF text layer', () => {
    const pdfBooths = locations.filter((location) => location.boothCode);
    expect(PDF_ASSIGNED_BOOTH_COUNT).toBe(EXPECTED_ASSIGNED_BOOTH_COUNT);
    expect(pdfBooths).toHaveLength(PDF_ASSIGNED_BOOTH_COUNT);
    expect(pdfBooths[0].boothCode).toBe('H3.H150');
    expect(pdfBooths[0].aliases).toContain('RedFrog');
    expect(pdfBooths.at(-1)!.boothCode).toBe('H3.I120');
  });

  test('includes all 233 PDF-exact public places and 1,075 destinations total', () => {
    const publicLocations = locations.filter((location) => !location.boothCode);

    expect(pdfPlaceCatalog.publicPlaceCount).toBe(EXPECTED_PUBLIC_PLACE_COUNT);
    expect(PDF_PUBLIC_PLACE_COUNT).toBe(EXPECTED_PUBLIC_PLACE_COUNT);
    expect(PDF_DESTINATION_COUNT).toBe(EXPECTED_DESTINATION_COUNT);
    expect(pdfPlaceCatalog.publicPlaces).toHaveLength(EXPECTED_PUBLIC_PLACE_COUNT);
    expect(publicLocations).toHaveLength(EXPECTED_PUBLIC_PLACE_COUNT);
    expect(locations).toHaveLength(EXPECTED_DESTINATION_COUNT);
    expect(locations).toHaveLength(
      EXPECTED_ASSIGNED_BOOTH_COUNT + EXPECTED_PUBLIC_PLACE_COUNT,
    );
    expect(new Set(publicLocations.map((location) => location.id))).toEqual(publicPlaceIds);

    for (const exactPublicId of [
      'registration',
      'main-entrance',
      'help-desk',
      'info-hub',
      'leader-pass-investors',
      'ai-deep-tech-a',
      'prayer-room-east',
      'medical-room-g40',
      'meeting-e3-1',
    ]) {
      expect(publicPlaceIds).toContain(exactPublicId);
    }
  });

  test('use unique location and graph node IDs', () => {
    const locationIds = locations.map((location) => location.id);
    const nodeIds = nodes.map((node) => node.id);

    expect(new Set(locationIds).size).toBe(locationIds.length);
    expect(new Set(nodeIds).size).toBe(nodeIds.length);
  });

  test('reference existing graph nodes and valid graph edges', () => {
    for (const location of locations) {
      expect(
        nodeById.has(location.nodeId),
        `${location.id} references missing node ${location.nodeId}`,
      ).toBe(true);
    }

    for (const edge of edges) {
      expect(nodeById.has(edge.from), `edge starts at missing node ${edge.from}`).toBe(true);
      expect(nodeById.has(edge.to), `edge ends at missing node ${edge.to}`).toBe(true);
    }
  });

  test('keeps every configured destination in one routable graph', () => {
    const adjacency = new Map(nodes.map((node) => [node.id, new Set<string>()]));
    for (const edge of edges) {
      adjacency.get(edge.from)!.add(edge.to);
      adjacency.get(edge.to)!.add(edge.from);
    }

    const visited = new Set<string>();
    const pending = [locations[0].nodeId];
    while (pending.length) {
      const nodeId = pending.pop()!;
      if (visited.has(nodeId)) continue;
      visited.add(nodeId);
      adjacency.get(nodeId)?.forEach((neighbor) => {
        if (!visited.has(neighbor)) pending.push(neighbor);
      });
    }

    for (const location of locations) {
      expect(visited.has(location.nodeId), `${location.id} is disconnected`).toBe(true);
    }

    const samplePairs = [
      ['registration', 'info-hub'],
      ['help-desk', 'booth-h3-i120'],
      ['meeting-e3-1', 'booth-h3-h150'],
    ] as const;
    for (const [originId, destinationId] of samplePairs) {
      const origin = locations.find((location) => location.id === originId)!;
      const destination = locations.find((location) => location.id === destinationId)!;
      const route = createRoute(originId, destinationId, 'en');
      expect(route, `no route from ${originId} to ${destinationId}`).not.toBeNull();
      expect(route!.path[0].id).toBe(origin.nodeId);
      expect(route!.path.at(-1)!.id).toBe(destination.nodeId);
    }
  });

  test('preserves the complete planner catalog beside the live 3D map', async ({ page }) => {
    await page.goto('/');

    const configuredOptions = page.locator('select').first().locator('option:not([value=""])');
    await expect(configuredOptions).toHaveCount(EXPECTED_DESTINATION_COUNT);
    const publicLandmarkCount = locations.filter((location) => !location.boothCode).length;
    expect(publicLandmarkCount).toBe(EXPECTED_PUBLIC_PLACE_COUNT);
    await expect(page.getByTestId('native-leap-map')).toBeVisible();
    await expect(page.locator('iframe')).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Zoom in' })).toBeVisible();

    const finalPdfBooth = locations.at(-1)!;
    const destinationSelect = page.locator('select').nth(1);
    await destinationSelect.focus();
    await destinationSelect.selectOption(finalPdfBooth.id);
    await expect(destinationSelect).toHaveValue(finalPdfBooth.id);
    await expect(destinationSelect.locator(`option[value="${finalPdfBooth.id}"]`)).toContainText(
      locationDisplayName(finalPdfBooth, 'en'),
    );

    await page.getByRole('button', { name: 'ع' }).click();
    await expect(page.locator('.map-panel--native')).toHaveAttribute('aria-label', 'خريطة ليب ثلاثية الأبعاد');
  });
});
