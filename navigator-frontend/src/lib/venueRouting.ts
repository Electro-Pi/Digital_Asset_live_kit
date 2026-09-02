import type { MapLocation } from '../data/mapData';

export type VenuePosition = [number, number];

interface RouteEdge {
  to: string;
  distance: number;
}

interface CalibrationPoint {
  x: number;
  y: number;
  coordinate: VenuePosition;
}

interface CoordinateTransform {
  longitude: [number, number, number];
  latitude: [number, number, number];
}

export interface VenueRouteAnchor {
  location: MapLocation;
  x: number;
  y: number;
}

export interface VenueRouteEndpoint {
  location: MapLocation;
  x: number;
  y: number;
}

export interface VenueRouter {
  nodes: Map<string, VenuePosition>;
  adjacency: Map<string, RouteEdge[]>;
  boothCenters: Map<string, VenuePosition>;
  transform?: CoordinateTransform;
}

export interface VenueRoutePath {
  coordinates: VenuePosition[];
  start: VenuePosition;
  destination: VenuePosition;
  distanceMeters: number;
}

type VenueGeometryFeature = GeoJSON.Feature<GeoJSON.Geometry, { code?: string }>;
type VenueGeometryCollection = GeoJSON.FeatureCollection<GeoJSON.Geometry, { code?: string }>;

const coordinateKey = (coordinate: VenuePosition) => (
  `${coordinate[0].toFixed(7)},${coordinate[1].toFixed(7)}`
);

const normalizeBoothCode = (value?: string) => (
  (value ?? '').toLocaleUpperCase().replace(/[^A-Z0-9]/g, '')
);

function geometryCoordinates(geometry: GeoJSON.Geometry): VenuePosition[] {
  if (geometry.type === 'GeometryCollection') {
    return geometry.geometries.flatMap(geometryCoordinates);
  }
  const positions: VenuePosition[] = [];
  const visit = (value: unknown) => {
    if (!Array.isArray(value)) return;
    if (typeof value[0] === 'number' && typeof value[1] === 'number') {
      positions.push([value[0], value[1]]);
      return;
    }
    value.forEach(visit);
  };
  visit(geometry.coordinates);
  return positions;
}

function geometryCenter(feature: VenueGeometryFeature): VenuePosition | undefined {
  const coordinates = geometryCoordinates(feature.geometry);
  if (!coordinates.length) return undefined;
  let minLongitude = Infinity;
  let minLatitude = Infinity;
  let maxLongitude = -Infinity;
  let maxLatitude = -Infinity;
  for (const [longitude, latitude] of coordinates) {
    minLongitude = Math.min(minLongitude, longitude);
    minLatitude = Math.min(minLatitude, latitude);
    maxLongitude = Math.max(maxLongitude, longitude);
    maxLatitude = Math.max(maxLatitude, latitude);
  }
  return [(minLongitude + maxLongitude) / 2, (minLatitude + maxLatitude) / 2];
}

function distanceMeters(first: VenuePosition, second: VenuePosition) {
  const latitudeRadians = ((first[1] + second[1]) / 2) * Math.PI / 180;
  const longitudeMeters = (first[0] - second[0]) * 111_320 * Math.cos(latitudeRadians);
  const latitudeMeters = (first[1] - second[1]) * 111_320;
  return Math.hypot(longitudeMeters, latitudeMeters);
}

function solveThreeVariableLeastSquares(rows: Array<[number, number, number]>, values: number[]) {
  const matrix = Array.from({ length: 3 }, (_, row) => Array.from({ length: 4 }, (_, column) => {
    if (column === 3) {
      return rows.reduce((sum, valuesRow, index) => sum + valuesRow[row] * values[index], 0);
    }
    return rows.reduce((sum, valuesRow) => sum + valuesRow[row] * valuesRow[column], 0);
  }));

  for (let column = 0; column < 3; column += 1) {
    let pivot = column;
    for (let row = column + 1; row < 3; row += 1) {
      if (Math.abs(matrix[row][column]) > Math.abs(matrix[pivot][column])) pivot = row;
    }
    [matrix[column], matrix[pivot]] = [matrix[pivot], matrix[column]];
    const divisor = matrix[column][column];
    if (Math.abs(divisor) < Number.EPSILON) return undefined;
    for (let index = column; index < 4; index += 1) matrix[column][index] /= divisor;
    for (let row = 0; row < 3; row += 1) {
      if (row === column) continue;
      const multiplier = matrix[row][column];
      for (let index = column; index < 4; index += 1) {
        matrix[row][index] -= multiplier * matrix[column][index];
      }
    }
  }
  return matrix.map((row) => row[3]) as [number, number, number];
}

function fitTransform(points: CalibrationPoint[]): CoordinateTransform | undefined {
  if (points.length < 3) return undefined;
  const rows = points.map(({ x, y }) => [x, y, 1] as [number, number, number]);
  const longitude = solveThreeVariableLeastSquares(rows, points.map(({ coordinate }) => coordinate[0]));
  const latitude = solveThreeVariableLeastSquares(rows, points.map(({ coordinate }) => coordinate[1]));
  return longitude && latitude ? { longitude, latitude } : undefined;
}

function transformPoint(transform: CoordinateTransform, x: number, y: number): VenuePosition {
  return [
    transform.longitude[0] * x + transform.longitude[1] * y + transform.longitude[2],
    transform.latitude[0] * x + transform.latitude[1] * y + transform.latitude[2],
  ];
}

function createRobustTransform(points: CalibrationPoint[]) {
  let included = points;
  let transform = fitTransform(included);
  if (!transform) return undefined;

  // A few duplicated or relocated booth codes can skew a venue-wide fit. Two
  // passes remove only large residuals while retaining hundreds of anchors.
  for (let pass = 0; pass < 2; pass += 1) {
    const residuals = included
      .map((point) => distanceMeters(point.coordinate, transformPoint(transform!, point.x, point.y)))
      .sort((first, second) => first - second);
    const median = residuals[Math.floor(residuals.length / 2)] ?? 0;
    const threshold = Math.max(18, median * 4);
    const filtered = included.filter((point) => (
      distanceMeters(point.coordinate, transformPoint(transform!, point.x, point.y)) <= threshold
    ));
    if (filtered.length < 3 || filtered.length === included.length) break;
    included = filtered;
    transform = fitTransform(included);
    if (!transform) break;
  }
  return transform;
}

function addGraphEdge(
  adjacency: Map<string, RouteEdge[]>,
  nodes: Map<string, VenuePosition>,
  first: VenuePosition,
  second: VenuePosition,
) {
  const firstKey = coordinateKey(first);
  const secondKey = coordinateKey(second);
  if (firstKey === secondKey) return;
  nodes.set(firstKey, first);
  nodes.set(secondKey, second);
  const distance = distanceMeters(first, second);
  const firstEdges = adjacency.get(firstKey) ?? [];
  const secondEdges = adjacency.get(secondKey) ?? [];
  firstEdges.push({ to: secondKey, distance });
  secondEdges.push({ to: firstKey, distance });
  adjacency.set(firstKey, firstEdges);
  adjacency.set(secondKey, secondEdges);
}

export function buildVenueRouter(
  routes: VenueGeometryCollection,
  booths: VenueGeometryFeature[],
  anchors: VenueRouteAnchor[],
): VenueRouter {
  const nodes = new Map<string, VenuePosition>();
  const adjacency = new Map<string, RouteEdge[]>();
  for (const feature of routes.features) {
    if (feature.geometry.type !== 'LineString') continue;
    const coordinates = feature.geometry.coordinates as VenuePosition[];
    for (let index = 1; index < coordinates.length; index += 1) {
      addGraphEdge(adjacency, nodes, coordinates[index - 1], coordinates[index]);
    }
  }

  const boothCenters = new Map<string, VenuePosition>();
  for (const booth of booths) {
    const code = normalizeBoothCode(booth.properties.code);
    const center = geometryCenter(booth);
    if (code && center) boothCenters.set(code, center);
  }
  const calibrationPoints = anchors.flatMap(({ location, x, y }) => {
    const coordinate = boothCenters.get(normalizeBoothCode(location.boothCode));
    return coordinate ? [{ x, y, coordinate }] : [];
  });

  return {
    nodes,
    adjacency,
    boothCenters,
    transform: createRobustTransform(calibrationPoints),
  };
}

function resolveEndpoint(router: VenueRouter, endpoint: VenueRouteEndpoint) {
  const boothCenter = router.boothCenters.get(normalizeBoothCode(endpoint.location.boothCode));
  if (boothCenter) return boothCenter;
  if (router.transform) return transformPoint(router.transform, endpoint.x, endpoint.y);
  return undefined;
}

function nearestNode(router: VenueRouter, coordinate: VenuePosition) {
  let nearestKey: string | undefined;
  let nearestDistance = Infinity;
  for (const [key, nodeCoordinate] of router.nodes) {
    const distance = distanceMeters(coordinate, nodeCoordinate);
    if (distance < nearestDistance) {
      nearestKey = key;
      nearestDistance = distance;
    }
  }
  return nearestKey;
}

interface HeapEntry {
  key: string;
  distance: number;
}

function pushHeap(heap: HeapEntry[], entry: HeapEntry) {
  heap.push(entry);
  let index = heap.length - 1;
  while (index > 0) {
    const parent = Math.floor((index - 1) / 2);
    if (heap[parent].distance <= entry.distance) break;
    heap[index] = heap[parent];
    index = parent;
  }
  heap[index] = entry;
}

function popHeap(heap: HeapEntry[]) {
  if (!heap.length) return undefined;
  const first = heap[0];
  const last = heap.pop()!;
  if (heap.length) {
    let index = 0;
    while (true) {
      const left = index * 2 + 1;
      const right = left + 1;
      if (left >= heap.length) break;
      const child = right < heap.length && heap[right].distance < heap[left].distance ? right : left;
      if (heap[child].distance >= last.distance) break;
      heap[index] = heap[child];
      index = child;
    }
    heap[index] = last;
  }
  return first;
}

function shortestNodePath(router: VenueRouter, startKey: string, destinationKey: string) {
  const distances = new Map<string, number>([[startKey, 0]]);
  const previous = new Map<string, string>();
  const heap: HeapEntry[] = [];
  pushHeap(heap, { key: startKey, distance: 0 });

  while (heap.length) {
    const current = popHeap(heap)!;
    if (current.distance !== distances.get(current.key)) continue;
    if (current.key === destinationKey) break;
    for (const edge of router.adjacency.get(current.key) ?? []) {
      const nextDistance = current.distance + edge.distance;
      if (nextDistance >= (distances.get(edge.to) ?? Infinity)) continue;
      distances.set(edge.to, nextDistance);
      previous.set(edge.to, current.key);
      pushHeap(heap, { key: edge.to, distance: nextDistance });
    }
  }
  if (!distances.has(destinationKey)) return undefined;

  const keys = [destinationKey];
  while (keys[0] !== startKey) {
    const parent = previous.get(keys[0]);
    if (!parent) return undefined;
    keys.unshift(parent);
  }
  return {
    coordinates: keys.map((key) => router.nodes.get(key)!),
    distance: distances.get(destinationKey)!,
  };
}

function removeConsecutiveDuplicates(coordinates: VenuePosition[]) {
  return coordinates.filter((coordinate, index) => (
    index === 0 || coordinateKey(coordinate) !== coordinateKey(coordinates[index - 1])
  ));
}

export function findVenueRoute(
  router: VenueRouter,
  startEndpoint: VenueRouteEndpoint,
  destinationEndpoint: VenueRouteEndpoint,
): VenueRoutePath | undefined {
  const start = resolveEndpoint(router, startEndpoint);
  const destination = resolveEndpoint(router, destinationEndpoint);
  if (!start || !destination || !router.nodes.size) return undefined;
  const startKey = nearestNode(router, start);
  const destinationKey = nearestNode(router, destination);
  if (!startKey || !destinationKey) return undefined;
  const path = shortestNodePath(router, startKey, destinationKey);
  if (!path) return undefined;

  const startNode = router.nodes.get(startKey)!;
  const destinationNode = router.nodes.get(destinationKey)!;
  return {
    start,
    destination,
    coordinates: removeConsecutiveDuplicates([start, ...path.coordinates, destination]),
    distanceMeters: distanceMeters(start, startNode) + path.distance + distanceMeters(destinationNode, destination),
  };
}
