export const TILE_SIZE = 256;
export const MIN_ZOOM = 2;
export const MAX_ZOOM = 19;
export const MAX_MERCATOR_LATITUDE = 85.05112878;

export function project(latitude, longitude, zoom) {
  const scale = TILE_SIZE * 2 ** zoom;
  const boundedLatitude = Math.max(
    -MAX_MERCATOR_LATITUDE,
    Math.min(MAX_MERCATOR_LATITUDE, latitude),
  );
  const sin = Math.sin((boundedLatitude * Math.PI) / 180);
  return {
    x: ((longitude + 180) / 360) * scale,
    y: (0.5 - Math.log((1 + sin) / (1 - sin)) / (4 * Math.PI)) * scale,
  };
}

export function unproject(x, y, zoom) {
  const scale = TILE_SIZE * 2 ** zoom;
  const longitude = (x / scale) * 360 - 180;
  const mercator = Math.PI * (1 - (2 * y) / scale);
  const latitude = (180 / Math.PI) * Math.atan(Math.sinh(mercator));
  return { latitude, longitude };
}

export function fitMarkers(markers, size) {
  if (markers.length === 0) return { latitude: 20, longitude: 0, zoom: MIN_ZOOM };
  if (markers.length === 1) {
    return {
      latitude: markers[0].latitude,
      longitude: markers[0].longitude,
      zoom: 13,
    };
  }

  for (let zoom = 16; zoom >= MIN_ZOOM; zoom -= 1) {
    const points = markers.map((marker) => project(marker.latitude, marker.longitude, zoom));
    const xs = points.map((point) => point.x);
    const ys = points.map((point) => point.y);
    const worldWidth = TILE_SIZE * 2 ** zoom;
    const minX = Math.min(...xs);
    const maxX = Math.max(...xs);
    const wrapsWorld = maxX - minX > worldWidth / 2;
    const adjustedXs = wrapsWorld ? xs.map((x) => (x < worldWidth / 2 ? x + worldWidth : x)) : xs;
    const adjustedMinX = Math.min(...adjustedXs);
    const adjustedMaxX = Math.max(...adjustedXs);
    const minY = Math.min(...ys);
    const maxY = Math.max(...ys);
    if (adjustedMaxX - adjustedMinX <= size.width - 96 && maxY - minY <= size.height - 96) {
      const center = unproject(
        (adjustedMinX + adjustedMaxX) / 2,
        (minY + maxY) / 2,
        zoom,
      );
      return { ...center, zoom };
    }
  }

  const centerLatitude = markers.reduce((sum, marker) => sum + marker.latitude, 0) / markers.length;
  const centerLongitude = markers.reduce((sum, marker) => sum + marker.longitude, 0) / markers.length;
  return { latitude: centerLatitude, longitude: centerLongitude, zoom: MIN_ZOOM };
}

export function wrappedTileX(tileX, worldTiles) {
  return ((tileX % worldTiles) + worldTiles) % worldTiles;
}

export function nearestWorldPixelX(x, centerX, worldPixels) {
  return x - Math.round((x - centerX) / worldPixels) * worldPixels;
}
