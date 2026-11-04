import { useCallback, useEffect, useMemo } from "react";
import { useLocation, useNavigate } from "react-router-dom";

/** A node address in the URL: `Type:id` (the id may itself contain colons). */
export interface NodeRef {
  type: string;
  id: string;
}

/** A supply link in the URL: `supplier>site`. */
export interface LinkRef {
  supplier: string;
  site: string;
}

export type Mode = "map" | "graph";
export type Metric = "impact" | "exposure";
export type FacilityType = "Site" | "Supplier";
export type LayoutChoice = "hierarchy" | "rings";

/** The whole network view as kept in the Overview's search params. `null` means "the default". */
export interface NetworkView {
  view: Mode | null; // map when any facility has coordinates, else graph
  sel: NodeRef | null; // a site or supplier: the first crumb of the side panel
  link: LinkRef | null; // or a supply link as the first crumb
  trail: NodeRef[]; // the panel's drill-in below it: parts, assemblies, risks, and `Link` (id `supplier>site`) entries
  layout: LayoutChoice | null; // hierarchy by default
  types: FacilityType[];
  tiers: number[];
  country: string[]; // empty: every country
  risk: boolean;
  ss: boolean;
  metric: Metric | null; // impact, or exposure when impact is unavailable
  top: number | null;
}

export const FACILITY_TYPES: FacilityType[] = ["Site", "Supplier"];
export const DEFAULT_TIERS = [1];

export const DEFAULT_VIEW: NetworkView = {
  view: null,
  sel: null,
  link: null,
  trail: [],
  layout: null,
  types: FACILITY_TYPES,
  tiers: DEFAULT_TIERS,
  country: [],
  risk: false,
  ss: false,
  metric: null,
  top: null,
};

export const nodeKey = (n: NodeRef) => `${n.type}:${n.id}`;

export function parseNode(value: string | null): NodeRef | null {
  if (!value) return null;
  const at = value.indexOf(":");
  if (at <= 0 || at === value.length - 1) return null;
  return { type: value.slice(0, at), id: value.slice(at + 1) };
}

function parseLink(value: string | null): LinkRef | null {
  if (!value) return null;
  const at = value.indexOf(">");
  if (at <= 0 || at === value.length - 1) return null;
  return { supplier: value.slice(0, at), site: value.slice(at + 1) };
}

const list = (value: string | null) => (value ? value.split(",").map((s) => s.trim()).filter(Boolean) : []);
const sameSet = <T>(a: T[], b: T[]) => a.length === b.length && a.every((x) => b.includes(x));

function oneOf<T extends string>(value: string | null, allowed: readonly T[]): T | null {
  return allowed.includes(value as T) ? (value as T) : null;
}

/** The trail param from the raw query: entries are split on commas before decoding, so an id may hold a comma. */
function parseTrail(search: string): NodeRef[] {
  const raw = /(?:^|[?&])trail=([^&]*)/.exec(search)?.[1];
  if (!raw) return [];
  return raw.split(",").flatMap((part) => {
    try {
      return parseNode(decodeURIComponent(part.replace(/\+/g, " "))) ?? [];
    } catch {
      return []; // malformed escape
    }
  });
}

/** Search params to a view; unknown or malformed values fall back to their defaults. Addresses of the former Parts
 *  lens (`lens=parts&focus=X`) and a selected part, assembly or risk open that node on the trail instead. */
export function parseView(search: string): NetworkView {
  const p = new URLSearchParams(search);
  const top = Number(p.get("top"));
  const named = list(p.get("types"));
  const types = FACILITY_TYPES.filter((t) => named.includes(t));
  const tiers = [...new Set(list(p.get("tiers")).map(Number).filter((t) => Number.isInteger(t) && t >= 1 && t <= 9))].sort();
  let sel = parseNode(p.get("sel"));
  const focus = p.get("lens") === "parts" ? parseNode(p.get("focus")) : null;
  if (focus && FACILITY(focus.type)) sel ??= focus;
  const moved = [focus, sel].filter((n): n is NodeRef => !!n && !FACILITY(n.type));
  const trail = [...moved, ...parseTrail(search)].filter((n, i, all) => all.findIndex((m) => nodeKey(m) === nodeKey(n)) === i);
  if (sel && !FACILITY(sel.type)) sel = null;
  return {
    view: oneOf(p.get("view"), ["map", "graph"] as const),
    sel,
    link: parseLink(p.get("link")),
    trail,
    layout: oneOf(p.get("layout"), ["hierarchy", "rings"] as const),
    types: types.length ? types : FACILITY_TYPES,
    tiers: tiers.length ? tiers : DEFAULT_TIERS,
    country: list(p.get("country")),
    risk: p.get("risk") === "1",
    ss: p.get("ss") === "1",
    metric: oneOf(p.get("metric"), ["impact", "exposure"] as const),
    top: Number.isInteger(top) && top > 0 ? top : null,
  };
}

// Readable addresses: keep the separators the params use instead of percent-encoding them.
const readable = (value: string) => encodeURIComponent(value).replace(/%3A/gi, ":").replace(/%3E/gi, ">");
const encode = (value: string) => readable(value).replace(/%2C/gi, ",");

/** A view to a search string ("" or "?…"), leaving out every default. */
export function serialiseView(v: NetworkView): string {
  const out: string[] = [];
  const put = (key: string, value: string | null | false) => value && out.push(`${key}=${encode(value)}`);
  put("view", v.view);
  put("sel", v.sel && nodeKey(v.sel));
  put("link", v.link && `${v.link.supplier}>${v.link.site}`);
  // each trail entry is encoded on its own (a comma in an id becomes %2C), then joined by readable commas
  if (v.trail.length) out.push(`trail=${v.trail.map((n) => readable(nodeKey(n))).join(",")}`);
  put("layout", v.layout);
  put("types", !sameSet(v.types, FACILITY_TYPES) && v.types.join(","));
  put("tiers", !sameSet(v.tiers, DEFAULT_TIERS) && [...v.tiers].sort().join(","));
  put("country", v.country.join(","));
  put("risk", v.risk && "1");
  put("ss", v.ss && "1");
  put("metric", v.metric);
  put("top", v.top !== null && String(v.top));
  return out.length ? `?${out.join("&")}` : "";
}

/** The number of filters that differ from the default view (the closed filter button shows it). */
export function activeFilters(v: NetworkView): number {
  return [
    !sameSet(v.types, FACILITY_TYPES),
    !sameSet(v.tiers, DEFAULT_TIERS),
    v.country.length > 0,
    v.risk,
    v.ss,
    v.metric !== null,
    v.top !== null,
  ].filter(Boolean).length;
}

export const FACILITY = (type: string): type is FacilityType => FACILITY_TYPES.includes(type as FacilityType);

/** Where a node opens on the Overview: sites and suppliers selected on the network, others on the panel's trail. */
export function nodeAddress(type: string, id: string): string {
  const node = { type, id };
  return `/${serialiseView({ ...DEFAULT_VIEW, ...(FACILITY(type) ? { sel: node } : { trail: [node] }) })}`;
}

// The last Overview address, so the Overview link and a return to the page restore the view.
const STORE = "tierscope.overview";
let lastSearch: string | null = null;

export function rememberedSearch(): string {
  if (lastSearch === null) {
    try {
      lastSearch = sessionStorage.getItem(STORE) ?? "";
    } catch {
      lastSearch = "";
    }
  }
  return lastSearch;
}

export function rememberSearch(search: string): void {
  lastSearch = search;
  try {
    sessionStorage.setItem(STORE, search);
  } catch {
    // storage unavailable: the module variable still covers this session
  }
}

/** Changes that move to another place in the history (Back steps out of a drill-in); everything else replaces it. */
const PUSHED: (keyof NetworkView)[] = ["trail"];

/** The network view in the Overview's URL, and an update that keeps defaults out of the address. */
export function useNetworkView(): [NetworkView, (patch: Partial<NetworkView>) => void] {
  const { search, pathname } = useLocation();
  const navigate = useNavigate();
  const view = useMemo(() => parseView(search), [search]);
  useEffect(() => {
    if (pathname === "/") rememberSearch(serialiseView(view));
  }, [view, pathname]);
  const update = useCallback(
    (patch: Partial<NetworkView>) => {
      const next = serialiseView({ ...view, ...patch });
      if (next === serialiseView(view)) return;
      const push = PUSHED.some((k) => k in patch && JSON.stringify(patch[k]) !== JSON.stringify(view[k]));
      navigate({ pathname: "/", search: next }, { replace: !push });
    },
    [view, navigate],
  );
  return [view, update];
}
