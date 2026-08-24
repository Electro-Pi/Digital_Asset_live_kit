import {
  edges,
  locationById,
  nodeById,
  type Language,
  type MapEdge,
  type MapNode,
} from '../data/mapData';

export interface RouteStep {
  id: string;
  maneuver: 'depart' | 'straight' | 'left' | 'right' | 'arrive';
  instruction: string;
  corridor: string;
  distanceMeters: number;
  fromNodeId: string;
  toNodeId: string;
  note?: string;
}

export interface RoutePlan {
  startId: string;
  destinationId: string;
  path: MapNode[];
  steps: RouteStep[];
  distanceMeters: number;
  durationMinutes: number;
}

interface GraphLink {
  nodeId: string;
  edge: MapEdge;
  weight: number;
}

const METERS_PER_MAP_UNIT = 0.22;

function distance(a: MapNode, b: MapNode) {
  return Math.hypot(a.x - b.x, a.y - b.y);
}

function buildGraph() {
  const graph = new Map<string, GraphLink[]>();
  nodeById.forEach((_, id) => graph.set(id, []));

  edges.forEach((edge) => {
    const from = nodeById.get(edge.from);
    const to = nodeById.get(edge.to);
    if (!from || !to) return;
    const weight = distance(from, to);
    graph.get(edge.from)?.push({ nodeId: edge.to, edge, weight });
    graph.get(edge.to)?.push({ nodeId: edge.from, edge, weight });
  });

  return graph;
}

const graph = buildGraph();

function shortestPath(startNodeId: string, endNodeId: string) {
  const distances = new Map<string, number>();
  const previous = new Map<string, { nodeId: string; edge: MapEdge }>();
  const unvisited = new Set(nodeById.keys());

  nodeById.forEach((_, id) => distances.set(id, Number.POSITIVE_INFINITY));
  distances.set(startNodeId, 0);

  while (unvisited.size) {
    let current: string | undefined;
    let best = Number.POSITIVE_INFINITY;
    unvisited.forEach((id) => {
      const candidate = distances.get(id) ?? Number.POSITIVE_INFINITY;
      if (candidate < best) {
        best = candidate;
        current = id;
      }
    });

    if (!current || best === Number.POSITIVE_INFINITY) break;
    if (current === endNodeId) break;
    unvisited.delete(current);

    graph.get(current)?.forEach((link) => {
      if (!unvisited.has(link.nodeId)) return;
      const candidate = best + link.weight;
      if (candidate < (distances.get(link.nodeId) ?? Number.POSITIVE_INFINITY)) {
        distances.set(link.nodeId, candidate);
        previous.set(link.nodeId, { nodeId: current!, edge: link.edge });
      }
    });
  }

  const nodeIds = [endNodeId];
  const pathEdges: MapEdge[] = [];
  let cursor = endNodeId;
  while (cursor !== startNodeId) {
    const entry = previous.get(cursor);
    if (!entry) return null;
    nodeIds.unshift(entry.nodeId);
    pathEdges.unshift(entry.edge);
    cursor = entry.nodeId;
  }

  return {
    nodes: nodeIds.map((id) => nodeById.get(id)!),
    edges: pathEdges,
  };
}

function normalizedTurn(previousAngle: number, nextAngle: number) {
  let delta = nextAngle - previousAngle;
  while (delta > Math.PI) delta -= Math.PI * 2;
  while (delta < -Math.PI) delta += Math.PI * 2;
  return delta;
}

function formatDistance(meters: number, language: Language) {
  const rounded = Math.max(5, Math.round(meters / 5) * 5);
  return language === 'ar' ? `حوالي ${rounded} متر` : `about ${rounded} m`;
}

function getInstruction(
  maneuver: RouteStep['maneuver'],
  corridor: string,
  distanceMeters: number,
  language: Language,
) {
  const distanceText = formatDistance(distanceMeters, language);
  if (language === 'ar') {
    if (maneuver === 'depart') return `ابدأ وامش مع ${corridor} لمسافة ${distanceText}.`;
    if (maneuver === 'left') return `لف يسار وكمّل مع ${corridor} لمسافة ${distanceText}.`;
    if (maneuver === 'right') return `لف يمين وكمّل مع ${corridor} لمسافة ${distanceText}.`;
    return `كمّل سيدا مع ${corridor} لمسافة ${distanceText}.`;
  }

  if (maneuver === 'depart') return `Start along the ${corridor} for ${distanceText}.`;
  if (maneuver === 'left') return `Turn left onto the ${corridor} and continue for ${distanceText}.`;
  if (maneuver === 'right') return `Turn right onto the ${corridor} and continue for ${distanceText}.`;
  return `Continue along the ${corridor} for ${distanceText}.`;
}

export function createRoute(startId: string, destinationId: string, language: Language): RoutePlan | null {
  const start = locationById.get(startId);
  const destination = locationById.get(destinationId);
  if (!start || !destination || start.id === destination.id) return null;

  const result = shortestPath(start.nodeId, destination.nodeId);
  if (!result) return null;

  const grouped: Array<{
    corridor: string;
    note?: string;
    startIndex: number;
    endIndex: number;
    distance: number;
  }> = [];

  result.edges.forEach((edge, edgeIndex) => {
    const corridor = language === 'ar' ? edge.corridorAr : edge.corridor;
    const note = language === 'ar' ? edge.noteAr : edge.note;
    const segmentDistance = distance(result.nodes[edgeIndex], result.nodes[edgeIndex + 1]);
    const last = grouped.at(-1);
    if (last?.corridor === corridor) {
      last.endIndex = edgeIndex + 1;
      last.distance += segmentDistance;
      if (!last.note && note) last.note = note;
    } else {
      grouped.push({ corridor, note, startIndex: edgeIndex, endIndex: edgeIndex + 1, distance: segmentDistance });
    }
  });

  const steps: RouteStep[] = grouped.map((group, index) => {
    let maneuver: RouteStep['maneuver'] = index === 0 ? 'depart' : 'straight';
    if (index > 0) {
      const previousGroup = grouped[index - 1];
      const previousFrom = result.nodes[previousGroup.startIndex];
      const previousTo = result.nodes[previousGroup.endIndex];
      const nextFrom = result.nodes[group.startIndex];
      const nextTo = result.nodes[group.endIndex];
      const previousAngle = Math.atan2(previousTo.y - previousFrom.y, previousTo.x - previousFrom.x);
      const nextAngle = Math.atan2(nextTo.y - nextFrom.y, nextTo.x - nextFrom.x);
      const turn = normalizedTurn(previousAngle, nextAngle);
      if (turn > 0.38) maneuver = 'right';
      if (turn < -0.38) maneuver = 'left';
    }

    const distanceMeters = Math.round(group.distance * METERS_PER_MAP_UNIT);
    return {
      id: `${group.startIndex}-${group.endIndex}`,
      maneuver,
      instruction: getInstruction(maneuver, group.corridor, distanceMeters, language),
      corridor: group.corridor,
      distanceMeters,
      fromNodeId: result.nodes[group.startIndex].id,
      toNodeId: result.nodes[group.endIndex].id,
      note: group.note,
    };
  });

  const totalMapDistance = result.nodes.slice(1).reduce(
    (total, node, index) => total + distance(result.nodes[index], node),
    0,
  );
  const distanceMeters = Math.max(5, Math.round((totalMapDistance * METERS_PER_MAP_UNIT) / 5) * 5);
  const durationMinutes = Math.max(1, Math.ceil(distanceMeters / 72));

  steps.push({
    id: 'arrive',
    maneuver: 'arrive',
    instruction:
      language === 'ar'
        ? `وصلت ${destination.nameAr}. بتلقى الوجهة عند نهاية الخط البنفسجي.`
        : `You have arrived at ${destination.name}. It is at the end of the purple route.`,
    corridor: language === 'ar' ? 'الوجهة' : 'Destination',
    distanceMeters: 0,
    fromNodeId: result.nodes.at(-1)!.id,
    toNodeId: result.nodes.at(-1)!.id,
  });

  return { startId, destinationId, path: result.nodes, steps, distanceMeters, durationMinutes };
}

export function routeSpeech(plan: RoutePlan, language: Language) {
  const start = locationById.get(plan.startId)!;
  const destination = locationById.get(plan.destinationId)!;
  const stepText = plan.steps.map((step) => `${step.instruction} ${step.note ?? ''}`.trim()).join(' ');
  if (language === 'ar') {
    return `تمام. مسارك من ${start.nameAr} إلى ${destination.nameAr}. المشوار تقريباً ${plan.distanceMeters} متر وياخذ ${plan.durationMinutes} دقيقة. ${stepText}`;
  }
  return `Route ready from ${start.name} to ${destination.name}. It is approximately ${plan.distanceMeters} metres and takes ${plan.durationMinutes} minutes. ${stepText}`;
}

export function findLocationFromSpeech(transcript: string) {
  const normalized = transcript.toLocaleLowerCase().replace(/[.,!?]/g, '').trim();
  return [...locationById.values()]
    .map((location) => ({
      location,
      score: [location.name, location.nameAr, ...location.aliases].reduce((best, alias) => {
        const candidate = alias.toLocaleLowerCase();
        if (normalized === candidate) return Math.max(best, 100 + candidate.length);
        if (normalized.includes(candidate) || candidate.includes(normalized)) return Math.max(best, candidate.length);
        return best;
      }, 0),
    }))
    .sort((a, b) => b.score - a.score)
    .find((entry) => entry.score > 0)?.location;
}
