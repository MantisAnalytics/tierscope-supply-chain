import { Bot, Check, CircleCheckBig, RefreshCw, Send, X } from "lucide-react";
import { useState, type KeyboardEvent } from "react";
import { busy, usePostMessage, type ThreadRead } from "@/api/agent";
import { useConfirmDraft, useDiscardDraft, useStartRun, type DisruptionDetail } from "@/api/ops";
import { useAuthor } from "@/app/author";
import { ErrorNote } from "@/components/States";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/input";

export type QuickAction = "plan" | "again" | "confirm" | "discard" | "resolve";

/** The quick actions a disruption's state offers: Plan a response without a plan, Ask for a new version when the
 *  plan is stale or has rejections, Confirm or Discard for a draft, Mark resolved for an active disruption. */
export function quickActions(detail: DisruptionDetail | undefined): QuickAction[] {
  if (!detail) return [];
  const status = detail.disruption.status;
  if (status !== "draft" && status !== "active") return [];
  const plan = detail.plan;
  const out: QuickAction[] = [];
  if (!plan) out.push("plan");
  else if (plan.stale || plan.actions.some((a) => a.status === "rejected")) out.push("again");
  if (status === "draft") out.push("confirm", "discard");
  if (status === "active") out.push("resolve");
  return out;
}

/** The message box with the quick actions above it. A message sent while a turn runs is queued. Without a model,
 *  sending and the actions that need it are disabled with that reason. */
export function Composer({ thread, detail, hasModel, onResolve }: {
  thread: ThreadRead;
  detail: DisruptionDetail | undefined;
  hasModel: boolean;
  onResolve: () => void;
}) {
  const [text, setText] = useState("");
  const post = usePostMessage();
  const plan = useStartRun();
  const confirm = useConfirmDraft();
  const discard = useDiscardDraft();
  const { requireAuthor } = useAuthor();
  const did = thread.thread.disruption_id;
  const actions = quickActions(detail);
  const send = () => {
    const t = text.trim();
    if (!t || !hasModel) return;
    requireAuthor(() => post.mutate({ id: thread.thread.id, text: t }, { onSuccess: () => setText("") }));
  };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };
  const noModel = "No model is configured";
  const error = post.error ?? plan.error ?? confirm.error ?? discard.error;
  return (
    <div className="flex flex-col gap-2 border-t bg-surface p-3">
      {actions.length > 0 && did !== null && (
        <div className="flex flex-wrap items-center gap-1.5" aria-label="Quick actions">
          {actions.includes("plan") && (
            <Button size="sm" variant="outline" disabled={!hasModel || plan.isPending || busy(thread)} title={hasModel ? undefined : noModel}
              onClick={() => requireAuthor(() => plan.mutate(did))}>
              <Bot /> Plan a response
            </Button>
          )}
          {actions.includes("again") && (
            <Button size="sm" variant="outline" disabled={!hasModel || plan.isPending || busy(thread)} title={hasModel ? undefined : noModel}
              onClick={() => requireAuthor(() => plan.mutate(did))}>
              <RefreshCw /> Ask for a new version
            </Button>
          )}
          {actions.includes("confirm") && (
            <Button size="sm" variant="accent" disabled={confirm.isPending} onClick={() => requireAuthor(() => confirm.mutate(did))}>
              <Check /> Confirm
            </Button>
          )}
          {actions.includes("discard") && (
            <Button size="sm" variant="ghost" disabled={discard.isPending} onClick={() => requireAuthor(() => discard.mutate(did))}>
              <X /> Discard
            </Button>
          )}
          {actions.includes("resolve") && (
            <Button size="sm" variant="outline" className="ml-auto" onClick={onResolve}>
              <CircleCheckBig /> Mark resolved
            </Button>
          )}
        </div>
      )}
      {error ? <ErrorNote error={error} /> : null}
      <div className="flex items-end gap-2">
        <Textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKey}
          rows={2}
          disabled={!hasModel}
          placeholder={hasModel ? (busy(thread) ? "The agent is working; your message waits its turn…" : "Message the agent (Enter sends, Shift+Enter for a new line)") : `${noModel}: set MODEL_API_KEY (Muse Spark) or MUSE_API_KEY (Muse Glimmer) to talk to the agent.`}
          aria-label="Message"
          className="min-h-[44px] resize-none"
        />
        <Button variant="accent" onClick={send} disabled={!hasModel || !text.trim() || post.isPending} aria-label="Send">
          <Send />
        </Button>
      </div>
    </div>
  );
}
