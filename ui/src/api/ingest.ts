/** Batch ingest: start a job over files and zip archives, poll it, list recent ones. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, query } from "./client";
import { invalidation } from "./queries";
import type { components } from "./schema";

type S = components["schemas"];
export type IngestJob = S["IngestJob"];
export type BatchState = S["BatchState"];
export type BatchFile = S["BatchFileOut"];

export const ACCEPT = [".xlsx", ".xlsm", ".csv", ".tsv", ".pdf", ".txt", ".md", ".zip"];
export const RECENT_JOBS = 10;
const POLL_MS = 1000;

export const ingestKeys = {
  jobs: ["ingest-jobs"] as const,
  job: (id: string) => ["ingest-job", id] as const,
};

export const isRunning = (job: { status: string } | undefined) => job?.status === "queued" || job?.status === "running";

/** Start a batch; the job comes back at once, before any file is ingested. */
export function useStartBatch() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ files, reuseOnly }: { files: File[]; reuseOnly: boolean }) => {
      const form = new FormData();
      for (const file of files) form.append("files", file);
      form.append("reuse_only", String(reuseOnly));
      return api<IngestJob>("/ingest/batch", { form });
    },
    onSuccess: (job) => {
      client.setQueryData(ingestKeys.job(job.id), job);
      return client.invalidateQueries({ queryKey: ingestKeys.jobs });
    },
  });
}

/** A job, polled while it runs; when it finishes, what it changed is refetched. */
export function useIngestJob(id: string | null) {
  const client = useQueryClient();
  return useQuery({
    queryKey: ingestKeys.job(id ?? ""),
    enabled: !!id,
    queryFn: async () => {
      const before = client.getQueryData<IngestJob>(ingestKeys.job(id!));
      const job = await api<IngestJob>(`/ingest/jobs/${encodeURIComponent(id!)}`);
      const doneBefore = before?.progress.files.filter((f) => f.status === "done").length ?? 0;
      const doneNow = job.progress.files.filter((f) => f.status === "done").length;
      if (doneNow > doneBefore || (isRunning(before) && !isRunning(job))) {
        void Promise.all([...invalidation.graph, ...invalidation.spec, ingestKeys.jobs].map((queryKey) =>
          client.invalidateQueries({ queryKey })));
      }
      return job;
    },
    refetchInterval: (q) => (isRunning(q.state.data) ? POLL_MS : false),
  });
}

export const useIngestJobs = () =>
  useQuery({
    queryKey: ingestKeys.jobs,
    queryFn: () => api<IngestJob[]>(`/ingest/jobs${query({ limit: RECENT_JOBS })}`),
    refetchInterval: (q) => (q.state.data?.some(isRunning) ? POLL_MS * 3 : false),
  });
