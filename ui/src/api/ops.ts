/** Operations API: disruptions, agent runs, decisions, tasks and reports (hand-written types for /api/ops). */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { OpenTask, PlanView } from "./agent";
import { api } from "./client";

export type DisruptionType = "supplier_outage" | "export_restriction" | "route_closure" | "plant_outage" | "tariff" | "shortage";
export type ActionType = "second_source" | "new_supplier" | "spot_buy" | "air_freight" | "buffer" | "priority";

export interface Disruption {
  name: string;
  type: DisruptionType;
  start_week: number;
  duration_weeks: number;
  description: string;
  params: Record<string, unknown>;
  stock_cover_weeks: number;
  horizon_weeks: number;
  source: string;
}

export interface LibraryEntry extends Disruption {
  type_label: string;
  affected_components: number;
  do_nothing_cost: number;
  lost_revenue: number;
}

export type DisruptionStatus = "draft" | "active" | "resolved" | "archived";

/** The disruption's agent thread; `needs` says why a person is needed (plan ready, agent failed, budget). */
export interface ThreadInfo {
  id: number;
  disruption_id: number | null;
  title: string;
  status: "active" | "archived";
  needs: string | null;
  created_at: string;
  updated_at: string;
}

export interface Action {
  type: ActionType;
  component?: string;
  supplier?: string;
  share?: number;
  weeks?: number;
  start_week?: number;
  assemblies?: string[];
  reason?: string;
}

export interface Outcome {
  total_cost: number;
  lost_margin: number;
  lost_revenue: number;
  action_cost: number;
  action_costs: Record<string, number>;
  disruption_costs: number;
  weekly_lost_revenue: number[];
  lost_revenue_by_assembly: Record<string, number>;
  assembly_output: Record<string, number[]>;
  first_stop_week: number | null;
  short_components: Record<string, number>;
  refused: { action: unknown; reason: string }[];
}

export interface Reference {
  plan: Action[];
  outcome: Outcome;
  skill?: number | null;
}

export interface AffectedComponent {
  id: string;
  description: string;
  commodity: string | null;
  criticality: string | null;
  single_source: boolean | null;
  supplier: string;
  supplier_name: string;
  supplier_country: string | null;
  weekly_value: number;
  assemblies: string[];
  reason: string;
  weeks_short: number;
}

export interface Assessment {
  affected_components: AffectedComponent[];
  affected_suppliers: { id: string; name: string; country: string | null }[];
  affected_sites: string[];
  assemblies: { id: string; business_unit: string | null; weekly_revenue: number; lost_revenue: number }[];
  do_nothing: Outcome;
  margin: number;
  margin_from_graph: boolean;
}

export interface RunStep {
  kind: "thought" | "tool" | "error";
  t: number;
  name?: string;
  input?: Record<string, unknown>;
  result?: unknown;
  error?: boolean;
  text?: string;
}

export interface Run {
  id: number;
  disruption_id: number;
  job_id: string | null;
  status: "running" | "done" | "failed";
  provider: string;
  model: string;
  started_at: string;
  finished_at: string | null;
  steps: RunStep[];
  plan: Action[] | null;
  summary: string | null;
  outcome: Outcome | null;
  skill: number | null;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  error: string | null;
}

export type RunSummary = Omit<Run, "steps" | "plan" | "summary" | "outcome" | "disruption_id" | "job_id"> & { steps: number };

export interface ProposedAction {
  id: number;
  disruption_id: number;
  run_id: number | null;
  action: Action;
  reason: string;
  status: "proposed" | "accepted" | "rejected" | "superseded";
  decided_by: string | null;
  decided_at: string | null;
  comment: string | null;
  created_at: string;
  label: string;
  text: string;
  valid: boolean;
}

export interface Task {
  id: number;
  action_id: number;
  disruption_id: number;
  disruption_name?: string | null;
  title: string;
  status: "open" | "in_progress" | "done" | "cancelled";
  owner: string | null;
  due_week: number | null;
  notes: string;
  updated_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface DisruptionDetail {
  disruption: Disruption & { id: number; status: DisruptionStatus; type_label: string; created_at: string; author: string;
    warnings?: string[] };
  assessment: Assessment;
  references: { do_nothing: Reference; playbook: Reference; optimiser: Reference };
  runs: RunSummary[];
  latest_run: Run | null;
  actions: ProposedAction[];
  accepted: { plan: Action[]; outcome: Outcome | null; skill: number | null };
  tasks: Task[];
  rules: Record<string, unknown>;
  thread?: ThreadInfo | null;
  plan?: PlanView | null;
}

export interface OpsMeta {
  types: { id: DisruptionType; label: string }[];
  actions: { id: ActionType; label: string }[];
  rules: Record<string, Record<string, number> | number>;
  countries: { suppliers: string[]; sites: string[] };
  continents: string[];
  commodities: string[];
  suppliers: { id: string; name: string; country: string | null }[];
  sites: { id: string; name: string; country: string | null }[];
  assemblies: { id: string; business_unit: string | null; revenue: number }[];
  shockbench_episodes: { episode: number; weeks: number; low: number }[];
  model: { provider: string; model: string } | null;
  margin: number;
}

export interface OpsSummary {
  active: {
    id: number;
    name: string;
    type: DisruptionType;
    type_label: string;
    start_week: number;
    duration_weeks: number;
    do_nothing_cost: number;
    lost_revenue: number;
    plan_cost: number | null;
    accepted_actions: number;
    thread_id?: number | null;
  }[];
  open_tasks: number;
  in_progress_tasks: number;
  overdue_tasks?: number;
  /** Open and in-progress tasks of every disruption, overdue first. */
  tasks?: OpenTask[];
  /** Disruptions waiting for a person (plan ready, agent failed, budget, task overdue): the Agent count. */
  agent_needs?: number;
}

export type ReportBlock =
  | { kind: "text"; text: string }
  | { kind: "metrics"; items: { label: string; value: string }[] }
  | { kind: "table"; columns: string[]; rows: (string | number | null)[][] }
  | { kind: "chart"; title: string; unit: string; series: { name: string; values: number[] }[] }
  | { kind: "list"; items: string[] };

export interface Report {
  kind: "disruption" | "portfolio" | "scorecard" | "benchmark";
  id: number | string;
  title: string;
  subtitle: string;
  generated_at: string;
  sections: { title: string; note?: string; blocks: ReportBlock[] }[];
}

/** A draft disruption from a news text: saved at once as a draft (`id`) and planned by the agent in its thread. */
export interface NewsDraft {
  id: number;
  thread_id: number;
  disruption: Disruption;
  warnings: string[];
  cost_usd: number;
}

export const opsKeys = {
  all: ["ops"] as const,
  meta: ["ops", "meta"] as const,
  library: ["ops", "library"] as const,
  summary: ["ops", "summary"] as const,
  disruption: (id: number) => ["ops", "disruption", id] as const,
  report: (kind: string, id?: number | string) => ["ops", "report", kind, id ?? null] as const,
};

export const reportPath = (kind: string, id?: number | string) =>
  kind === "disruption" ? `/ops/reports/disruption/${id}` : `/ops/reports/${kind}`;

export function useOpsMeta() {
  return useQuery({ queryKey: opsKeys.meta, queryFn: () => api<OpsMeta>("/ops/meta"), staleTime: 60_000 });
}

export function useLibrary() {
  return useQuery({ queryKey: opsKeys.library, queryFn: () => api<LibraryEntry[]>("/ops/library"), staleTime: 60_000 });
}

export function useOpsSummary() {
  return useQuery({ queryKey: opsKeys.summary, queryFn: () => api<OpsSummary>("/ops/summary") });
}

/** A disruption with its assessment, references, actions, tasks and current plan; refreshed with its thread. */
export function useDisruption(id: number | undefined) {
  return useQuery({
    queryKey: opsKeys.disruption(id ?? -1),
    queryFn: () => api<DisruptionDetail>(`/ops/disruptions/${id}`),
    enabled: id !== undefined,
  });
}

export function useReport(kind: string | undefined, id?: number | string) {
  return useQuery({
    queryKey: opsKeys.report(kind ?? "", id),
    queryFn: () => api<Report>(reportPath(kind!, id)),
    enabled: !!kind && (kind !== "disruption" || id !== undefined),
    staleTime: 30_000,
  });
}

/** Mutations invalidate every ops and agent query: lists, details, summary, reports and threads (a decision is posted
 *  to its thread) all move together. */
function useOpsMutation<TVars, TOut>(fn: (vars: TVars) => Promise<TOut>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => Promise.all([qc.invalidateQueries({ queryKey: opsKeys.all }), qc.invalidateQueries({ queryKey: ["agent"] })]),
  });
}

export const useCreateDisruption = () =>
  useOpsMutation((d: Disruption) => api<{ id: number }>("/ops/disruptions", { json: d }));
export const useUpdateDisruption = () =>
  useOpsMutation(({ id, d }: { id: number; d: Disruption }) => api<{ id: number }>(`/ops/disruptions/${id}`, { json: d }));
export const useConfirmDraft = () =>
  useOpsMutation((id: number) => api<{ id: number; status: DisruptionStatus }>(`/ops/disruptions/${id}/confirm`, { method: "POST" }));
export const useDiscardDraft = () =>
  useOpsMutation((id: number) => api<{ id: number; status: DisruptionStatus }>(`/ops/disruptions/${id}/discard`, { json: {} }));
export const useStartRun = () =>
  useOpsMutation((id: number) => api<{ run_id: number; job_id: string }>(`/ops/disruptions/${id}/runs`, { method: "POST" }));
export const useDecide = () =>
  useOpsMutation(({ id, accept, comment, owner }: { id: number; accept: boolean; comment?: string; owner?: string }) =>
    api<ProposedAction>(`/ops/actions/${id}/${accept ? "accept" : "reject"}`, { json: { comment, owner } }),
  );
export const useUpdateTask = () =>
  useOpsMutation(({ id, ...changes }: { id: number } & Partial<Pick<Task, "status" | "owner" | "due_week" | "notes">>) =>
    api<Task>(`/ops/tasks/${id}`, { json: changes }),
  );

export function draftFromNews(text: string) {
  return api<NewsDraft>("/ops/disruptions/from-news", { json: { text } });
}

export function assess(d: Disruption) {
  return api<Assessment>("/ops/assess", { json: d });
}
