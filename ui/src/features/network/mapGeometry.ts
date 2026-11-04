import { geoNaturalEarth1, geoPath, type GeoProjection } from "d3-geo";
import type { MultiPoint } from "geojson";

const PAD = 0.08; // of the container on each side
const MIN_SPAN = [12, 7]; // degrees of longitude and latitude the opening view shows at least
// Land without Antarctica and the polar seas, for a network with no placed facility.
const WORLD: MultiPoint = { type: "MultiPoint", coordinates: [[-180, -60], [-180, 0], [-180, 85], [0, -60], [0, 85], [180, -60], [180, 0], [180, 85]] };

/** The opening projection (design D5): the bounding box of the placed facilities, padded by 8% and widened to the
 *  container's aspect ratio by the fit; without facilities, the land between 60°S and 85°N. */
export function openingProjection(points: [number, number][], width: number, height: number): GeoProjection {
  let target = WORLD;
  if (points.length) {
    const lons = points.map((p) => p[0]);
    const lats = points.map((p) => p[1]);
    const box = [Math.min(...lons), Math.min(...lats), Math.max(...lons), Math.max(...lats)];
    for (const axis of [0, 1]) {
      const grow = Math.max(0, MIN_SPAN[axis] - (box[axis + 2] - box[axis])) / 2;
      box[axis] -= grow;
      box[axis + 2] += grow;
    }
    const [w, s, e, n] = [Math.max(-180, box[0]), Math.max(-85, box[1]), Math.min(180, box[2]), Math.min(85, box[3])];
    target = { type: "MultiPoint", coordinates: [[w, s], [w, n], [e, s], [e, n], [(w + e) / 2, s], [(w + e) / 2, n]] };
  }
  const [px, py] = [width * PAD, height * PAD];
  return geoNaturalEarth1().fitExtent([[px, py], [width - px, height - py]], target);
}

/** The projected bounds of the whole globe: how far the map may pan. */
export function worldBounds(projection: GeoProjection): [[number, number], [number, number]] {
  return geoPath(projection).bounds({ type: "Sphere" });
}

export interface Point {
  key: string;
  x: number;
  y: number;
}

/** Facilities at the same projected point (within `tolerance` px at k = 1): each member's place in its group. A
 *  facility alone at its point is left out. */
export function sharedGroups(points: Point[], tolerance = 0.5): Map<string, { index: number; size: number; x: number; y: number }> {
  const groups: Point[][] = [];
  for (const p of [...points].sort((a, b) => a.key.localeCompare(b.key))) {
    const g = groups.find((g) => Math.hypot(g[0].x - p.x, g[0].y - p.y) <= tolerance);
    if (g) g.push(p);
    else groups.push([p]);
  }
  const out = new Map<string, { index: number; size: number; x: number; y: number }>();
  for (const g of groups) if (g.length > 1) g.forEach((p, index) => out.set(p.key, { index, size: g.length, x: g[0].x, y: g[0].y }));
  return out;
}

export const RING_ZOOM = 6; // shared points fan out from this zoom on

/** Where member `index` of `size` sits on the ring around a shared point, in map units at zoom `k`, so the ring has
 *  the same on-screen radius at every zoom: `rMax` (the largest marker of the group, in px) plus 4 px, or wider when
 *  that would let neighbours on the ring touch. */
export function ringOffset(index: number, size: number, rMax: number, k: number): [number, number] {
  const radius = Math.max(rMax + 4, (rMax + 1) / Math.sin(Math.PI / size)) / k;
  const angle = (2 * Math.PI * index) / size - Math.PI / 2;
  return [radius * Math.cos(angle), radius * Math.sin(angle)];
}
