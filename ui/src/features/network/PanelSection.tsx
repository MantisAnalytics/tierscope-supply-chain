import { ChevronDown, ChevronRight } from "lucide-react";
import { useState, type ReactNode } from "react";

/** One titled section of the side panel; with `collapsed`, the body shows only once the title is clicked. */
export function Section({ title, children, action, collapsed }: { title: string; children: ReactNode; action?: ReactNode; collapsed?: boolean }) {
  const [open, setOpen] = useState(!collapsed);
  const heading = <h3 className="text-xs font-semibold tracking-wide text-muted uppercase">{title}</h3>;
  return (
    <section className="border-t px-4 py-3" aria-label={title}>
      <div className="mb-1 flex items-center justify-between">
        {collapsed ? (
          <button className="flex cursor-pointer items-center gap-1" aria-expanded={open} onClick={() => setOpen(!open)}>
            {open ? <ChevronDown className="size-3.5 text-muted" /> : <ChevronRight className="size-3.5 text-muted" />}
            {heading}
          </button>
        ) : (
          heading
        )}
        {action}
      </div>
      {open && children}
    </section>
  );
}

/** A cell that opens a node: the main text with an optional muted line under it. */
export function OpenCell({ label, sub, onClick, title }: { label: string; sub?: string | null; onClick: () => void; title?: string }) {
  return (
    <button className="flex w-full min-w-0 cursor-pointer flex-col text-left hover:underline" onClick={onClick} title={title ?? sub ?? label}>
      <span className="truncate font-medium">{label}</span>
      {sub && <span className="truncate text-xs text-muted">{sub}</span>}
    </button>
  );
}
