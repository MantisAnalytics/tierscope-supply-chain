// Small API responses shaped like the reference graph (ids from the DMAuto template).

export const stats = (pending = 9) => ({
  nodes: { Supplier: 254, Site: 42, Component: 453, Assembly: 8, RiskIndicator: 401 },
  edges: { SUPPLIES: 160, DELIVERED_TO: 160, CONTAINS: 545 },
  proposals: pending ? { mapping: pending } : {},
  version: 1,
  review_threshold: 0.9,
});

const journal = [{ id: 1, ts: "2026-10-29T18:53:39+00:00", actor: "ingest", action: "ingest", summary: "ingest DMAuto.xlsx: 10223 applied", items: 10223, undone_by: null }];

export const overviewPending = {
  version: 1,
  total_revenue_eur: null,
  exposure_available: false,
  revenue_proposals: [8],
  counts: { assemblies: 8, suppliers: 254, sites: 42, components: 453, single_source_components: 85 },
  pending_proposals: { mapping: 9 },
  pending_total: 9,
  top_suppliers_by_impact: [],
  top_suppliers_by_exposure: [],
  top_sites_by_exposure: [],
  latest_journal: journal,
};

export const overviewAccepted = {
  ...overviewPending,
  version: 2,
  total_revenue_eur: 2_400_000_000,
  exposure_available: true,
  revenue_proposals: [],
  pending_proposals: { mapping: 8 },
  pending_total: 8,
  top_suppliers_by_impact: [{ type: "Supplier", id: "S100228", name: "Kessler Antriebe GmbH", exposure_pct: 41.2, impact_eur: 18_400_000 }],
  top_suppliers_by_exposure: [{ type: "Supplier", id: "S100228", name: "Kessler Antriebe GmbH", exposure_pct: 41.2, impact_eur: 18_400_000 }],
  top_sites_by_exposure: [{ type: "Site", id: "DMA-DE-01", name: "Werk Stuttgart", exposure_pct: 55.0, impact_eur: null }],
  latest_journal: [{ id: 2, ts: "2026-10-29T19:00:00+00:00", actor: "ana", action: "accept", summary: "accept #8", items: 8, undone_by: null }, ...journal],
};

const facility = { risk_flags: 0, single_source_parts: 0 };

export const network = {
  version: 2,
  exposure_available: true,
  entities: [
    { ...facility, type: "Supplier", id: "S100228", name: "Kessler Antriebe GmbH", country: "Germany", latitude: 48.8, longitude: 9.2, tier: 1, exposure_pct: 41.2, impact_eur: 18_400_000, risk_flags: 2, single_source_parts: 1 },
    { ...facility, type: "Supplier", id: "S523036", name: "Orbis Plastics", country: "Poland", latitude: 52.2, longitude: 21.0, tier: 1, exposure_pct: 12.5, impact_eur: 2_100_000 },
    { ...facility, type: "Supplier", id: "T2-DM001", name: "Feinguss Weber", country: "Czechia", latitude: null, longitude: null, tier: 2, exposure_pct: 3.1, impact_eur: 400_000 },
    { ...facility, type: "Site", id: "DMA-DE-01", name: "Werk Stuttgart", country: "Germany", latitude: 48.78, longitude: 9.18, tier: null, exposure_pct: 55.0, impact_eur: null, single_source_parts: 1 },
  ],
  supply_links: [
    {
      supplier: "S100228",
      site: "DMA-DE-01",
      parts: [
        { id: "DMC-9948", description: "Gear housing", annual_spend: 1_200_000, single_source: true, exposure_pct: 21.4 },
        { id: "DMC-1203", description: "Shaft seal", annual_spend: 85_000, single_source: false, exposure_pct: 3.2 },
      ],
    },
  ],
  tier_links: [{ supplier: "T2-DM001", parent: "S100228" }],
};

export const searchHits = [{ type: "Supplier", id: "S100228", name: "Kessler Antriebe GmbH", score: 0.9 }];

export const schema = {
  core: {
    entities: {
      Site: { identity: ["id"], fields: { name: "str", country: "str", recovery_time_months: "float" } },
      Supplier: { identity: ["id"], fields: { name: "str", country: "str", tier: "int" } },
      Component: { identity: ["id"], fields: { description: "str", single_source: "bool" } },
      Assembly: { identity: ["id"], fields: { revenue_eur: "float" } },
    },
    relations: {
      CONTAINS: { from: "Component", to: "Component", identity: { assembly: "Assembly" }, fields: { quantity: "float" }, occurrence_fields: ["quantity"] },
      SUPPLIES: { from: "Supplier", to: "Component", identity: { site: "Site" }, fields: {}, occurrence_fields: [] },
      DELIVERED_TO: { from: "Component", to: "Site", identity: {}, fields: {}, occurrence_fields: [] },
    },
  },
};

const src = (extra: Record<string, unknown> = {}) => ({
  source: { file: "DMAuto.xlsx", sheet: "Suppliers", row: 5, column: "B", ...extra },
  label: "DMAuto.xlsx Suppliers!B5",
  quote: null,
  evidence_id: null,
  author: null,
});

export const fieldValue = (value: unknown, extra: Record<string, unknown> = {}) => ({
  value,
  confidence: 1,
  method: "mapping",
  status: "active",
  review: "accepted",
  active: true,
  locked: false,
  proposal_id: null,
  resolution: null,
  origin: null,
  ...src(),
  ...extra,
});

export const supplierNode = {
  type: "Supplier",
  id: "S100228",
  referenced_only: false,
  sources: [{ ...src(), method: "mapping", confidence: 1, resolution: null }],
  fields: {
    name: [fieldValue("Kessler Antriebe GmbH")],
    country: [fieldValue("Germany")],
    supplies_material: [
      fieldValue("cast aluminium housings", {
        method: "llm",
        confidence: 0.82,
        source: { file: "supplier_letter.pdf", page: 2, lines: [14, 16], quote: "we deliver cast aluminium housings" },
        label: "supplier_letter.pdf p. 2",
        quote: "we deliver cast aluminium housings",
      }),
    ],
  },
  edges: [
    { id: 11, type: "SUPPLIES", direction: "out", key: ["S100228", "DMC-9948", null], other: { type: "Component", id: "DMC-9948", name: "Gear housing", values: { description: "Gear housing" } }, identity: { site: null }, fields: {}, sources: [] },
    ...[
      ["Geography", "Supplier region", "Eastern Asia", "ERP sourcing (2026-08-19 extract)", "2026-08-19"],
      ["Financial", "Credit rating", "BB-", "Rating agency report", "2026-09-30"],
    ].map(([category, indicator, value, source_text, last_updated], i) => ({
      id: 20 + i,
      type: "HAS_RISK",
      direction: "out",
      key: ["S100228", `S100228::${category}::${indicator}`],
      other: { type: "RiskIndicator", id: `S100228::${category}::${indicator}`, name: indicator, values: { supplier: "S100228", category, indicator, value, source_text, last_updated } },
      identity: {},
      fields: {},
      sources: [],
    })),
  ],
};

const edge = (id: number, type: string, direction: string, other: { type: string; id: string; name?: string; values?: Record<string, unknown> }, extra: Record<string, unknown> = {}) => ({
  id, type, direction, key: [], identity: {}, fields: {}, sources: [], ...extra,
  other: { name: other.id, values: {}, ...other },
});

/** A part supplied by Kessler to Werk Stuttgart, used in one assembly. */
export const componentNode = {
  type: "Component",
  id: "DMC-9948",
  referenced_only: false,
  sources: [],
  fields: { description: [fieldValue("Gear housing")], single_source: [fieldValue(true)] },
  edges: [
    edge(11, "SUPPLIES", "in", { type: "Supplier", id: "S100228", name: "Kessler Antriebe GmbH" }, { identity: { site: "DMA-DE-01" } }),
    edge(12, "DELIVERED_TO", "out", { type: "Site", id: "DMA-DE-01", name: "Werk Stuttgart" }),
  ],
};

/** An assembly whose BOM is DMC-2221 > DMC-5010 > DMC-9948 (and DMC-5011 beside DMC-5010). */
export const assemblyNode = {
  type: "Assembly",
  id: "DMP3552717",
  referenced_only: false,
  sources: [],
  fields: { revenue_eur: [fieldValue(990_000_000)] },
  edges: [],
  root: "DMC-2221",
  components: ["DMC-2221", "DMC-5010", "DMC-5011", "DMC-9948"],
};

const contains = (id: number, child: string, description: string, quantity: number) =>
  edge(id, "CONTAINS", "out", { type: "Component", id: child, name: description, values: { description } }, {
    identity: { assembly: "DMP3552717" },
    fields: { quantity: [fieldValue(quantity)] },
  });

export const bomNodes = {
  "DMC-2221": { ...componentNode, id: "DMC-2221", fields: { description: [fieldValue("Drive unit")] }, edges: [contains(31, "DMC-5010", "Gearbox", 1), contains(32, "DMC-5011", "Motor", 1), contains(39, "DMC-7777", "Other assembly part", 1)].map((e, i) => (i === 2 ? { ...e, identity: { assembly: "DMP0000001" } } : e)) },
  "DMC-5010": { ...componentNode, id: "DMC-5010", fields: { description: [fieldValue("Gearbox")] }, edges: [contains(33, "DMC-9948", "Gear housing", 2)] },
  "DMC-5011": { ...componentNode, id: "DMC-5011", fields: { description: [fieldValue("Motor")] }, edges: [] },
};

export const siteNode = (recovery: unknown[]) => ({
  type: "Site",
  id: "DMA-DE-01",
  referenced_only: false,
  sources: [],
  fields: { name: [fieldValue("Werk Stuttgart")], recovery_time_months: recovery },
  edges: [],
});

export const supplierExposure = {
  type: "Supplier",
  id: "S100228",
  business_exposure_pct: 41.2,
  assemblies: ["DMP3552717"],
  components: ["DMC-9948"],
  adjusted_pct: null,
  recovery_time_months: 6,
  business_impact_eur: 18_400_000,
  total_revenue_eur: 2_400_000_000,
  impacted_assemblies: [{ id: "DMP3552717", revenue_eur: 990_000_000, paths: [["DMC-2221", "DMC-5010", "DMC-9948"]] }],
};

export const componentExposure = {
  ...supplierExposure,
  type: "Component",
  id: "DMC-9948",
  business_impact_eur: null,
  recovery_time_months: null,
};

const impact = (extra: Record<string, unknown> = {}) => ({
  version: 1,
  no_effect: true,
  depends_on: [],
  changed: 0,
  max_impact_eur: 0,
  max_exposure_pp: 0,
  effects: [],
  ...extra,
});

export const revenueImpact = impact({
  no_effect: false,
  changed: 340,
  max_exposure_pp: 88.25,
  effects: [{ type: "Supplier", id: "S100228", exposure_pp: 41.2, impact_eur: 0, exposure_pct_before: 0, exposure_pct_after: 41.2, impact_eur_before: null, impact_eur_after: null }],
});

export const proposal = (id: number, summary: string, extra: Record<string, unknown> = {}) => ({
  id,
  kind: "mapping",
  group_key: ["spec", "mappings/generated/x__sites_data.json", "sites_data", summary],
  status: "pending",
  summary: `column '${summary}' of region sites_data`,
  created_run: "r1",
  created_at: "2026-10-29T18:53:39+00:00",
  decided_by: null,
  decided_at: null,
  comment: null,
  impact_version: 1,
  notes: [],
  impact: impact(),
  facts: 8,
  confidence: [0.8, 0.8],
  ...extra,
});

export const queue = [
  proposal(8, "Weight", { group_key: ["spec", "mappings/generated/x__weights_assemblies.json", "weights_assemblies", "Weight"], impact: revenueImpact }),
  proposal(2, "Recovery Time", { facts: 15, impact: impact({ depends_on: [8] }) }),
  proposal(3, "Photo", { facts: 254, confidence: [0.7, 0.7] }),
];

export const fact = (item: string, value: unknown, extra: Record<string, unknown> = {}) => ({
  kind: "field",
  type: "Assembly",
  key: [item],
  item: `Assembly ${item}`,
  field: "revenue_eur",
  value,
  confidence: 0.8,
  method: "mapping",
  source: "DMAuto.xlsx Weights!C4",
  location: { file: "DMAuto.xlsx", sheet: "Weights", row: 4, column: "C" },
  quote: null,
  review: "proposed",
  resolution: null,
  target: "Assembly.revenue_eur",
  current: null,
  ...extra,
});

export const detail = (p: ReturnType<typeof proposal>, extra: Record<string, unknown> = {}) => ({
  ...p,
  facts: [fact("DMP3552717", 990_000_000), fact("DMP1893848", 410_000_000)],
  count: 8,
  targets: ["Assembly.revenue_eur"],
  spec: { path: "mappings/generated/x__weights_assemblies.json", region: "weights_assemblies", column: "Weight", target: "Assembly.revenue_eur", confidence: 0.8, rationale: "Revenue per assembly in EUR", role: "fact" },
  ...extra,
});

export const decision = (id: number, actor: string) => ({
  journal: { id: 2, ts: "2026-10-29T19:00:00+00:00", actor, action: "accept", summary: `accept #${id}`, items: 8, undone_by: null },
  proposal: { ...proposal(id, "Weight"), status: "accepted", decided_by: actor },
  warnings: [],
  proposals_created: [],
});

export const opsSummary = (active: unknown[] = []) => ({ active, open_tasks: 1, in_progress_tasks: 2, overdue_tasks: 0, tasks: [], agent_needs: 0 });

export const opsMeta = {
  types: [{ id: "supplier_outage", label: "Supplier outage" }], actions: [], rules: {}, countries: { suppliers: [], sites: [] },
  continents: [], commodities: [], suppliers: [], sites: [], assemblies: [], shockbench_episodes: [],
  model: { provider: "meta", model: "muse-spark-1.1" }, margin: 0.19,
};

export const fire = { id: 3, name: "Fire at Kessler Antriebe", type: "supplier_outage", do_nothing_cost: 33_900_000, plan_cost: null };

/** Everything the Overview loads, with revenue accepted. */
export const overviewApi = (extra: Record<string, unknown> = {}) => ({
  "GET /api/stats": stats(8),
  "GET /api/overview": overviewAccepted,
  "GET /api/network": network,
  "GET /api/ops/summary": opsSummary(),
  "GET /api/schema": schema,
  "GET /api/search": searchHits,
  "GET /api/nodes/Supplier/S100228": supplierNode,
  "GET /api/exposure/Supplier/S100228": supplierExposure,
  ...extra,
});
