/** Agent API: threads, messages, edit blocks and resolving (hand-written types for /api/agent and the ops calls the
 *  workspace makes). A thread is polled every second while a turn runs or messages wait; the list every 5 seconds. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, query } from "./client";
import { opsKeys, type Action, type DisruptionStatus, type Outcome, type RunStep } from "./ops";

/** Why a thread waits for a person. */
export type Needs = "plan ready" | "agent failed" | "budget" | "task overdue";

export interface ThreadRow {
  id: number;
  disruption_id: number | null;
  disruption_status: DisruptionStatus | "resolved" | null;
  title: string;
  status: "active" | "archived";
  needs: Needs | null;
  created_at: string;
  updated_at: string;
  last: { author: string; role: Role; created_at: string; text: string } | null;
}

export type Role = "person" | "agent" | "system";

export interface PlanAction {
  action: Action;
  text: string;
  reason: string;
  kept: boolean;
  action_id: number | null;
  status: "proposed" | "accepted" | "rejected" | "superseded";
  comment: string | null;
}

export interface PlanView {
  id: number;
  disruption_id: number;
  version: number;
  run_id: number | null;
  graph_version: number | null;
  summary: string;
  skill: number | null;
  created_at: string;
  actions: PlanAction[];
  outcome: Outcome | null;
  /** Why the plan no longer holds on the current graph: its cost moved, or actions are refused now. */
  stale: { total_cost_before: number | null; total_cost_now: number; refused: { action: unknown; reason: string }[] } | null;
  current: boolean;
  do_nothing_cost: number;
  spend_limit_eur: number;
  over_limit: boolean;
}

export interface EditBlock {
  id: number;
  thread_id: number;
  edits: Record<string, unknown>[];
  before: unknown[];
  preview: { changed?: number; [k: string]: unknown } | null;
  reason: string;
  status: "proposed" | "applied" | "dismissed" | "stale";
  decided_by: string | null;
  decided_at: string | null;
  journal_id: number | null;
  created_at: string;
  changes: { edit: string; old: unknown; new: unknown }[];
}

export interface DraftDisruption {
  id: number;
  status: DisruptionStatus | "resolved";
  name: string;
  type: string;
  start_week: number;
  duration_weeks: number;
  description: string;
  params: Record<string, unknown>;
  stock_cover_weeks: number;
  horizon_weeks: number;
  source: string;
  warnings?: string[];
}

export interface AgentEvent {
  id: number;
  type: string;
  payload: Record<string, unknown>;
  source: string;
  thread_id: number | null;
  run_id: number | null;
  created_at: string;
}

export type Block =
  | { type: "text"; text: string }
  | { type: "plan"; ref: number; plan?: PlanView; applied?: boolean }
  | { type: "simulation"; ref: number }
  | { type: "edit"; ref: number; edit?: EditBlock }
  | { type: "draft"; ref: number; disruption?: DraftDisruption | null }
  | { type: "event"; ref: number; event?: AgentEvent | null }
  | { type: "decision"; kind: string; ref: number; decision: string; comment?: string; what?: string };

export interface Message {
  id: number;
  thread_id: number;
  author: string;
  role: Role;
  blocks: Block[];
  status: "queued" | "done";
  profile: string | null;
  run_id: number | null;
  created_at: string;
}

export interface ThreadRun {
  id: number;
  status: "running" | "done" | "failed";
  profile: string | null;
  error: string | null;
  steps: RunStep[];
}

export interface ThreadRead {
  thread: Omit<ThreadRow, "last" | "disruption_status">;
  messages: Message[];
  runs: ThreadRun[];
  queue: number[];
  running: boolean;
}

export interface ResolvedRow {
  id: number;
  name: string;
  type: string;
  type_label: string;
  resolved_at: string;
  resolved_by: string;
  resolution_note: string | null;
  thread_id: number | null;
  do_nothing_cost: number | null;
  plan_cost: number | null;
  saved: number | null;
  model_cost_usd: number | null;
  plan_version: number | null;
  accepted_actions: number;
}

export interface OpenTask {
  id: number;
  disruption_id: number;
  disruption_name: string;
  disruption_status: DisruptionStatus | "resolved";
  thread_id: number | null;
  title: string;
  status: "open" | "in_progress";
  owner: string | null;
  due_week: number | null;
  overdue: boolean;
  elapsed_week: number | null;
}

export const agentKeys = {
  all: ["agent"] as const,
  threads: ["agent", "threads"] as const,
  thread: (id: number) => ["agent", "thread", id] as const,
  resolved: ["agent", "resolved"] as const,
};

/** A turn is under way, or messages wait for one. */
export const busy = (t: ThreadRead | undefined) => !!t && (t.running || t.queue.length > 0);

/** Active threads, most recently active first; `live` polls every 5 seconds (a turn runs somewhere). */
export function useThreads(live = false) {
  return useQuery({
    queryKey: agentKeys.threads,
    queryFn: () => api<ThreadRow[]>(`/agent/threads${query({ status: "active" })}`),
    refetchInterval: live ? 5000 : false,
  });
}

/** A thread, polled every second while it is busy. The list and ops queries refresh when a turn ends. */
export function useThread(id: number | undefined) {
  const qc = useQueryClient();
  return useQuery({
    queryKey: agentKeys.thread(id ?? -1),
    queryFn: async () => {
      const before = qc.getQueryData<ThreadRead>(agentKeys.thread(id!));
      const next = await api<ThreadRead>(`/agent/threads/${id}`);
      if (busy(before) && !busy(next)) {
        qc.invalidateQueries({ queryKey: agentKeys.threads });
        qc.invalidateQueries({ queryKey: opsKeys.all });
      }
      return next;
    },
    enabled: id !== undefined,
    refetchInterval: (q) => (busy(q.state.data) ? 1000 : false),
  });
}

export function useResolved() {
  return useQuery({ queryKey: agentKeys.resolved, queryFn: () => api<ResolvedRow[]>("/ops/resolved") });
}

/** Mutations refresh every agent and ops query: threads, cards, details and counts move together. */
function useAgentMutation<TVars, TOut>(fn: (vars: TVars) => Promise<TOut>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => Promise.all([qc.invalidateQueries({ queryKey: agentKeys.all }), qc.invalidateQueries({ queryKey: opsKeys.all })]),
  });
}

export const useStartThread = () =>
  useAgentMutation((text: string) => api<{ thread_id: number; message_id: number }>("/agent/threads", { json: { text } }));
export const usePostMessage = () =>
  useAgentMutation(({ id, text }: { id: number; text: string }) => api<{ message_id: number }>(`/agent/threads/${id}/messages`, { json: { text } }));
export const useApplyEdit = () => useAgentMutation((id: number) => api(`/agent/edits/${id}/apply`, { method: "POST" }));
export const useDismissEdit = () =>
  useAgentMutation(({ id, comment }: { id: number; comment?: string }) => api(`/agent/edits/${id}/dismiss`, { json: { comment } }));
export const useResolve = () =>
  useAgentMutation(({ id, note }: { id: number; note: string }) =>
    api<{ id: number; status: "resolved"; thread_id: number }>(`/ops/disruptions/${id}/resolve`, { json: { note } }),
  );

/** The thread of a disruption, if it has one (old addresses and newly created disruptions). */
export async function threadOf(disruptionId: number): Promise<ThreadRow | undefined> {
  const rows = await api<ThreadRow[]>(`/agent/threads${query({ disruption: disruptionId })}`);
  return rows[0];
}
