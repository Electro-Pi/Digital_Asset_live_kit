import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Building2,
  Filter,
  Layers3,
  Minus,
  Plus,
  RotateCcw,
  Search,
  Sparkles,
  Star,
  X,
} from 'lucide-react';
import maplibregl, { type GeoJSONSource, type Map as MapLibreMap } from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import {
  locations,
  locationDisplayName,
  nodeById,
  type Language,
  type MapLocation,
  type MapNode,
} from '../data/mapData';
import type { RoutePlan } from '../lib/navigation';
import {
  buildVenueRouter,
  findVenueRoute,
  type VenuePosition,
} from '../lib/venueRouting';

interface MapViewProps {
  language: Language;
  route: RoutePlan | null;
  currentPoint?: MapNode;
  start?: MapLocation;
  destination?: MapLocation;
  picking: 'start' | 'destination';
  onSelectLocation: (location: MapLocation) => void;
}

interface VenuePoi {
  slug: string;
  name: string;
  description?: string | null;
  category_slugs?: string[];
  booth_codes?: string[];
  sponsor_label?: string | null;
  image?: string | null;
  swapcard_logo?: string | null;
}

interface VenueCategory {
  id: string;
  name: string;
  slug: string;
  children?: VenueCategory[];
}

interface PolygonConfig {
  color?: string;
  height?: number;
  base?: number;
  title?: string;
}

interface VenueFeatureProperties {
  id: string;
  name?: string;
  code?: string;
  title?: string;
  polygon_config?: PolygonConfig;
  polygon_color?: string;
  polygon_height?: number;
  polygon_base?: number;
  pois?: Array<{ slug: string; name: string }>;
  sponsor_label?: string;
  map_color?: string;
  map_height?: number;
  map_base?: number;
  category_slugs?: string[];
  description?: string;
  logo?: string;
}

type VenueFeature = GeoJSON.Feature<GeoJSON.Geometry, VenueFeatureProperties>;
type VenueCollection = GeoJSON.FeatureCollection<GeoJSON.Geometry, VenueFeatureProperties>;

interface VenueMapData {
  title: string;
  mapbox_access_token: string;
  center: GeoJSON.Point;
  center_mobile?: GeoJSON.Point;
  zoom: number;
  zoom_mobile?: number;
  zoom_min?: number;
  zoom_max_desktop?: number;
  pitch: number;
  pitch_max?: number;
  bearing: number;
  bg_tileset: string;
  booths_geojson: VenueCollection;
  permanent_polygons: VenueCollection;
  routes_geojson: VenueCollection;
  threed_objects?: Array<{
    id: string;
    name: string;
    is_visible_on_fp: boolean;
    locations: { type: 'MultiPoint'; coordinates: Array<[number, number]> };
  }>;
  pois: VenuePoi[];
  pinned_categories: VenueCategory[];
}

type FilterMode = 'all' | 'exhibitors' | 'partners' | string;

const venueSourceId = 'leap-venue-shapes';
const boothSourceId = 'leap-booths';
const routeSourceId = 'leap-walkways';
const activeRouteSourceId = 'leap-active-route';
const activeRoutePointsSourceId = 'leap-active-route-points';

const venueRouteAnchors = locations.flatMap((location) => {
  const point = nodeById.get(location.nodeId);
  return point ? [{ location, x: point.x, y: point.y }] : [];
});

function coordinatesFromGeometry(geometry: GeoJSON.Geometry): GeoJSON.Position[] {
  if (geometry.type === 'GeometryCollection') {
    return geometry.geometries.flatMap(coordinatesFromGeometry);
  }
  const positions: GeoJSON.Position[] = [];
  const walk = (value: unknown) => {
    if (!Array.isArray(value)) return;
    if (typeof value[0] === 'number' && typeof value[1] === 'number') {
      positions.push(value as GeoJSON.Position);
      return;
    }
    value.forEach(walk);
  };
  walk(geometry.coordinates);
  return positions;
}

function featureCenter(feature: VenueFeature): [number, number] {
  const coordinates = coordinatesFromGeometry(feature.geometry);
  if (!coordinates.length) return [46.387346, 25.253298];
  const bounds = coordinates.reduce(
    (value, coordinate) => ({
      minX: Math.min(value.minX, coordinate[0]),
      minY: Math.min(value.minY, coordinate[1]),
      maxX: Math.max(value.maxX, coordinate[0]),
      maxY: Math.max(value.maxY, coordinate[1]),
    }),
    { minX: Infinity, minY: Infinity, maxX: -Infinity, maxY: -Infinity },
  );
  return [(bounds.minX + bounds.maxX) / 2, (bounds.minY + bounds.maxY) / 2];
}

function flattenVenueData(data: VenueMapData) {
  const poiBySlug = new Map(data.pois.map((poi) => [poi.slug, poi]));
  const booths: VenueFeature[] = data.booths_geojson.features.map((feature) => {
    const linkedPois = (feature.properties.pois ?? [])
      .map((poi) => poiBySlug.get(poi.slug))
      .filter(Boolean) as VenuePoi[];
    const categorySlugs = [...new Set(linkedPois.flatMap((poi) => poi.category_slugs ?? []))];
    const primaryPoi = linkedPois[0];
    return {
      ...feature,
      properties: {
        ...feature.properties,
        name: feature.properties.name || primaryPoi?.name || feature.properties.code || 'LEAP place',
        map_color: feature.properties.polygon_config?.color || '#5b3de1',
        map_height: feature.properties.polygon_config?.height ?? 1.5,
        map_base: feature.properties.polygon_config?.base ?? 0,
        category_slugs: categorySlugs,
        sponsor_label: primaryPoi?.sponsor_label || undefined,
        description: primaryPoi?.description || undefined,
        logo: primaryPoi?.swapcard_logo || primaryPoi?.image || undefined,
      },
    };
  });
  const venueShapes: VenueFeature[] = data.permanent_polygons.features.map((feature) => ({
    ...feature,
    properties: {
      ...feature.properties,
      name: feature.properties.title,
      map_color: feature.properties.polygon_color || '#f5f5f3',
      map_height: feature.properties.polygon_height ?? 0.7,
      map_base: feature.properties.polygon_base ?? 0,
    },
  }));
  const landmarkPattern = /hall|stage|deepfest|digital saudi|creative economy|sports|academy|entrance|investor|founder|startup|ecosystem|aud|main leap/i;
  const utilityPattern = /arrow|wall|washroom|toilet|tunnel|room|seating|parking|palm|escalator|food|terrace|separator|triangle/i;
  const landmarks: VenueFeature[] = (data.threed_objects ?? [])
    .filter((object) => object.is_visible_on_fp && landmarkPattern.test(object.name) && !utilityPattern.test(object.name))
    .flatMap((object) => object.locations.coordinates.map((coordinates, index) => ({
      type: 'Feature' as const,
      id: `${object.id}-${index}`,
      geometry: { type: 'Point' as const, coordinates },
      properties: {
        id: `${object.id}-${index}`,
        name: object.name
          .replace(/^(3D|FEATURE|TEXT)\s+/i, '')
          .replace(/_3d_text.*$/i, '')
          .replace(/_/g, ' ')
          .replace(/\b(FEATURE|TEXT|TITLE)\b/gi, '')
          .replace(/\s+/g, ' ')
          .trim(),
      },
    })));
  return {
    booths,
    boothById: new Map(booths.map((feature) => [feature.properties.id, feature])),
    venueShapes,
    landmarks,
  };
}

function matchesFilter(feature: VenueFeature, filter: FilterMode) {
  if (filter === 'all') return true;
  const categories = feature.properties.category_slugs ?? [];
  if (filter === 'exhibitors') {
    return categories.includes('exhibitors') || categories.includes('exhibitors-2');
  }
  if (filter === 'partners') {
    return Boolean(feature.properties.sponsor_label) || categories.includes('partners-sponsors');
  }
  return categories.includes(filter);
}

export function MapView({ language, route, start, destination }: MapViewProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MapLibreMap | undefined>(undefined);
  const selectedFeatureRef = useRef<string | undefined>(undefined);
  const [mapData, setMapData] = useState<VenueMapData>();
  const [ready, setReady] = useState(false);
  const [error, setError] = useState(false);
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<FilterMode>('all');
  const [showCategories, setShowCategories] = useState(false);
  const [selectedBoothId, setSelectedBoothId] = useState<string>();

  const venue = useMemo(() => (mapData ? flattenVenueData(mapData) : undefined), [mapData]);
  const filteredBooths = useMemo(
    () => venue?.booths.filter((feature) => matchesFilter(feature, filter)) ?? [],
    [filter, venue],
  );
  const searchResults = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase();
    if (!normalized) return [];
    return filteredBooths.filter((feature) => (
      `${feature.properties.name ?? ''} ${feature.properties.code ?? ''}`
        .toLocaleLowerCase()
        .includes(normalized)
    )).slice(0, 12);
  }, [filteredBooths, query]);
  const selectedBooth = selectedBoothId ? venue?.boothById.get(selectedBoothId) : undefined;
  const venueRouter = useMemo(() => (
    mapData && venue
      ? buildVenueRouter(mapData.routes_geojson, venue.booths, venueRouteAnchors)
      : undefined
  ), [mapData, venue]);
  const activeVenueRoute = useMemo(() => {
    if (!route || !start || !destination || !venueRouter) return undefined;
    const startPoint = nodeById.get(start.nodeId);
    const destinationPoint = nodeById.get(destination.nodeId);
    if (!startPoint || !destinationPoint) return undefined;
    return findVenueRoute(
      venueRouter,
      { location: start, x: startPoint.x, y: startPoint.y },
      { location: destination, x: destinationPoint.x, y: destinationPoint.y },
    );
  }, [destination, route, start, venueRouter]);

  useEffect(() => {
    const controller = new AbortController();
    void fetch('/api/leap-map-data', { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error('Map data is unavailable');
        return response.json() as Promise<VenueMapData>;
      })
      .then(setMapData)
      .catch((fetchError: unknown) => {
        if ((fetchError as Error).name !== 'AbortError') setError(true);
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!containerRef.current || !mapData || !venue || mapRef.current) return;
    const narrow = window.matchMedia('(max-width: 820px)').matches;
    const center = narrow && mapData.center_mobile ? mapData.center_mobile : mapData.center;
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: {
        version: 8,
        glyphs: `https://api.mapbox.com/fonts/v1/mapbox/{fontstack}/{range}.pbf?access_token=${mapData.mapbox_access_token}`,
        sources: {},
        layers: [{ id: 'canvas', type: 'background', paint: { 'background-color': '#e3e5e9' } }],
      },
      center: center.coordinates as [number, number],
      zoom: narrow ? mapData.zoom_mobile ?? mapData.zoom : mapData.zoom,
      minZoom: mapData.zoom_min ?? 10,
      maxZoom: mapData.zoom_max_desktop ?? 22,
      pitch: mapData.pitch,
      maxPitch: mapData.pitch_max ?? 60,
      bearing: mapData.bearing,
      attributionControl: false,
    });
    mapRef.current = map;

    map.on('load', () => {
      map.addSource('venue-aerial', { type: 'raster', url: mapData.bg_tileset, tileSize: 256 });
      map.addLayer({
        id: 'venue-aerial',
        type: 'raster',
        source: 'venue-aerial',
        paint: { 'raster-opacity': 1, 'raster-saturation': -0.08 },
      });

      map.addSource(routeSourceId, { type: 'geojson', data: mapData.routes_geojson });
      map.addLayer({
        id: 'venue-walkways',
        type: 'line',
        source: routeSourceId,
        paint: {
          'line-color': '#ffffff',
          'line-width': ['interpolate', ['linear'], ['zoom'], 16, 0.6, 20, 2.2],
          'line-opacity': 0.48,
        },
      });

      map.addSource(venueSourceId, {
        type: 'geojson',
        data: { type: 'FeatureCollection', features: venue.venueShapes },
      });
      map.addLayer({
        id: 'venue-buildings',
        type: 'fill-extrusion',
        source: venueSourceId,
        paint: {
          'fill-extrusion-color': ['coalesce', ['get', 'map_color'], '#f5f5f3'],
          'fill-extrusion-height': ['coalesce', ['get', 'map_height'], 0.7],
          'fill-extrusion-base': ['coalesce', ['get', 'map_base'], 0],
          'fill-extrusion-opacity': 0.96,
        },
      });
      map.addLayer({
        id: 'venue-building-labels',
        type: 'symbol',
        source: venueSourceId,
        filter: ['==', ['get', 'id'], '__never_show_internal_shape_names__'],
        minzoom: 15.5,
        layout: {
          'text-field': ['coalesce', ['get', 'name'], ''],
          'text-font': ['Open Sans Bold'],
          'text-size': ['interpolate', ['linear'], ['zoom'], 16, 8, 20, 13],
          'text-max-width': 11,
          'text-transform': 'uppercase',
          'text-letter-spacing': 0.08,
        },
        paint: {
          'text-color': '#4c3d76',
          'text-halo-color': 'rgba(255,255,255,0.96)',
          'text-halo-width': 2,
        },
      });

      map.addSource('leap-landmarks', {
        type: 'geojson',
        data: { type: 'FeatureCollection', features: venue.landmarks },
      });
      map.addLayer({
        id: 'leap-landmark-labels',
        type: 'symbol',
        source: 'leap-landmarks',
        minzoom: 15,
        layout: {
          'text-field': ['coalesce', ['get', 'name'], ''],
          'text-font': ['Open Sans Bold'],
          'text-size': ['interpolate', ['linear'], ['zoom'], 15, 9, 20, 14],
          'text-max-width': 10,
          'text-transform': 'uppercase',
          'text-letter-spacing': 0.08,
          'text-allow-overlap': false,
        },
        paint: {
          'text-color': '#38247f',
          'text-halo-color': 'rgba(255,255,255,0.98)',
          'text-halo-width': 2.5,
        },
      });

      map.addSource(boothSourceId, {
        type: 'geojson',
        data: { type: 'FeatureCollection', features: venue.booths },
        promoteId: 'id',
      });
      map.addLayer({
        id: 'venue-booths',
        type: 'fill-extrusion',
        source: boothSourceId,
        paint: {
          'fill-extrusion-color': [
            'case',
            ['boolean', ['feature-state', 'selected'], false], '#ffd23f',
            ['boolean', ['feature-state', 'hover'], false], '#7b5dff',
            ['coalesce', ['get', 'map_color'], '#563be5'],
          ],
          'fill-extrusion-height': [
            'case',
            ['boolean', ['feature-state', 'selected'], false], 4.6,
            ['boolean', ['feature-state', 'hover'], false], 3.2,
            ['coalesce', ['get', 'map_height'], 1.5],
          ],
          'fill-extrusion-base': ['coalesce', ['get', 'map_base'], 0],
          'fill-extrusion-opacity': 0.98,
        },
      });
      map.addLayer({
        id: 'venue-booth-labels',
        type: 'symbol',
        source: boothSourceId,
        minzoom: 18.2,
        layout: {
          'text-field': ['coalesce', ['get', 'name'], ['get', 'code'], ''],
          'text-font': ['Open Sans Bold'],
          'text-size': ['interpolate', ['linear'], ['zoom'], 18.2, 8, 21, 12],
          'text-max-width': 9,
          'text-allow-overlap': false,
        },
        paint: {
          'text-color': '#252234',
          'text-halo-color': 'rgba(255,255,255,0.95)',
          'text-halo-width': 1.5,
        },
      });

      map.addSource(activeRouteSourceId, {
        type: 'geojson',
        data: { type: 'FeatureCollection', features: [] },
      });
      map.addLayer({
        id: 'leap-active-route-halo',
        type: 'line',
        source: activeRouteSourceId,
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: {
          'line-color': 'rgba(255,255,255,0.96)',
          'line-width': ['interpolate', ['linear'], ['zoom'], 15, 7, 20, 15],
          'line-opacity': 0.98,
        },
      });
      map.addLayer({
        id: 'leap-active-route-line',
        type: 'line',
        source: activeRouteSourceId,
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: {
          'line-color': '#4263eb',
          'line-width': ['interpolate', ['linear'], ['zoom'], 15, 4, 20, 9],
          'line-opacity': 1,
        },
      });

      map.addSource(activeRoutePointsSourceId, {
        type: 'geojson',
        data: { type: 'FeatureCollection', features: [] },
      });
      map.addLayer({
        id: 'leap-active-route-point-halos',
        type: 'circle',
        source: activeRoutePointsSourceId,
        paint: {
          'circle-radius': ['interpolate', ['linear'], ['zoom'], 15, 7, 20, 12],
          'circle-color': '#ffffff',
          'circle-opacity': 0.98,
        },
      });
      map.addLayer({
        id: 'leap-active-route-points',
        type: 'circle',
        source: activeRoutePointsSourceId,
        paint: {
          'circle-radius': ['interpolate', ['linear'], ['zoom'], 15, 4.5, 20, 8],
          'circle-color': [
            'match',
            ['get', 'kind'],
            'start', '#20b486',
            '#ff6d62',
          ],
          'circle-stroke-color': '#ffffff',
          'circle-stroke-width': 1.5,
        },
      });

      let hoveredId: string | number | undefined;
      map.on('mousemove', 'venue-booths', (event) => {
        map.getCanvas().style.cursor = 'pointer';
        if (hoveredId !== undefined) map.setFeatureState({ source: boothSourceId, id: hoveredId }, { hover: false });
        hoveredId = event.features?.[0]?.properties?.id;
        if (hoveredId !== undefined) map.setFeatureState({ source: boothSourceId, id: hoveredId }, { hover: true });
      });
      map.on('mouseleave', 'venue-booths', () => {
        map.getCanvas().style.cursor = '';
        if (hoveredId !== undefined) map.setFeatureState({ source: boothSourceId, id: hoveredId }, { hover: false });
        hoveredId = undefined;
      });
      map.on('click', 'venue-booths', (event) => {
        const id = event.features?.[0]?.properties?.id;
        if (typeof id === 'string') setSelectedBoothId(id);
      });
      setReady(true);
    });
    map.on('error', (event) => {
      if (event.error) console.warn('LEAP map renderer:', event.error.message);
    });

    return () => {
      map.remove();
      mapRef.current = undefined;
    };
  }, [mapData, venue]);

  useEffect(() => {
    const source = mapRef.current?.getSource(boothSourceId) as GeoJSONSource | undefined;
    if (!source) return;
    source.setData({ type: 'FeatureCollection', features: filteredBooths });
  }, [filteredBooths]);

  useEffect(() => {
    if (!ready) return;
    const map = mapRef.current;
    const routeSource = map?.getSource(activeRouteSourceId) as GeoJSONSource | undefined;
    const pointsSource = map?.getSource(activeRoutePointsSourceId) as GeoJSONSource | undefined;
    if (!map || !routeSource || !pointsSource) return;

    if (!activeVenueRoute) {
      routeSource.setData({ type: 'FeatureCollection', features: [] });
      pointsSource.setData({ type: 'FeatureCollection', features: [] });
      return;
    }

    routeSource.setData({
      type: 'Feature',
      properties: {},
      geometry: { type: 'LineString', coordinates: activeVenueRoute.coordinates },
    });
    pointsSource.setData({
      type: 'FeatureCollection',
      features: [
        {
          type: 'Feature',
          properties: { kind: 'start' },
          geometry: { type: 'Point', coordinates: activeVenueRoute.start },
        },
        {
          type: 'Feature',
          properties: { kind: 'destination' },
          geometry: { type: 'Point', coordinates: activeVenueRoute.destination },
        },
      ],
    });

    const coordinates = activeVenueRoute.coordinates as VenuePosition[];
    const bounds = coordinates.reduce(
      (routeBounds, coordinate) => routeBounds.extend(coordinate),
      new maplibregl.LngLatBounds(coordinates[0], coordinates[0]),
    );
    const narrow = window.matchMedia('(max-width: 820px)').matches;
    map.fitBounds(bounds, {
      padding: narrow ? 45 : 70,
      maxZoom: 19.2,
      duration: 1_000,
    });
  }, [activeVenueRoute, ready]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !venue) return;
    if (selectedFeatureRef.current) {
      map.setFeatureState({ source: boothSourceId, id: selectedFeatureRef.current }, { selected: false });
    }
    selectedFeatureRef.current = selectedBoothId;
    if (!selectedBoothId) return;
    map.setFeatureState({ source: boothSourceId, id: selectedBoothId }, { selected: true });
    const feature = venue.boothById.get(selectedBoothId);
    if (!feature) return;
    map.flyTo({ center: featureCenter(feature), zoom: Math.max(map.getZoom(), 19.2), pitch: 46, duration: 900 });
  }, [selectedBoothId, venue]);

  const chooseBooth = useCallback((feature: VenueFeature) => {
    setSelectedBoothId(feature.properties.id);
    setQuery('');
  }, []);

  const chooseFilter = (nextFilter: FilterMode) => {
    setFilter(nextFilter);
    setSelectedBoothId(undefined);
    setShowCategories(false);
  };

  const resetMap = () => {
    if (!mapData || !mapRef.current) return;
    mapRef.current.easeTo({
      center: mapData.center.coordinates as [number, number],
      zoom: mapData.zoom,
      pitch: mapData.pitch,
      bearing: mapData.bearing,
      duration: 800,
    });
    setSelectedBoothId(undefined);
  };

  const categoryGroups = mapData?.pinned_categories.find((category) => category.slug === 'categories')?.children ?? [];
  const routeDestination = destination ? locationDisplayName(destination, language) : undefined;

  return (
    <section
      className="map-panel map-panel--native"
      aria-label={language === 'ar' ? 'خريطة ليب ثلاثية الأبعاد' : 'LEAP 3D map'}
      data-route-geometry={activeVenueRoute ? 'ready' : route ? 'unavailable' : 'idle'}
    >
      <div ref={containerRef} className="native-map-canvas" data-testid="native-leap-map" />

      {!ready && !error && (
        <div className="map-loader" role="status">
          <span className="map-loader__mark" aria-hidden="true"><span /><span /><span /></span>
          <strong>{language === 'ar' ? 'جارٍ بناء خريطة ليب' : 'Building the LEAP venue map'}</strong>
          <small>{language === 'ar' ? 'يتم تحميل الأماكن والأجنحة والمسارات.' : 'Loading places, booths, and walkways.'}</small>
        </div>
      )}

      {error && (
        <div className="map-loader map-loader--error" role="alert">
          <Layers3 size={32} />
          <strong>{language === 'ar' ? 'تعذر تحميل بيانات الخريطة' : 'The venue map could not load'}</strong>
          <small>{language === 'ar' ? 'تحقق من اتصال الخادم بالإنترنت ثم أعد المحاولة.' : 'Check the server network connection and try again.'}</small>
        </div>
      )}

      <div className="venue-map__search">
        <Search size={19} />
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={language === 'ar' ? 'ابحث عن عارض أو جناح أو مكان…' : 'Search exhibitor, booth, or place…'}
          aria-label={language === 'ar' ? 'البحث في الخريطة' : 'Search the map'}
        />
        {query && <button onClick={() => setQuery('')} aria-label="Clear search"><X size={17} /></button>}
        {query && (
          <div className="venue-map__results">
            {searchResults.length ? searchResults.map((feature) => (
              <button key={feature.properties.id} onClick={() => chooseBooth(feature)}>
                <span>{feature.properties.name}</span>
                <small>{feature.properties.code || feature.properties.polygon_config?.title}</small>
              </button>
            )) : <p>{language === 'ar' ? 'لا توجد نتائج مطابقة.' : 'No matching places.'}</p>}
          </div>
        )}
      </div>

      <div className="venue-map__filters" aria-label="Map filters">
        <button className={filter === 'all' ? 'active' : ''} onClick={() => chooseFilter('all')}>
          {language === 'ar' ? 'عرض الكل' : 'View All'}
        </button>
        <button className={filter === 'exhibitors' ? 'active' : ''} onClick={() => chooseFilter('exhibitors')}>
          <Building2 size={15} /> {language === 'ar' ? 'العارضون' : 'Exhibitors'}
        </button>
        <button className={showCategories ? 'active' : ''} onClick={() => setShowCategories((value) => !value)}>
          <Filter size={15} /> {language === 'ar' ? 'الفئات' : 'Categories'}
        </button>
        <button className={filter === 'partners' ? 'active venue-filter--partners' : 'venue-filter--partners'} onClick={() => chooseFilter('partners')}>
          <Star size={16} fill="currentColor" /> {language === 'ar' ? 'الشركاء والرعاة' : 'Partners & Sponsors'}
        </button>
      </div>

      {showCategories && (
        <div className="venue-map__categories">
          <div><strong>{language === 'ar' ? 'تصفية حسب الفئة' : 'Filter by category'}</strong><button onClick={() => setShowCategories(false)}><X size={16} /></button></div>
          {categoryGroups.flatMap((group) => group.children?.length ? group.children : [group]).slice(0, 18).map((category) => (
            <button key={category.id} className={filter === category.slug ? 'active' : ''} onClick={() => chooseFilter(category.slug)}>
              {category.name}
            </button>
          ))}
        </div>
      )}

      <div className="venue-map__controls" aria-label="Map controls">
        <button onClick={() => mapRef.current?.zoomIn()} aria-label="Zoom in"><Plus size={20} /></button>
        <button onClick={() => mapRef.current?.zoomOut()} aria-label="Zoom out"><Minus size={20} /></button>
        <button onClick={resetMap} aria-label="Reset map"><RotateCcw size={18} /></button>
      </div>

      <div className="venue-map__count">
        <Sparkles size={13} />
        {new Intl.NumberFormat(language === 'ar' ? 'ar-SA' : 'en-US').format(filteredBooths.length)}
        {language === 'ar' ? ' مكان' : ' places'}
      </div>

      {selectedBooth && (
        <article className="venue-map__place-card">
          <button className="place-card__close" onClick={() => setSelectedBoothId(undefined)} aria-label="Close place details"><X size={17} /></button>
          <div className="place-card__logo">
            {selectedBooth.properties.logo
              ? <img src={selectedBooth.properties.logo} alt="" />
              : <Building2 size={22} />}
          </div>
          <div>
            {selectedBooth.properties.sponsor_label && <small>{selectedBooth.properties.sponsor_label}</small>}
            <h3>{selectedBooth.properties.name}</h3>
            <p>{selectedBooth.properties.code || selectedBooth.properties.polygon_config?.title}</p>
          </div>
        </article>
      )}

      {route && (
        <div className="map-route-ready" aria-live="polite">
          <span />
          {activeVenueRoute
            ? language === 'ar'
              ? `يظهر المسار${routeDestination ? ` إلى ${routeDestination}` : ''} الآن على الخريطة.`
              : `The route${routeDestination ? ` to ${routeDestination}` : ''} is now shown on the map.`
            : language === 'ar'
              ? `المسار الصوتي${routeDestination ? ` إلى ${routeDestination}` : ''} جاهز في لوحة الإرشادات.`
              : `Voice route${routeDestination ? ` to ${routeDestination}` : ''} is ready in the guidance panel.`}
        </div>
      )}

      {(start || destination) && <div className="venue-map__selection-accent" aria-hidden="true" />}
    </section>
  );
}
