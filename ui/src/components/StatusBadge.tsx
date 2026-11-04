import { AlertTriangle, CheckCircle2, Circle, Clock, Lock, Trash2, XCircle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

const STYLES: Record<string, { label: string; className: string; Icon: typeof Circle }> = {
  accepted: { label: "accepted", className: "text-ok border-ok/40 bg-ok/10", Icon: CheckCircle2 },
  active: { label: "active", className: "text-ok border-ok/40 bg-ok/10", Icon: CheckCircle2 },
  proposed: { label: "proposed", className: "text-proposed border-proposed/40 bg-proposed/10", Icon: Clock },
  pending: { label: "pending", className: "text-proposed border-proposed/40 bg-proposed/10", Icon: Clock },
  draft: { label: "draft", className: "text-proposed border-proposed/40 bg-proposed/10", Icon: Clock },
  approved: { label: "approved", className: "text-ok border-ok/40 bg-ok/10", Icon: CheckCircle2 },
  flagged: { label: "flagged", className: "text-conflict border-conflict/40 bg-conflict/10", Icon: AlertTriangle },
  conflict: { label: "conflict", className: "text-conflict border-conflict/40 bg-conflict/10", Icon: AlertTriangle },
  rejected: { label: "rejected", className: "text-muted border-border bg-surface-2", Icon: XCircle },
  superseded: { label: "superseded", className: "text-muted border-border bg-surface-2", Icon: Circle },
  deleted: { label: "deleted", className: "text-muted border-border bg-surface-2 line-through", Icon: Trash2 },
  locked: { label: "locked", className: "text-brand border-brand/40 bg-brand-soft", Icon: Lock },
};

/** A status as icon and word, never colour alone. */
export function StatusBadge({ status, className }: { status: string; className?: string }) {
  const style = STYLES[status] ?? { label: status, className: "text-muted border-border", Icon: Circle };
  const { Icon } = style;
  return (
    <Badge className={cn(style.className, className)}>
      <Icon aria-hidden />
      {style.label}
    </Badge>
  );
}

const METHOD: Record<string, string> = {
  mapping: "table mapping",
  parser: "parser",
  llm: "model",
  human: "person",
};

export function MethodBadge({ method }: { method: string }) {
  return (
    <Badge className={cn("border-border text-muted", method === "human" && "border-brand/40 text-brand", method === "llm" && "border-interactive/40 text-interactive")}>
      {METHOD[method] ?? method}
    </Badge>
  );
}
