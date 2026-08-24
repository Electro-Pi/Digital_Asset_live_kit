import { useMemo, useRef, useState, type PointerEvent as ReactPointerEvent, type WheelEvent } from 'react';
import { Crosshair, LocateFixed, Minus, Plus } from 'lucide-react';
import {
  locations,
  nodeById,
  type Language,
  type MapLocation,
  type MapNode,
} from '../data/mapData';
import type { RoutePlan } from '../lib/navigation';

interface MapViewProps {
  language: Language;
  route: RoutePlan | null;
  currentPoint?: MapNode;
  start?: MapLocation;
  destination?: MapLocation;
  picking: 'start' | 'destination';
  onSelectLocation: (location: MapLocation) => void;
}

const BASE_VIEW = { centerX: 590, centerY: 750, width: 1090, height: 1280 };

export function MapView({
  language,
  route,
  currentPoint,
  start,
  destination,
  picking,
  onSelectLocation,
}: MapViewProps) {
  const [zoom, setZoom] = useState(1);
  const [center, setCenter] = useState({ x: BASE_VIEW.centerX, y: BASE_VIEW.centerY });
  const svgRef = useRef<SVGSVGElement>(null);
  const dragRef = useRef<{ clientX: number; clientY: number; centerX: number; centerY: number } | null>(null);

  const viewBox = useMemo(() => {
    const width = BASE_VIEW.width / zoom;
    const height = BASE_VIEW.height / zoom;
    return { x: center.x - width / 2, y: center.y - height / 2, width, height };
  }, [center, zoom]);

  const routePoints = route?.path.map((node) => `${node.x},${node.y}`).join(' ') ?? '';

  const setSafeZoom = (nextZoom: number) => setZoom(Math.min(2.8, Math.max(1, nextZoom)));

  const resetView = () => {
    setZoom(1);
    setCenter({ x: BASE_VIEW.centerX, y: BASE_VIEW.centerY });
  };

  const focusRoute = () => {
    if (!route?.path.length) return resetView();
    const xs = route.path.map((node) => node.x);
    const ys = route.path.map((node) => node.y);
    const minX = Math.min(...xs);
    const maxX = Math.max(...xs);
    const minY = Math.min(...ys);
    const maxY = Math.max(...ys);
    setCenter({ x: (minX + maxX) / 2, y: (minY + maxY) / 2 });
    const desiredZoom = Math.min(BASE_VIEW.width / Math.max(430, maxX - minX + 180), BASE_VIEW.height / Math.max(560, maxY - minY + 200));
    setSafeZoom(desiredZoom);
  };

  const onPointerDown = (event: ReactPointerEvent<SVGSVGElement>) => {
    if ((event.target as Element).closest('[data-landmark]')) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    dragRef.current = {
      clientX: event.clientX,
      clientY: event.clientY,
      centerX: center.x,
      centerY: center.y,
    };
  };

  const onPointerMove = (event: ReactPointerEvent<SVGSVGElement>) => {
    const drag = dragRef.current;
    const rect = svgRef.current?.getBoundingClientRect();
    if (!drag || !rect) return;
    const dx = ((event.clientX - drag.clientX) / rect.width) * viewBox.width;
    const dy = ((event.clientY - drag.clientY) / rect.height) * viewBox.height;
    setCenter({ x: drag.centerX - dx, y: drag.centerY - dy });
  };

  const onPointerUp = () => {
    dragRef.current = null;
  };

  const onWheel = (event: WheelEvent<SVGSVGElement>) => {
    event.preventDefault();
    setSafeZoom(zoom + (event.deltaY < 0 ? 0.15 : -0.15));
  };

  return (
    <section className="map-panel" aria-label={language === 'ar' ? 'خريطة الفعالية' : 'Event map'}>
      <div className="map-topbar">
        <div>
          <div className="eyebrow"><span className="live-dot" /> {language === 'ar' ? 'خريطة داخلية' : 'INDOOR MAP'}</div>
          <h2>{language === 'ar' ? 'مخطط فعالية ليب' : 'LEAP event plan'}</h2>
        </div>
        <div className="map-source"><Crosshair size={14} /> {language === 'ar' ? '١٧ وجهة محددة' : '17 mapped places'}</div>
      </div>

      <div className="map-viewport">
        <svg
          ref={svgRef}
          className="floor-map"
          viewBox={`${viewBox.x} ${viewBox.y} ${viewBox.width} ${viewBox.height}`}
          role="img"
          aria-label={language === 'ar' ? 'خريطة قابلة للتكبير مع مسار ملاحي' : 'Zoomable floor plan with navigation route'}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
          onWheel={onWheel}
        >
          <image href="/leap-plan.png" x="0" y="0" width="1275" height="1650" />

          {route && (
            <g className="route-layer" aria-label="Active route">
              <polyline className="route-halo" points={routePoints} />
              <polyline className="route-line" points={routePoints} />
              <polyline className="route-motion" points={routePoints} />
            </g>
          )}

          {locations.map((location) => {
            const node = nodeById.get(location.nodeId);
            if (!node) return null;
            const selected = start?.id === location.id || destination?.id === location.id;
            const label = language === 'ar' ? location.shortNameAr : location.shortName;
            const labelWidth = Math.max(78, Math.min(170, label.length * (language === 'ar' ? 9 : 7.3) + 26));
            return (
              <g
                key={location.id}
                data-landmark
                className={`landmark ${location.featured || selected ? 'landmark--featured' : ''} ${selected ? 'landmark--selected' : ''}`}
                transform={`translate(${node.x} ${node.y})`}
                role="button"
                tabIndex={0}
                aria-label={`${picking === 'start' ? 'Set current location to' : 'Set destination to'} ${location.name}`}
                onPointerDown={(event) => event.stopPropagation()}
                onClick={() => onSelectLocation(location)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') onSelectLocation(location);
                }}
              >
                <circle className="landmark-target" r="24" />
                <circle className="landmark-dot" r={selected ? 8 : 5.5} />
                {(location.featured || selected) && (
                  <g className="landmark-label" transform="translate(10 -30)">
                    <rect x="0" y="0" width={labelWidth} height="25" rx="12.5" />
                    <text x="12" y="16.5">{label}</text>
                  </g>
                )}
              </g>
            );
          })}

          {destination && (() => {
            const point = nodeById.get(destination.nodeId);
            if (!point) return null;
            return (
              <g className="destination-pin" transform={`translate(${point.x} ${point.y - 4})`}>
                <path d="M0,-23 C-12,-23 -19,-15 -19,-5 C-19,9 0,25 0,25 C0,25 19,9 19,-5 C19,-15 12,-23 0,-23Z" />
                <circle cy="-5" r="6" />
              </g>
            );
          })()}

          {currentPoint && (
            <g className="position-marker" transform={`translate(${currentPoint.x} ${currentPoint.y})`}>
              <circle className="position-pulse" r="28" />
              <circle className="position-ring" r="14" />
              <circle className="position-core" r="8" />
            </g>
          )}
        </svg>

        <div className="map-controls" aria-label="Map controls">
          <button onClick={() => setSafeZoom(zoom + 0.25)} aria-label="Zoom in"><Plus size={19} /></button>
          <button onClick={() => setSafeZoom(zoom - 0.25)} aria-label="Zoom out"><Minus size={19} /></button>
          <button onClick={route ? focusRoute : resetView} aria-label="Fit route"><LocateFixed size={19} /></button>
        </div>

        <div className="map-hint">
          <span className={`pick-indicator pick-indicator--${picking}`} />
          {picking === 'start'
            ? language === 'ar' ? 'اضغط على الخريطة لتحديد موقعك' : 'Tap a place to set your location'
            : language === 'ar' ? 'اضغط على الخريطة لاختيار وجهتك' : 'Tap a place to choose your destination'}
        </div>
      </div>
    </section>
  );
}
