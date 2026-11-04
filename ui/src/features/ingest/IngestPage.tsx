import { CheckCircle2, ChevronDown, ChevronRight, Clock, FileUp, Loader2, MinusCircle, Upload, X, XCircle } from "lucide-react";
import { useEffect, useRef, useState, type DragEvent, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import type { IngestReport } from "@/api/client";
import { ACCEPT, isRunning, useIngestJob, useIngestJobs, useStartBatch, type BatchFile, type IngestJob } from "@/api/ingest";
import { useAuthor } from "@/app/author";
import { Empty, ErrorNote } from "@/components/States";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn, formatNumber, formatTime } from "@/lib/utils";

interface ExcludedRow { sheet?: string | null; row: number; label: string; reason: string }
interface Region {
  name: string;
  sheet: string | null;
  kind: string;
  source: string;
  problems: string[];
  repair_rounds?: number;
  excluded_rows?: ExcludedRow[];
}
type Report = IngestReport & {
  encoding?: string | null;
  regions?: Region[];
  ignored?: Record<string, string>;
  tables?: Report[];
  chunks?: number;
  extracted?: number;
  rejected?: unknown[];
  flagged?: unknown[];
  unresolved?: unknown[];
};
interface Totals {
  files: number;
  done: number;
  failed: number;
  skipped: number;
  facts_applied: number;
  facts_proposed: number;
  proposals_created: number;
  excluded_rows: number;
  llm_calls: number;
  cost_usd: number;
}

const reviewLink = (id: number) => `/data/review/${id}`;

function Stat({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="rounded-md bg-surface-2 px-3 py-2">
      <div className="text-xs text-muted">{label}</div>
      <div className="text-lg font-semibold tabular">{value}</div>
    </div>
  );
}

function Regions({ regions }: { regions: Region[] }) {
  return (
    <table className="w-full text-sm" aria-label="Regions">
      <thead className="text-left text-xs text-muted">
        <tr className="border-b">
          <th className="py-1.5 font-medium">Region</th>
          <th className="font-medium">Sheet</th>
          <th className="font-medium">Kind</th>
          <th className="font-medium">Spec</th>
          <th className="font-medium">Problems</th>
        </tr>
      </thead>
      <tbody>
        {regions.map((r) => (
          <tr key={`${r.sheet}:${r.name}`} className="border-b align-top last:border-b-0">
            <td className="py-1.5">{r.name}</td>
            <td>{r.sheet ?? "—"}</td>
            <td className="text-muted">{r.kind}</td>
            <td>
              <span className={cn("rounded px-1.5 py-0.5 text-xs", r.source === "reused" ? "bg-ok/10 text-ok" : "bg-brand-soft text-brand")}>{r.source}</span>
              {!!r.repair_rounds && <span className="ml-1 text-xs text-muted">{r.repair_rounds} repair round{r.repair_rounds > 1 ? "s" : ""}</span>}
            </td>
            <td className="text-xs text-muted">{r.problems.join("; ") || "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function ReportView({ report, title = true }: { report: Report; title?: boolean }) {
  const load = report.load as Record<string, unknown>;
  const created = (load.proposals_created as number[] | undefined) ?? [];
  const llm = report.llm as { calls: number; cache_hits: number; cost_usd: number };
  const regions = report.regions ?? (report.tables ?? []).flatMap((t) => t.regions ?? []);
  const excluded = regions.flatMap((r) => r.excluded_rows ?? []);
  return (
    <Card aria-label={`Ingest report ${report.file}`}>
      {title && (
        <CardHeader>
          <CardTitle>{report.file}</CardTitle>
          <span className="text-xs text-muted">run {report.run_id}</span>
        </CardHeader>
      )}
      <CardContent className={cn("flex flex-col gap-4", !title && "pt-4")}>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
          <Stat label="Facts applied" value={formatNumber(load.applied as number | undefined)} />
          <Stat label="Waiting for review" value={formatNumber(load.proposed as number | undefined)} />
          <Stat label="Unchanged" value={formatNumber(load.unchanged as number | undefined)} />
          <Stat label="Flagged" value={formatNumber((load.flagged as number | undefined) ?? report.flagged?.length)} />
          <Stat label="LLM calls" value={<span data-testid="llm-calls">{llm.calls}</span>} />
        </div>
        <div className="text-sm">
          {created.length === 0 ? (
            <span data-testid="no-new-proposals">No new proposals.</span>
          ) : (
            <span>
              New proposals:{" "}
              {created.map((id, i) => (
                <span key={id}>{i > 0 && ", "}<Link to={reviewLink(id)} className="text-interactive hover:underline">#{id}</Link></span>
              ))}
            </span>
          )}
          <span className="ml-3 text-muted">LLM: {llm.calls} calls, {llm.cache_hits} from cache, ${llm.cost_usd.toFixed(4)}</span>
          {report.encoding && <span className="ml-3 text-muted">Read as {report.encoding}</span>}
        </div>
        {regions.length > 0 && <Regions regions={regions} />}
        {excluded.length > 0 && (
          <div className="text-xs text-muted" data-testid="excluded-rows">
            Summary rows left out: {excluded.map((e) => `${e.sheet ? `${e.sheet} ` : ""}row ${e.row} (“${e.label}”)`).join(", ")}
          </div>
        )}
        {report.kind === "document" && (
          <div className="text-sm text-muted">
            {report.chunks} chunks, {report.extracted} facts extracted, {report.rejected?.length ?? 0} rejected, {report.unresolved?.length ?? 0} unresolved mentions.
          </div>
        )}
        {report.ignored && Object.keys(report.ignored).length > 0 && (
          <div className="text-xs text-muted">Ignored sheets: {Object.entries(report.ignored).map(([s, why]) => `${s} (${why})`).join(", ")}</div>
        )}
        {report.errors.length > 0 && <ErrorNote error={report.errors.join("; ")} />}
      </CardContent>
    </Card>
  );
}

const FILE_STATUS: Record<string, { Icon: typeof Clock; className: string; spin?: boolean }> = {
  queued: { Icon: Clock, className: "text-muted border-border" },
  running: { Icon: Loader2, className: "text-interactive border-interactive/40 bg-interactive-soft", spin: true },
  done: { Icon: CheckCircle2, className: "text-ok border-ok/40 bg-ok/10" },
  failed: { Icon: XCircle, className: "text-conflict border-conflict/40 bg-conflict/10" },
  skipped: { Icon: MinusCircle, className: "text-muted border-border bg-surface-2" },
};

/** A file's or a job's status as icon and word. */
function RunStatus({ status }: { status: string }) {
  const style = FILE_STATUS[status] ?? FILE_STATUS.queued;
  const { Icon } = style;
  return (
    <Badge className={style.className}>
      <Icon aria-hidden className={cn(style.spin && "animate-spin")} />
      {status}
    </Badge>
  );
}

function fileSummary(file: BatchFile): string {
  if (file.status === "skipped") return file.reason ?? "skipped";
  if (file.status === "failed") return file.error ?? "failed";
  const report = file.report as Report | null | undefined;
  if (!report) return file.status === "running" ? "working…" : "";
  const load = report.load as Record<string, number | number[] | undefined>;
  const created = (load.proposals_created as number[] | undefined)?.length ?? 0;
  const llm = report.llm as { calls: number };
  return `${formatNumber(load.applied as number | undefined)} facts applied, ${created} new proposals, ${llm.calls} LLM calls`;
}

function FileRow({ file }: { file: BatchFile }) {
  const [open, setOpen] = useState(false);
  const report = file.report as Report | null | undefined;
  return (
    <li className="border-b last:border-b-0" aria-label={file.name}>
      <div className="flex items-center gap-3 py-2">
        <button
          type="button"
          className="flex min-w-0 flex-1 items-center gap-2 text-left disabled:cursor-default"
          disabled={!report}
          aria-expanded={report ? open : undefined}
          aria-label={report ? `${open ? "Hide" : "Show"} report for ${file.name}` : undefined}
          onClick={() => setOpen((o) => !o)}
        >
          {report ? (open ? <ChevronDown className="size-4 shrink-0" aria-hidden /> : <ChevronRight className="size-4 shrink-0" aria-hidden />) : <span className="size-4 shrink-0" />}
          <span className="truncate font-medium">{file.name}</span>
          <span className="shrink-0 text-xs text-muted">{file.kind}</span>
        </button>
        <span className={cn("hidden max-w-[45%] truncate text-xs sm:block", file.status === "failed" ? "text-conflict" : "text-muted")} title={fileSummary(file)}>
          {fileSummary(file)}
        </span>
        <RunStatus status={file.status} />
      </div>
      {file.status === "failed" && file.error && <ErrorNote error={file.error} className="mb-2 sm:hidden" />}
      {open && report && <div className="pb-3"><ReportView report={report} title={false} /></div>}
    </li>
  );
}

function JobView({ job }: { job: IngestJob }) {
  const state = job.result ?? job.progress;
  const totals = state.totals as unknown as Totals;
  const ingestable = totals.files - totals.skipped;
  return (
    <Card aria-label="Batch">
      <CardHeader>
        <div className="min-w-0">
          <CardTitle className="truncate">{job.title}</CardTitle>
          <span className="text-xs text-muted">started {formatTime(job.created_at)}</span>
        </div>
        <RunStatus status={job.status} />
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {isRunning(job) && (
          <div role="status" className="flex items-center gap-2 rounded-md bg-surface-2 px-3 py-2 text-sm">
            <Loader2 className="size-4 animate-spin" aria-hidden />
            {state.current ? <>Working on {state.current}</> : <>Waiting to start</>}: {totals.done + totals.failed} of {ingestable} files finished.
          </div>
        )}
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-6" aria-label="Batch totals">
          <Stat label="Files done" value={<span data-testid="files-done">{totals.done} / {ingestable}</span>} />
          <Stat label="Facts applied" value={formatNumber(totals.facts_applied)} />
          <Stat label="Waiting for review" value={formatNumber(totals.facts_proposed)} />
          <Stat label="New proposals" value={formatNumber(totals.proposals_created)} />
          <Stat label="Summary rows left out" value={formatNumber(totals.excluded_rows)} />
          <Stat label="LLM" value={<span data-testid="batch-llm">{totals.llm_calls} calls, ${totals.cost_usd.toFixed(4)}</span>} />
        </div>
        {totals.failed > 0 && <div className="text-sm text-conflict">{totals.failed} file{totals.failed > 1 ? "s" : ""} failed; the others were ingested.</div>}
        <ul aria-label="Files" className="text-sm">
          {state.files.map((f) => <FileRow key={f.name} file={f} />)}
        </ul>
        {job.error && <ErrorNote error={job.error.split("\n")[0]} />}
      </CardContent>
    </Card>
  );
}

function RecentJobs({ jobs, selected, onSelect }: { jobs: IngestJob[]; selected: string | null; onSelect: (id: string) => void }) {
  return (
    <Card aria-label="Recent batches">
      <CardHeader><CardTitle>Recent batches</CardTitle></CardHeader>
      <CardContent>
        {jobs.length === 0 ? (
          <Empty>No batches yet.</Empty>
        ) : (
          <ul className="flex flex-col text-sm">
            {jobs.map((j) => {
              const t = (j.result ?? j.progress).totals as unknown as Totals;
              return (
                <li key={j.id}>
                  <button
                    type="button"
                    onClick={() => onSelect(j.id)}
                    aria-current={j.id === selected ? "true" : undefined}
                    className={cn("flex w-full items-center gap-3 rounded-md px-2 py-1.5 text-left hover:bg-surface-2", j.id === selected && "bg-surface-2")}
                  >
                    <span className="min-w-0 flex-1 truncate">{j.title}</span>
                    <span className="hidden text-xs text-muted sm:inline">{formatNumber(t.facts_applied)} applied, {t.proposals_created} proposals</span>
                    <span className="text-xs text-muted">{formatTime(j.created_at)}</span>
                    <RunStatus status={j.status} />
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

const accepted = (f: File) => ACCEPT.some((s) => f.name.toLowerCase().endsWith(s));

/** Upload many files and zip archives as one batch job; follow each file's status and report. */
export default function IngestPage() {
  const [files, setFiles] = useState<File[]>([]);
  const [reuseOnly, setReuseOnly] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const start = useStartBatch();
  const jobs = useIngestJobs();
  const jobId = selected ?? jobs.data?.[0]?.id ?? null;
  const job = useIngestJob(jobId);
  const { requireAuthor } = useAuthor();

  useEffect(() => {
    if (job.data?.status === "done" && job.data.id === start.data?.id) toast.success(`Batch finished: ${job.data.title}`);
  }, [job.data?.status, job.data?.id, job.data?.title, start.data?.id]);

  const add = (list: FileList | null | undefined) => {
    const picked = Array.from(list ?? []);
    const refused = picked.filter((f) => !accepted(f));
    if (refused.length) toast.error(`Not ingested: ${refused.map((f) => f.name).join(", ")}. Upload ${ACCEPT.join(", ")}`);
    const ok = picked.filter(accepted);
    if (!ok.length) return;
    setFiles((current) => [...current.filter((c) => !ok.some((f) => f.name === c.name)), ...ok]);
    start.reset();
  };
  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    add(e.dataTransfer.files);
  };
  const run = () =>
    files.length > 0 &&
    requireAuthor(() =>
      start.mutate(
        { files, reuseOnly },
        {
          onSuccess: (started) => {
            setSelected(started.id);
            setFiles([]);
            if (input.current) input.current.value = "";
          },
        },
      ),
    );

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-4 p-6">
      <p className="text-sm text-muted">
        Drop the files as they came: spreadsheets, csv exports, PDFs, text, or zip archives of them. Tables are read first, then documents; a known layout
        reuses its stored mapping spec. Anything below the review threshold becomes a proposal.
      </p>
      <div
        role="button"
        tabIndex={0}
        aria-label="Drop files here or choose them"
        onClick={() => input.current?.click()}
        onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && input.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={cn("flex cursor-pointer flex-col items-center gap-2 rounded-xl border-2 border-dashed bg-surface px-6 py-8 text-center", dragging && "border-interactive bg-interactive-soft")}
      >
        <FileUp className="size-8 text-muted" />
        <span>Drop files or zip archives here, or click to choose</span>
        <span className="text-xs text-muted">{ACCEPT.join(", ")} · up to 200 files, 200 MB</span>
        <input ref={input} type="file" multiple className="hidden" accept={ACCEPT.join(",")} data-testid="file-input" onChange={(e) => add(e.target.files)} />
      </div>
      {files.length > 0 && (
        <ul aria-label="Chosen files" className="flex flex-wrap gap-2">
          {files.map((f) => (
            <li key={f.name} className="flex items-center gap-1 rounded-full border bg-surface px-2.5 py-0.5 text-sm">
              {f.name}
              <button type="button" aria-label={`Remove ${f.name}`} className="text-muted hover:text-fg" onClick={() => setFiles((c) => c.filter((x) => x !== f))}>
                <X className="size-3.5" aria-hidden />
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="flex items-center gap-4">
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={reuseOnly} onChange={(e) => setReuseOnly(e.target.checked)} />
          Stored specs only (no model calls)
        </label>
        <Button variant="accent" className="ml-auto" disabled={files.length === 0 || start.isPending} onClick={run}>
          {start.isPending ? <Loader2 className="animate-spin" /> : <Upload />} {start.isPending ? "Uploading…" : files.length > 1 ? `Ingest ${files.length} files` : "Ingest"}
        </Button>
      </div>
      {start.error && <ErrorNote error={start.error} />}
      {job.data && <JobView job={job.data} />}
      {job.error && <ErrorNote error={job.error} />}
      {jobs.data && <RecentJobs jobs={jobs.data} selected={jobId} onSelect={setSelected} />}
    </div>
  );
}
