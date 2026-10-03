export interface MapCoordinates {
  latitude: number;
  longitude: number;
}

export interface MapSize {
  width: number;
  height: number;
}

export interface MapViewport extends MapCoordinates {
  zoom: number;
}

export interface PixelPoint {
  x: number;
  y: number;
}

export const TILE_SIZE: number;
export const MIN_ZOOM: number;
export const MAX_ZOOM: number;
export const MAX_MERCATOR_LATITUDE: number;
export function project(latitude: number, longitude: number, zoom: number): PixelPoint;
export function unproject(x: number, y: number, zoom: number): MapCoordinates;
export function fitMarkers(markers: readonly MapCoordinates[], size: MapSize): MapViewport;
export function wrappedTileX(tileX: number, worldTiles: number): number;
export function nearestWorldPixelX(x: number, centerX: number, worldPixels: number): number;
