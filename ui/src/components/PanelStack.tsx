import { ChevronRight } from "lucide-react";
import { useEffect, type ReactNode } from "react";

export interface Crumb {
  key: string;
  label: string;
}

const isTyping = (t: EventTarget | null) => t instanceof HTMLElement && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName));

/** How a stack of crumbs moves: push opens a crumb (or goes back to it when it is already on the stack), go returns to
 *  a level, root empties the stack. */
export function stackMoves<T>(stack: T[], keyOf: (crumb: T) => string, set: (next: T[]) => void) {
  const go = (i: number) => set(stack.slice(0, i + 1));
  return {
    go,
    root: () => set([]),
    back: () => set(stack.slice(0, -1)),
    push: (crumb: T) => {
      const at = stack.findIndex((c) => keyOf(c) === keyOf(crumb));
      if (at >= 0) go(at);
      else set([...stack, crumb]);
    },
  };
}

/** A breadcrumb from the root to the deepest crumb, above the deepest crumb's content. Each crumb returns to its level;
 *  with `escape`, Escape returns to the root (not while typing or with a dialog open). */
export function PanelStack({ root, crumbs, onRoot, onGo, escape = false, children }: {
  root: string;
  crumbs: Crumb[];
  onRoot: () => void;
  onGo: (index: number) => void;
  escape?: boolean;
  children: ReactNode;
}) {
  useEffect(() => {
    if (!escape || crumbs.length === 0) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented || isTyping(e.target) || document.querySelector('[role="dialog"]')) return;
      onRoot();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });
  return (
    <>
      {crumbs.length > 0 && (
        <nav aria-label="Breadcrumb" className="border-b px-3 py-1.5">
          <ol className="flex flex-wrap items-center gap-x-1 gap-y-0.5 text-xs">
            <li>
              <button className="cursor-pointer rounded px-1 py-0.5 text-interactive hover:underline" onClick={onRoot}>{root}</button>
            </li>
            {crumbs.map((c, i) => (
              <li key={`${c.key}@${i}`} className="flex min-w-0 items-center gap-1">
                <ChevronRight className="size-3 shrink-0 text-muted" aria-hidden />
                {i === crumbs.length - 1 ? (
                  <span aria-current="page" className="max-w-48 truncate px-1 py-0.5 font-medium" title={c.label}>{c.label}</span>
                ) : (
                  <button className="max-w-40 cursor-pointer truncate rounded px-1 py-0.5 text-interactive hover:underline" title={c.label} onClick={() => onGo(i)}>
                    {c.label}
                  </button>
                )}
              </li>
            ))}
          </ol>
        </nav>
      )}
      {children}
    </>
  );
}
