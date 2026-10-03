"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent, ReactNode } from "react";
import {
  fitMarkers,
  MAX_ZOOM,
  nearestWorldPixelX,
  project,
  TILE_SIZE,
  unproject,
  wrappedTileX,
} from "../lib/map-geometry.mjs";
import type { MapSize, MapViewport } from "../lib/map-geometry.mjs";

const tileApiKey = process.env.NEXT_PUBLIC_GEOAPIFY_API_KEY?.trim() ?? "";

export type MapMarkerKind = "itinerary" | "reservation" | "candidate" | "search";

export interface TripMapMarker {
  id: string;
  name: string;
  latitude: number;
  longitude: number;
  kind: MapMarkerKind;
  detail: string;
  sourceAttribution?: string | null;
  sourceLicense?: string | null;
  sourceUrl?: string | null;
}

export interface TripMapRoute {
  id: string;
  geometry: [number, number][];
  warning: boolean;
}

export default function TripMap({
  markers,
  routes,
  label,
}: {
  markers: TripMapMarker[];
  routes: TripMapRoute[];
  label: string;
}) {
  const mapRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{
    pointerId: number;
    startX: number;
    startY: number;
    centerX: number;
    centerY: number;
  } | null>(null);
  const [size, setSize] = useState<MapSize>({ width: 640, height: 420 });
  const [viewState, setViewState] = useState<{ key: string; view: MapViewport } | null>(null);
  const [selectedMarker, setSelectedMarker] = useState<{ id: string; signature: string } | null>(null);

  const markerSignature = useMemo(
    () => markers.map((marker) => `${marker.id}:${marker.latitude}:${marker.longitude}`).join("|"),
    [markers],
  );
  const viewportKey = `${markerSignature}:${size.width}:${size.height}`;
  const view = viewState?.key === viewportKey ? viewState.view : fitMarkers(markers, size);
  const selectedMarkerId = selectedMarker?.signature === markerSignature ? selectedMarker.id : null;

  useEffect(() => {
    const element = mapRef.current;
    if (!element) return;
    const updateSize = () => {
      const bounds = element.getBoundingClientRect();
      if (bounds.width > 0 && bounds.height > 0) {
        setSize({ width: bounds.width, height: bounds.height });
      }
    };
    updateSize();
    const observer = new ResizeObserver(updateSize);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const center = project(view.latitude, view.longitude, view.zoom);
  const firstTileX = Math.floor((center.x - size.width / 2) / TILE_SIZE) - 1;
  const lastTileX = Math.floor((center.x + size.width / 2) / TILE_SIZE) + 1;
  const firstTileY = Math.floor((center.y - size.height / 2) / TILE_SIZE) - 1;
  const lastTileY = Math.floor((center.y + size.height / 2) / TILE_SIZE) + 1;
  const worldTiles = 2 ** view.zoom;
  const tileNodes: ReactNode[] = [];

  if (tileApiKey) {
    for (let tileX = firstTileX; tileX <= lastTileX; tileX += 1) {
      for (let tileY = firstTileY; tileY <= lastTileY; tileY += 1) {
        if (tileY < 0 || tileY >= worldTiles) continue;
        const x = wrappedTileX(tileX, worldTiles);
        const left = tileX * TILE_SIZE - center.x + size.width / 2;
        const top = tileY * TILE_SIZE - center.y + size.height / 2;
        tileNodes.push(
          // eslint-disable-next-line @next/next/no-img-element
          <img
            key={`${view.zoom}-${tileX}-${tileY}`}
            className="mapTile"
            src={`https://maps.geoapify.com/v1/tile/osm-carto/${view.zoom}/${x}/${tileY}.png?apiKey=${encodeURIComponent(tileApiKey)}`}
            alt=""
            draggable={false}
            style={{ left, top, width: TILE_SIZE, height: TILE_SIZE }}
          />,
        );
      }
    }
  }

  const markerPositions = markers.map((marker, index) => {
    const point = project(marker.latitude, marker.longitude, view.zoom);
    const wrappedX = nearestWorldPixelX(point.x, center.x, worldTiles * TILE_SIZE);
    return {
      ...marker,
      index: index + 1,
      left: wrappedX - center.x + size.width / 2,
      top: point.y - center.y + size.height / 2,
    };
  });
  const routePolylines = routes.map((route) => {
    const points = route.geometry.map(([longitude, latitude]) => {
      const point = project(latitude, longitude, view.zoom);
      const wrappedX = nearestWorldPixelX(point.x, center.x, worldTiles * TILE_SIZE);
      return `${wrappedX - center.x + size.width / 2},${point.y - center.y + size.height / 2}`;
    });
    return { ...route, points: points.join(" ") };
  });

  const startDrag = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (
      event.button !== 0
      || !tileApiKey
      || (event.target as HTMLElement).closest("button, a")
    ) return;
    dragRef.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      centerX: center.x,
      centerY: center.y,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
  };

  const moveMap = (event: ReactPointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    const nextCenter = unproject(
      drag.centerX - (event.clientX - drag.startX),
      drag.centerY - (event.clientY - drag.startY),
      view.zoom,
    );
    setViewState({ key: viewportKey, view: { ...view, ...nextCenter } });
  };

  const stopDrag = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (dragRef.current?.pointerId === event.pointerId) dragRef.current = null;
  };

  const changeZoom = (delta: number) => {
    setViewState({
      key: viewportKey,
      view: { ...view, zoom: Math.max(2, Math.min(MAX_ZOOM, view.zoom + delta)) },
    });
  };

  const selected = markerPositions.find((marker) => marker.id === selectedMarkerId);

  return (
    <div className="mapFrame">
      <div
        className={`tripMap ${tileApiKey ? "tripMapReady" : "tripMapUnavailable"}`}
        ref={mapRef}
        role="region"
        aria-label={label}
        onPointerDown={startDrag}
        onPointerMove={moveMap}
        onPointerUp={stopDrag}
        onPointerCancel={stopDrag}
      >
        {tileApiKey ? (
          <>
            <div className="mapTiles" aria-hidden="true">{tileNodes}</div>
            {markers.length === 0 ? (
              <div className="mapEmptyOverlay">
                <strong>No trip locations to show yet.</strong>
                <span>Add coordinates to a place or search and save a candidate below.</span>
              </div>
            ) : null}
            <svg
              className="mapRouteLayer"
              viewBox={`0 0 ${size.width} ${size.height}`}
              aria-hidden="true"
            >
              {routePolylines.map((route) => (
                <polyline
                  key={route.id}
                  points={route.points}
                  className={route.warning ? "mapRouteWarning" : "mapRoute"}
                />
              ))}
            </svg>
            {markerPositions.map((marker) => (
              <button
                key={marker.id}
                type="button"
                className={`mapMarker mapMarker-${marker.kind}${selectedMarkerId === marker.id ? " mapMarkerSelected" : ""}`}
                style={{ left: marker.left, top: marker.top }}
                onClick={() => setSelectedMarker((current) =>
                  current?.signature === markerSignature && current.id === marker.id
                    ? null
                    : { id: marker.id, signature: markerSignature },
                )}
                aria-label={`${marker.name}: ${marker.detail}`}
                title={`${marker.name} · ${marker.detail}`}
              >
                {marker.index}
              </button>
            ))}
            {selected ? (
              <div className="mapMarkerPopup" style={{ left: selected.left, top: selected.top }}>
                <strong>{selected.name}</strong>
                <span>{selected.detail}</span>
              </div>
            ) : null}
            <div className="mapZoomControls" aria-label="Map zoom controls">
              <button type="button" onClick={() => changeZoom(1)} aria-label="Zoom in">+</button>
              <button type="button" onClick={() => changeZoom(-1)} aria-label="Zoom out">−</button>
            </div>
            <div className="mapAttribution">
              <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">© OpenStreetMap contributors</a>
              <span> · </span>
              <a href="https://www.geoapify.com/" target="_blank" rel="noreferrer">Powered by Geoapify</a>
            </div>
            <span className="srOnly">Drag to pan. Use the zoom buttons to change map scale.</span>
          </>
        ) : (
          <div className="mapMissingKey">
            <strong>Map tiles need a Geoapify key.</strong>
            <span>Set NEXT_PUBLIC_GEOAPIFY_API_KEY in frontend/.env.local and restart the web app.</span>
            <span>Location search and manual planning remain available.</span>
          </div>
        )}
      </div>
      {markers.length > 0 ? (
        <ol className="mapLocationList" aria-label={`${label} locations`}>
          {markers.map((marker, index) => (
            <li key={marker.id}>
              <span className={`mapListNumber mapListNumber-${marker.kind}`}>{index + 1}</span>
              <span>
                <strong>{marker.name}</strong>
                <small>{marker.detail}</small>
                {marker.sourceAttribution ? (
                  <small>
                    {marker.sourceAttribution}
                    {marker.sourceLicense ? ` · ${marker.sourceLicense}` : ""}
                    {marker.sourceUrl ? <> · <a href={marker.sourceUrl} target="_blank" rel="noreferrer">Source</a></> : null}
                  </small>
                ) : null}
              </span>
            </li>
          ))}
        </ol>
      ) : null}
    </div>
  );
}
