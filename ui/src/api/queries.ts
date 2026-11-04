import {
  keepPreviousData,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  type QueryKey,
} from "@tanstack/react-query";
import {
  api,
  nodePath,
  query,
  type Decision,
  type EditResult,
  type EvidenceOut,
  type ExposureDetail,
  type JournalEntry,
  type Network,
  type NodeDetail,
  type Overview,
  type Proposal,
  type ProposalDetail,
  type SearchHit,
  type SpecDetail,
  type SpecSummary,
  type Stats,
  type UndoResult,
} from "./client";

export const keys = {
  stats: ["stats"] as const,
  overview: ["overview"] as const,
  network: ["network"] as const,
  node: (type: string, id: string) => ["node", type, id] as const,
  exposure: (type: string, id: string) => ["exposure", type, id] as const,
  search: (q: string, type?: string) => ["search", q, type ?? null] as const,
  proposals: (status: string, kind?: string) => ["proposals", status, kind ?? null] as const,
  proposal: (id: number, samples: number) => ["proposal", id, samples] as const,
  journal: ["journal"] as const,
  specs: ["specs"] as const,
  spec: (name: string) => ["spec", name] as const,
};

/** What each kind of change makes stale (by query-key prefix). */
export const invalidation: Record<"graph" | "spec", QueryKey[]> = {
  graph: [["stats"], ["overview"], ["network"], ["node"], ["exposure"], ["search"], ["proposals"], ["proposal"],
    ["journal"]],
  spec: [["specs"], ["spec"], ["proposal"]],
};

function useInvalidate() {
  const client = useQueryClient();
  return (kind: keyof typeof invalidation) =>
    Promise.all(invalidation[kind].map((queryKey) => client.invalidateQueries({ queryKey })));
}

// -- reads -------------------------------------------------------------------

export const useStats = () => useQuery({ queryKey: keys.stats, queryFn: () => api<Stats>("/stats") });
export const useOverview = () => useQuery({ queryKey: keys.overview, queryFn: () => api<Overview>("/overview") });
export const useNetwork = () => useQuery({ queryKey: keys.network, queryFn: () => api<Network>("/network") });

export const useNode = (type: string | undefined, id: string | undefined) =>
  useQuery({
    queryKey: keys.node(type ?? "", id ?? ""),
    queryFn: () => api<NodeDetail>(`/nodes/${nodePath(type!, id!)}`),
    enabled: !!type && !!id,
  });

export const EXPOSED = ["Component", "Site", "Supplier"];

export const useExposure = (type: string | undefined, id: string | undefined) =>
  useQuery({
    queryKey: keys.exposure(type ?? "", id ?? ""),
    queryFn: () => api<ExposureDetail>(`/exposure/${nodePath(type!, id!)}`),
    enabled: !!type && !!id && EXPOSED.includes(type),
    retry: false,
  });

export const useSearch = (q: string, type?: string) =>
  useQuery({
    queryKey: keys.search(q, type),
    queryFn: ({ signal }) => api<SearchHit[]>(`/search${query({ q, type, limit: 12 })}`, { signal }),
    enabled: q.trim().length > 0,
    placeholderData: keepPreviousData,
  });

export const useProposals = (status: string, kind?: string) =>
  useQuery({
    queryKey: keys.proposals(status, kind),
    queryFn: () => api<Proposal[]>(`/proposals${query({ status, kind, sort: "impact" })}`),
  });

export const useProposal = (id: number | undefined, samples = 5) =>
  useQuery({
    queryKey: keys.proposal(id ?? 0, samples),
    queryFn: () => api<ProposalDetail>(`/proposals/${id}${query({ samples })}`),
    enabled: !!id,
    placeholderData: keepPreviousData,
  });

export const JOURNAL_PAGE = 50;

export const useJournal = () =>
  useInfiniteQuery({
    queryKey: keys.journal,
    queryFn: ({ pageParam }) => api<JournalEntry[]>(`/journal${query({ before: pageParam, limit: JOURNAL_PAGE })}`),
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (last) => (last.length === JOURNAL_PAGE ? last[last.length - 1].id : undefined),
  });

export const useSpecs = () => useQuery({ queryKey: keys.specs, queryFn: () => api<SpecSummary[]>("/specs") });
export const useSpec = (name: string | undefined) =>
  useQuery({
    queryKey: keys.spec(name ?? ""),
    queryFn: () => api<SpecDetail>(`/specs/${encodeURIComponent(name!)}`),
    enabled: !!name,
  });

// -- changes -----------------------------------------------------------------

export interface AcceptInput {
  id: number;
  comment?: string;
  target_id?: string;
  new_node?: string | boolean;
}

export function useAccept() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ id, ...body }: AcceptInput) => api<Decision>(`/proposals/${id}/accept`, { json: body }),
    onSuccess: () => invalidate("graph"),
  });
}

export function useReject() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ id, comment }: { id: number; comment?: string }) =>
      api<Decision>(`/proposals/${id}/reject`, { json: { comment } }),
    onSuccess: () => invalidate("graph"),
  });
}

export type Edit = Record<string, unknown> & { op: string };

export interface EditsInput {
  edits: Edit[];
  evidence_id?: string;
  comment?: string;
}

export function useEdits() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (body: EditsInput) => api<EditResult>("/edits", { json: body }),
    onSuccess: () => invalidate("graph"),
  });
}

export function uploadEvidence(file: File): Promise<EvidenceOut> {
  const form = new FormData();
  form.append("file", file);
  return api<EvidenceOut>("/evidence", { form });
}

export function useUndo() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (entryId: number) => api<UndoResult>(`/journal/${entryId}/undo`, { method: "POST" }),
    onSuccess: () => invalidate("graph"),
  });
}

export function useApproveSpec() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (name: string) => api<SpecDetail>(`/specs/${encodeURIComponent(name)}/approve`, { method: "POST" }),
    onSuccess: () => invalidate("spec"),
  });
}
