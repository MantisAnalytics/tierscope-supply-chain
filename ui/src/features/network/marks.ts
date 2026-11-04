import type { NetworkEntity } from "@/api/client";
import { TYPE_COLORS } from "@/components/TypeIcon";
import type { Slice } from "./filters";

// Marker colour shared by the map and the graph: exposure on a sequential slate-blue -> rose ramp (rose is the risk
// role). Three stops taken from the OKLCH path between the ends, so the middle stays clear instead of muddy grey.
const STOPS = [
  [0x8f, 0xa3, 0xc7],
  [0xab, 0x76, 0xc6],
  [0xd6, 0x33, 0x6c],
];
export const RISK_HALO = "#d6336c";

/** Facility type is told by shape and outline, never by fill: sites are rounded squares, suppliers circles. */
export const TYPE_OUTLINE: Record<string, string> = { Site: TYPE_COLORS.Site, Supplier: TYPE_COLORS.Supplier };

/** Sequential slate-blue -> rose by t in [0, 1]; neutral grey when exposure is unavailable. */
export function exposureColour(t: number | null): string {
  if (t === null) return "#9a98a3";
  const x = Math.min(1, Math.max(0, t)) * (STOPS.length - 1);
  const i = Math.min(STOPS.length - 2, Math.floor(x));
  const c = STOPS[i].map((lo, j) => Math.round(lo + (STOPS[i + 1][j] - lo) * (x - i)));
  return `rgb(${c[0]}, ${c[1]}, ${c[2]})`;
}

/** Fill of each facility relative to the most exposed one shown. */
export function colourer(entities: NetworkEntity[], available: boolean) {
  const max = Math.max(1e-9, ...entities.map((e) => e.exposure_pct ?? 0));
  return (e: NetworkEntity) => exposureColour(available ? (e.exposure_pct ?? 0) / max : null);
}

/** What the map and the facility graph both take: the same slice, selection and callbacks. */
export interface FacilityCanvasProps {
  slice: Slice;
  exposureAvailable: boolean;
  selected: string | null; // entity key
  link: string | null; // link key
  highlight: Set<string> | null; // entity keys to keep bright (the selection's chain, a part's facilities); others fade
  onSelect: (type: string, id: string) => void;
  onSelectLink: (supplier: string, site: string) => void;
  onClear: () => void;
}
