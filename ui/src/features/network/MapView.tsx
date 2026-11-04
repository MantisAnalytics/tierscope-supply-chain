import { geoPath } from "d3-geo";
import { select } from "d3-selection";
import { zoom, zoomIdentity, zoomTransform, type ZoomBehavior } from "d3-zoom";
import type { FeatureCollection, Geometry } from "geojson";
import { Minus, Plus, RotateCcw } from "lucide-react";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { feature } from "topojson-client";
import type { Topology } from "topojson-specification";
import countries from "world-atlas/countries-110m.json";
import type { NetworkEntity } from "@/api/client";
import { Button } from "@/components/ui/button";
import { formatEur, formatPct } from "@/lib/utils";
import { entityKey, linkKey, metricValue, sizer } from "./filters";
import { LinkTooltip, type LinkHover } from "./LinkTooltip";
import { openingProjection, RING_ZOOM, ringOffset, sharedGroups, worldBounds } from "./mapGeometry";
import { colourer, exposureColour, RISK_HALO, TYPE_OUTLINE, type FacilityCanvasProps } from "./marks";

const DETAIL_ZOOM = 4; // finer country outlines from this zoom on
export const SCALE_EXTENT: [number, number] = [0.6, 48];
const FALLBACK = { width: 960, height: 470 }; // before the container is measured

const toLand = (topology: unknown) =>
  feature(topology as Topology, (topology as Topology).objects.countries) as unknown as FeatureCollection<Geometry>;
const coarse = toLand(countries);
let fine: Promise<FeatureCollection<Geometry>> | null = null;
/** The 50m outlines, loaded once, in their own chunk. */
const loadFine = () => (fine ??= import("world-atlas/countries-50m.json").then((m) => toLand(m.default)));

const later = (f: () => void) => (typeof requestAnimationFrame === "function" ? requestAnimationFrame(f) : window.setTimeout(f, 16));
const cancel = (id: number) => (typeof cancelAnimationFrame === "function" ? cancelAnimationFrame(id) : window.clearTimeout(id));

interface Placed {
  e: NetworkEntity;
  x: number;
  y: number;
  r: number; // on-screen radius in px, whatever the zoom
  fill: string;
}

/** Bow a link a little so links between the same places stay apart from straight borders. */
function arc(a: Placed, b: Placed): string {
  const mx = (a.x + b.x) / 2;
  const my = (a.y + b.y) / 2 - Math.hypot(b.x - a.x, b.y - a.y) * 0.15;
  return `M${a.x},${a.y} Q${mx},${my} ${b.x},${b.y}`;
}

/** The facilities of the slice placed by coordinates, with supply and tier links; those without coordinates are
 *  counted. The map fills its container and opens on the area of `facilities` (the whole network, so a selection
 *  does not move it). Markers keep their on-screen size while zooming; facilities at one point fan out when close. */
export default function MapView({ slice, facilities, exposureAvailable, selected, link, highlight, onSelect, onSelectLink, onClear, onShowGraph }: FacilityCanvasProps & {
  facilities?: NetworkEntity[];
  onShowGraph: () => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const groupRef = useRef<SVGGElement>(null);
  const zoomRef = useRef<ZoomBehavior<SVGSVGElement, unknown> | null>(null);
  const [size, setSize] = useState(FALLBACK);
  const [scale, setScale] = useState(1);
  const [detail, setDetail] = useState<FeatureCollection<Geometry> | null>(null);
  const [hover, setHover] = useState<string | null>(null);
  const [linkHover, setLinkHover] = useState<LinkHover | null>(null);

  // The SVG takes the container's pixel size, so it never letterboxes.
  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    const measure = () => {
      const [width, height] = [el.clientWidth, el.clientHeight];
      if (width > 0 && height > 0) setSize((s) => (s.width === width && s.height === height ? s : { width, height }));
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const placeable = facilities ?? slice.entities;
  const projection = useMemo(() => {
    const points = placeable.filter((e) => e.latitude != null && e.longitude != null).map((e) => [e.longitude!, e.latitude!] as [number, number]);
    return openingProjection(points, size.width, size.height);
  }, [placeable, size]);

  useEffect(() => {
    if (scale >= DETAIL_ZOOM && !detail) loadFine().then(setDetail, () => {}); // keep the 110m outlines on failure
  }, [scale >= DETAIL_ZOOM, detail]); // eslint-disable-line react-hooks/exhaustive-deps
  const land = detail && scale >= DETAIL_ZOOM ? detail : coarse;
  const shapes = useMemo(() => {
    const path = geoPath(projection);
    // vector-effect is not inherited, so each outline keeps its hairline width at any zoom
    return land.features.map((f, i) => <path key={i} d={path(f) ?? undefined} vectorEffect="non-scaling-stroke" />);
  }, [projection, land]);

  const layout = useMemo(() => {
    const at = new Map<string, Placed>();
    const size = sizer(slice.entities, slice.metric);
    const fill = colourer(slice.entities, exposureAvailable);
    for (const e of slice.entities) {
      if (e.latitude == null || e.longitude == null) continue;
      const p = projection([e.longitude, e.latitude]);
      if (!p) continue;
      at.set(entityKey(e), { e, x: p[0], y: p[1], r: 2.5 + size(e) * 8, fill: fill(e) });
    }
    const supply = slice.supply.flatMap((l) => {
      const a = at.get(`Supplier:${l.supplier}`);
      const b = at.get(`Site:${l.site}`);
      return a && b ? [{ key: linkKey(l), l, a: entityKey(a.e), b: entityKey(b.e), d: arc(a, b) }] : [];
    });
    const tier = slice.tier.flatMap((l) => {
      const a = at.get(`Supplier:${l.supplier}`);
      const b = at.get(`Supplier:${l.parent}`);
      return a && b ? [{ key: `${l.supplier}^${l.parent}`, a: entityKey(a.e), b: entityKey(b.e), d: arc(a, b) }] : [];
    });
    // larger markers first, so smaller ones are drawn on top and stay clickable
    const order = [...at.entries()].sort(([ka, a], [kb, b]) => b.r - a.r || ka.localeCompare(kb));
    const groups = sharedGroups([...at.entries()].map(([key, p]) => ({ key, x: p.x, y: p.y })));
    const groupMax = new Map<string, number>();
    for (const [key, g] of groups) groupMax.set(`${g.x},${g.y}`, Math.max(groupMax.get(`${g.x},${g.y}`) ?? 0, at.get(key)!.r));
    return { at, supply, tier, order, groups, groupMax, unplaced: slice.entities.length - at.size };
  }, [slice, exposureAvailable, projection]);
  const names = useMemo(() => new Map(slice.entities.map((e) => [entityKey(e), e])), [slice]);

  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    let frame = 0;
    const behaviour = zoom<SVGSVGElement, unknown>()
      .scaleExtent(SCALE_EXTENT)
      .extent([[0, 0], [size.width, size.height]])
      .translateExtent(worldBounds(projection))
      .on("zoom", (event) => {
        groupRef.current?.setAttribute("transform", event.transform.toString());
        cancel(frame);
        frame = later(() => setScale(event.transform.k)); // marker sizes follow once per frame
      });
    zoomRef.current = behaviour;
    select(svg).call(behaviour);
    return () => {
      cancel(frame);
      select(svg).on(".zoom", null);
    };
  }, [size, projection]);

  // Centre the selected facility, keeping the zoom.
  const chosen = selected ? layout.at.get(selected) : undefined;
  useEffect(() => {
    if (!chosen || !svgRef.current || !zoomRef.current) return;
    try {
      zoomRef.current.translateTo(select(svgRef.current), chosen.x, chosen.y);
    } catch {
      // no layout (tests): nothing to centre
    }
  }, [chosen]);

  // Hovering shows a facility's neighbours; otherwise the highlight (the selection's chain) stays bright.
  let bright = highlight;
  if (hover) {
    bright = new Set([hover]);
    for (const l of [...layout.supply, ...layout.tier]) if (l.a === hover || l.b === hover) bright.add(l.a).add(l.b);
  }
  const both = (l: { a: string; b: string }) => bright !== null && bright.has(l.a) && bright.has(l.b);
  const shown = hover ? layout.at.get(hover) : undefined;
  const shownGroup = hover ? layout.groups.get(hover) : undefined;
  const k = scale;
  const fanned = k >= RING_ZOOM;
  // The buttons zoom about the selected facility, which the pan limits may keep from the centre (one near the top of
  // the world, for instance); without a selection, about the centre.
  const zoomBy = (factor: number) => {
    const svg = svgRef.current;
    if (!svg || !zoomRef.current) return;
    zoomRef.current.scaleBy(select(svg), factor, chosen ? zoomTransform(svg).apply([chosen.x, chosen.y]) : undefined);
  };
  const hoverAt = (event: React.MouseEvent) => {
    const r = box.current?.getBoundingClientRect();
    return { x: event.clientX - (r?.left ?? 0), y: event.clientY - (r?.top ?? 0) };
  };

  return (
    <div ref={box} className="relative h-full w-full overflow-hidden" data-testid="map-view">
      <svg
        ref={svgRef}
        viewBox={`0 0 ${size.width} ${size.height}`}
        className="block h-full w-full cursor-grab bg-surface active:cursor-grabbing"
        role="img"
        aria-label={`World map with ${layout.at.size} sites and suppliers`}
        onClick={(e) => (e.target === svgRef.current || (e.target as Element).closest("[data-testid=countries]")) && onClear()}
      >
        <g ref={groupRef}>
          <g fill="var(--map-land)" stroke="var(--map-line)" strokeWidth={0.5} data-testid="countries">
            {shapes}
          </g>
          <g fill="none" stroke="var(--muted)" strokeLinecap="round" strokeDasharray={`${3 / k} ${3 / k}`}>
            {layout.tier.map((l) => {
              const on = both(l);
              return <path key={l.key} d={l.d} strokeWidth={(on ? 1.4 : 0.7) / k} strokeOpacity={bright ? (on ? 0.8 : 0.05) : 0.25} />;
            })}
          </g>
          <g fill="none" stroke="var(--interactive)" strokeLinecap="round">
            {layout.supply.map((l) => {
              const on = l.key === link || both(l);
              return (
                <g key={l.key}>
                  <path d={l.d} strokeWidth={(l.key === link ? 2.4 : on ? 1.6 : 0.8) / k} strokeOpacity={l.key === link ? 1 : bright || link ? (on ? 0.85 : 0.06) : 0.22} />
                  {/* a wider invisible stroke makes thin links easy to hover and click */}
                  <path
                    d={l.d}
                    stroke="transparent"
                    strokeWidth={10 / k}
                    pointerEvents="stroke"
                    className="cursor-pointer"
                    data-testid={`link-${l.key}`}
                    role="button"
                    aria-label={`Supply link ${l.l.supplier} to ${l.l.site}`}
                    onMouseMove={(e) => setLinkHover({ link: l.l, ...hoverAt(e) })}
                    onMouseEnter={(e) => setLinkHover({ link: l.l, ...hoverAt(e) })}
                    onMouseLeave={() => setLinkHover(null)}
                    onClick={() => onSelectLink(l.l.supplier, l.l.site)}
                  />
                </g>
              );
            })}
          </g>
          <g>
            {layout.order.map(([id, m]) => {
              const dim = bright !== null && !bright.has(id);
              const r = m.r / k;
              const group = fanned ? layout.groups.get(id) : undefined;
              const [dx, dy] = group ? ringOffset(group.index, group.size, layout.groupMax.get(`${group.x},${group.y}`)!, k) : [0, 0];
              const [x, y] = [m.x + dx, m.y + dy];
              const isSelected = id === selected;
              const common = {
                fill: m.fill,
                stroke: isSelected ? "var(--interactive)" : TYPE_OUTLINE[m.e.type],
                strokeWidth: (isSelected ? 2.5 : 1.5) / k,
                opacity: dim ? 0.25 : 0.95,
                className: "cursor-pointer",
                "data-testid": `marker-${id}`,
                "data-selected": isSelected || undefined,
                "data-dim": dim || undefined,
                role: "button",
                "aria-label": `${m.e.type} ${m.e.name}`,
                "aria-pressed": isSelected,
                onMouseEnter: () => setHover(id),
                onMouseLeave: () => setHover(null),
                onClick: () => onSelect(m.e.type, m.e.id),
              };
              return (
                <g key={id}>
                  {group && <line x1={m.x} y1={m.y} x2={x} y2={y} stroke="var(--muted)" strokeWidth={0.75 / k} opacity={dim ? 0.2 : 0.6} data-testid={`ring-${id}`} />}
                  {m.e.risk_flags > 0 && <circle cx={x} cy={y} r={r + 3 / k} fill={RISK_HALO} opacity={dim ? 0.08 : 0.28} data-testid={`risk-${id}`} />}
                  {m.e.type === "Site" ? (
                    <rect x={x - r * 0.89} y={y - r * 0.89} width={r * 1.78} height={r * 1.78} rx={r * 0.45} {...common} />
                  ) : (
                    <circle cx={x} cy={y} r={r} {...common} />
                  )}
                </g>
              );
            })}
          </g>
        </g>
      </svg>
      {shown && !linkHover && (
        <div role="tooltip" className="pointer-events-none absolute top-3 left-3 w-60 rounded-lg border bg-surface p-3 text-xs shadow-lg">
          <div className="truncate text-sm font-medium">{shown.e.name}</div>
          <div className="text-muted">{shown.e.type} · {shown.e.id}{shown.e.country ? ` · ${shown.e.country}` : ""}</div>
          {shownGroup && (
            <div className="mt-0.5 text-muted">Same location as {shownGroup.size - 1} {shownGroup.size === 2 ? "other" : "others"}</div>
          )}
          <dl className="mt-1.5 grid grid-cols-2 gap-y-0.5">
            <dt className="text-muted">Exposure</dt>
            <dd className="text-right tabular">{exposureAvailable ? formatPct(shown.e.exposure_pct, 2) : "unavailable"}</dd>
            {shown.e.type === "Supplier" && (
              <>
                <dt className="text-muted">Business Impact</dt>
                <dd className="text-right text-risk tabular">{formatEur(metricValue(shown.e, "impact"))}</dd>
                <dt className="text-muted">Tier</dt>
                <dd className="text-right">{shown.e.tier ?? "—"}</dd>
              </>
            )}
            {shown.e.risk_flags > 0 && (
              <>
                <dt className="text-muted">Risk flags</dt>
                <dd className="text-right tabular">{shown.e.risk_flags}</dd>
              </>
            )}
          </dl>
        </div>
      )}
      {linkHover && <LinkTooltip hover={linkHover} names={names} />}
      <div className="absolute right-3 bottom-3 flex flex-col gap-1">
        <Button size="icon" variant="outline" className="size-7" aria-label="Zoom in" onClick={() => zoomBy(1.6)}><Plus /></Button>
        <Button size="icon" variant="outline" className="size-7" aria-label="Zoom out" onClick={() => zoomBy(1 / 1.6)}><Minus /></Button>
        <Button size="icon" variant="outline" className="size-7" aria-label="Reset view" onClick={() => svgRef.current && zoomRef.current?.transform(select(svgRef.current), zoomIdentity)}><RotateCcw /></Button>
      </div>
      <div className="absolute bottom-3 left-3 flex items-center gap-3 rounded-md bg-surface/90 px-2 py-1 text-xs text-muted">
        <span className="flex items-center gap-1"><svg width="12" height="12" aria-hidden><circle cx="6" cy="6" r="4.5" fill={exposureColour(0.5)} stroke={TYPE_OUTLINE.Supplier} strokeWidth="1.5" /></svg> supplier</span>
        <span className="flex items-center gap-1"><svg width="12" height="12" aria-hidden><rect x="2" y="2" width="8" height="8" rx="2" fill={exposureColour(0.5)} stroke={TYPE_OUTLINE.Site} strokeWidth="1.5" /></svg> site</span>
        <span className="flex items-center gap-1"><span className="h-2 w-10 rounded-sm" style={{ background: `linear-gradient(90deg, ${exposureColour(0)}, ${exposureColour(1)})` }} /> exposure</span>
        <span className="flex items-center gap-1"><svg width="12" height="12" aria-hidden><circle cx="6" cy="6" r="6" fill={RISK_HALO} opacity="0.3" /><circle cx="6" cy="6" r="3" fill={exposureColour(0.5)} /></svg> risk flags</span>
      </div>
      {layout.unplaced > 0 && (
        <div role="status" data-testid="missing-coordinates" className="absolute top-3 right-3 flex items-center gap-2 rounded-md border bg-surface px-3 py-1.5 text-xs shadow-sm">
          {layout.at.size === 0
            ? `None of the ${layout.unplaced} facilities has coordinates.`
            : `${layout.unplaced} ${layout.unplaced === 1 ? "facility has" : "facilities have"} no coordinates.`}
          <button className="cursor-pointer font-medium text-interactive hover:underline" onClick={onShowGraph}>Show graph</button>
        </div>
      )}
    </div>
  );
}
