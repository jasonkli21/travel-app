import assert from "node:assert/strict";
import test from "node:test";

import {
  fitMarkers,
  nearestWorldPixelX,
  project,
  unproject,
  wrappedTileX,
} from "../lib/map-geometry.mjs";

test("map coordinates project and unproject without changing the location", () => {
  const projected = project(37.7749, -122.4194, 12);
  const restored = unproject(projected.x, projected.y, 12);

  assert.ok(Math.abs(restored.latitude - 37.7749) < 1e-8);
  assert.ok(Math.abs(restored.longitude - -122.4194) < 1e-8);
});

test("map fitting keeps markers on both sides of the date line close together", () => {
  const view = fitMarkers(
    [
      { latitude: 0, longitude: 179 },
      { latitude: 0, longitude: -179 },
    ],
    { width: 640, height: 420 },
  );

  assert.ok(view.zoom >= 2);
  assert.ok(Math.abs(Math.abs(view.longitude) - 180) < 1);
  const center = project(view.latitude, view.longitude, view.zoom);
  const otherSide = project(0, -179, view.zoom);
  const wrapped = nearestWorldPixelX(otherSide.x, center.x, 256 * 2 ** view.zoom);
  assert.ok(Math.abs(wrapped - center.x) < 256 * 2 ** view.zoom / 4);
});

test("tile X wraps at either edge of the world", () => {
  assert.equal(wrappedTileX(-1, 4), 3);
  assert.equal(wrappedTileX(4, 4), 0);
});
