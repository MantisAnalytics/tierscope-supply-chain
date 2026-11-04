import { Bot, PanelRight, X } from "lucide-react";
import { useState } from "react";
import { busy, useThread, useThreads, type Block, type ThreadRead } from "@/api/agent";
import { useNetwork } from "@/api/queries";
import { useDisruption, useOpsMeta, useOpsSummary, type DisruptionDetail } from "@/api/ops";
import { PanelStack, stackMoves } from "@/components/PanelStack";
import { Empty, ErrorNote, Loading } from "@/components/States";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EditDialog, type EditIntent } from "@/features/network/EditDialogs";
import { LinkPanel } from "@/features/network/LinkPanel";
import { NodePanel } from "@/features/network/NodePanel";
import { ReportView } from "@/features/reports/ReportDocument";
import { cn } from "@/lib/utils";
import { crumbKey, useAgentAddress, type DetailCrumb } from "./address";
import { Composer } from "./Composer";
import { Draft } from "./detail/Draft";
import { Edit } from "./detail/Edit";
import { Incident } from "./detail/Incident";
import { Plan } from "./detail/Plan";
import { Simulation } from "./detail/Simulation";
import { Steps } from "./detail/Steps";
import { Task } from "./detail/Task";
import { EmptyState } from "./EmptyState";
import { ResolveDialog } from "./ResolveDialog";
import { Thread } from "./Thread";
import { ThreadList } from "./ThreadList";

/** The Agent tab: the list of disruptions and conversations, the selected thread, and a detail column with the
 *  incident state and whatever the person opened from the thread. The thread and the column's stack are in the
 *  address (`/agent/t/:id?d=plan:2,node:Supplier:S1`). Below 1440px the column opens over the thread. */
export default function AgentPage() {
  const { threadId, stack, setStack } = useAgentAddress();
  const { data: meta } = useOpsMeta();
  const thread = useThread(threadId);
  const { data: threads, error: listError } = useThreads(busy(thread.data));
  const hasModel = !!meta?.model;

  return (
    <div className="flex h-full flex-col">
      <header className="flex h-11 shrink-0 items-center gap-2 border-b bg-surface px-4 text-sm">
        <Bot className="size-4 text-brand" aria-hidden />
        <h1 className="font-semibold">Agent</h1>
        <span className="ml-auto text-xs text-muted" aria-label="Model">
          {meta === undefined ? "" : meta.model ? `${meta.model.model} via ${meta.model.provider}` : "No model is configured: the agent cannot answer"}
        </span>
      </header>
      <div className="grid min-h-0 flex-1 grid-cols-[300px_minmax(0,1fr)] min-[1440px]:grid-cols-[300px_minmax(0,1fr)_460px]">
        <aside className="flex min-h-0 flex-col border-r bg-surface">
          {listError ? <ErrorNote error={listError} className="m-3" /> : <ThreadList threads={threads ?? []} current={threadId} />}
        </aside>
        {threadId === undefined ? (
          <section aria-label="New thread" className="min-h-0 overflow-auto">
            <EmptyState hasModel={hasModel} />
          </section>
        ) : thread.error ? (
          <ErrorNote error={thread.error} className="m-6" />
        ) : !thread.data ? (
          <Loading />
        ) : (
          <Workspace key={threadId} data={thread.data} stack={stack} setStack={setStack} hasModel={hasModel} />
        )}
      </div>
    </div>
  );
}

function Workspace({ data, stack, setStack, hasModel }: { data: ThreadRead; stack: DetailCrumb[]; setStack: (s: DetailCrumb[]) => void; hasModel: boolean }) {
  const did = data.thread.disruption_id ?? undefined;
  const { data: detail } = useDisruption(did);
  const [resolving, setResolving] = useState(false);
  const [incident, setIncident] = useState(false); // narrow screens: the incident state opened over the thread
  const moves = stackMoves(stack, crumbKey, setStack);
  const overlay = stack.length > 0 || incident;
  const closed = data.thread.status !== "active" || (detail && !["draft", "active"].includes(detail.disruption.status));
  return (
    <>
      <section aria-label="Thread" className="relative flex min-h-0 flex-col min-[1440px]:col-span-1">
        <div className="flex h-11 shrink-0 items-center gap-2 border-b px-5">
          <h2 className="min-w-0 truncate text-sm font-semibold">{data.thread.title}</h2>
          {detail?.disruption.status === "draft" && <Badge className="text-proposed">draft</Badge>}
          {data.thread.needs && <Badge className="text-risk">{data.thread.needs}</Badge>}
          {did !== undefined && (
            <Button size="xs" variant="ghost" className="ml-auto min-[1440px]:hidden" onClick={() => setIncident(true)}>
              <PanelRight /> Incident
            </Button>
          )}
        </div>
        <div className="min-h-0 flex-1 overflow-auto">
          <Thread data={data} onOpen={moves.push} />
        </div>
        {closed ? (
          <p className="border-t px-5 py-3 text-sm text-muted">This thread is closed.</p>
        ) : (
          <Composer thread={data} detail={detail} hasModel={hasModel} onResolve={() => setResolving(true)} />
        )}
        {detail && <ResolveDialog detail={detail} open={resolving} onClose={() => setResolving(false)} />}
      </section>
      <aside
        aria-label="Detail"
        className={cn(
          "min-h-0 overflow-auto border-l bg-surface",
          "max-[1439px]:absolute max-[1439px]:inset-y-0 max-[1439px]:right-0 max-[1439px]:z-20 max-[1439px]:w-[min(480px,100%)] max-[1439px]:shadow-xl",
          !overlay && "max-[1439px]:hidden",
        )}
      >
        <Button size="icon" variant="ghost" className="absolute top-1 right-1 z-10 size-7 min-[1440px]:hidden" aria-label="Close details"
          onClick={() => (setIncident(false), moves.root())}>
          <X />
        </Button>
        <DetailColumn data={data} detail={detail} stack={stack} setStack={setStack} hasModel={hasModel} />
      </aside>
    </>
  );
}

const planBlocks = (data: ThreadRead) =>
  data.messages.flatMap((m) => m.blocks).filter((b): b is Extract<Block, { type: "plan" }> => b.type === "plan" && !!b.plan);
const blockOf = <K extends Block["type"]>(data: ThreadRead, type: K, ref: string) =>
  data.messages.flatMap((m) => m.blocks).find((b) => b.type === type && "ref" in b && String(b.ref) === ref) as Extract<Block, { type: K }> | undefined;

/** The detail column: the incident state at the root, then a breadcrumb stack of what was opened. Only the person's
 *  actions move it; new messages and plans never do. */
export function DetailColumn({ data, detail, stack, setStack, hasModel, readOnly = false }: {
  data: ThreadRead;
  detail: DisruptionDetail | undefined;
  stack: DetailCrumb[];
  setStack: (s: DetailCrumb[]) => void;
  hasModel: boolean;
  readOnly?: boolean;
}) {
  const moves = stackMoves(stack, crumbKey, setStack);
  const { data: network } = useNetwork();
  const { data: summary } = useOpsSummary();
  const [intent, setIntent] = useState<EditIntent | null>(null);
  const deepest = stack.at(-1);
  const openNode = (type: string, id: string) => moves.push({ kind: "node", id: `${type}:${id}` });
  const label = (c: DetailCrumb): string => {
    switch (c.kind) {
      case "plan": return `Plan v${c.id}`;
      case "edit": return `Edit ${c.id}`;
      case "draft": return blockOf(data, "draft", c.id)?.disruption?.name ?? "Draft";
      case "task": return detail?.tasks.find((t) => String(t.id) === c.id)?.title ?? `Task ${c.id}`;
      case "steps": return `Turn ${c.id}`;
      case "sim": return `Simulation ${c.id}`;
      case "report": return "Report";
      case "node": {
        const [type, ...rest] = c.id.split(":");
        const id = rest.join(":");
        return network?.entities.find((e) => e.type === type && e.id === id)?.name ?? id;
      }
    }
  };

  let body;
  if (!deepest) {
    body = detail ? <Incident detail={detail} onOpen={moves.push} /> : data.thread.disruption_id !== null ? <Loading /> : (
      <Empty className="p-6 text-sm">Cards, nodes and steps you open from the conversation show here.</Empty>
    );
  } else if (deepest.kind === "node") {
    const [type, ...rest] = deepest.id.split(":");
    const id = rest.join(":");
    if (type === "Link") {
      const [supplier, site] = id.split(">");
      body = network ? <LinkPanel link={{ supplier, site }} network={network} onSelect={openNode} onOpenPart={(part) => openNode("Component", part)} /> : <Loading />;
    } else {
      body = (
        <NodePanel key={deepest.id} type={type} id={id} network={network} onEdit={setIntent} onOpen={openNode}
          onOpenLink={(supplier, site) => moves.push({ kind: "node", id: `Link:${supplier}>${site}` })} />
      );
    }
  } else if (deepest.kind === "plan") {
    const plan = planBlocks(data).find((b) => String(b.plan!.version) === deepest.id)?.plan ?? (detail?.plan && String(detail.plan.version) === deepest.id ? detail.plan : undefined);
    body = !plan ? <Missing what={`plan version ${deepest.id}`} /> : !detail ? <Loading /> : <Plan plan={plan} detail={detail} canPlan={hasModel && !busy(data)} readOnly={readOnly} />;
  } else if (deepest.kind === "edit") {
    const edit = blockOf(data, "edit", deepest.id)?.edit;
    body = edit ? <Edit block={edit} readOnly={readOnly} /> : <Missing what={`edit ${deepest.id}`} />;
  } else if (deepest.kind === "draft") {
    const draft = blockOf(data, "draft", deepest.id)?.disruption ?? (detail && String(detail.disruption.id) === deepest.id ? { ...detail.disruption, warnings: detail.disruption.warnings } : undefined);
    body = draft ? <Draft draft={draft} readOnly={readOnly} /> : <Missing what={`draft ${deepest.id}`} />;
  } else if (deepest.kind === "task") {
    const task = detail?.tasks.find((t) => String(t.id) === deepest.id);
    const overdue = !!summary?.tasks?.find((t) => String(t.id) === deepest.id)?.overdue;
    body = task ? <Task key={task.id} task={task} overdue={overdue} /> : detail ? <Missing what={`task ${deepest.id}`} /> : <Loading />;
  } else if (deepest.kind === "steps") {
    body = <Steps run={data.runs.find((r) => String(r.id) === deepest.id)} />;
  } else if (deepest.kind === "sim") {
    body = <Simulation id={deepest.id} data={data} />;
  } else {
    body = data.thread.disruption_id !== null ? <ReportView kind="disruption" id={data.thread.disruption_id} /> : <Missing what="a report" />;
  }

  return (
    <>
      <PanelStack root={data.thread.disruption_id !== null ? "Incident" : "Thread"} crumbs={stack.map((c) => ({ key: crumbKey(c), label: label(c) }))}
        onRoot={moves.root} onGo={moves.go} escape>
        {body}
      </PanelStack>
      <EditDialog intent={intent} onClose={() => setIntent(null)} />
    </>
  );
}

function Missing({ what }: { what: string }) {
  return <Empty className="p-6 text-sm">There is no {what} in this thread.</Empty>;
}
