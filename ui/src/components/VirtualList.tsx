import { useVirtualizer } from "@tanstack/react-virtual";
import { useRef, type ReactNode } from "react";
import { cn } from "@/lib/utils";

/** A scrolling list that renders only the rows in view (rows may differ in height). */
export function VirtualList<T>({ items, estimate, render, getKey, className, label, role = "list", itemRole = "listitem" }: {
  items: T[];
  estimate: number;
  render: (item: T, index: number) => ReactNode;
  getKey: (item: T, index: number) => string | number;
  className?: string;
  label: string;
  role?: string;
  itemRole?: string;
}) {
  const scroller = useRef<HTMLDivElement>(null);
  const virtual = useVirtualizer({
    count: items.length,
    getScrollElement: () => scroller.current,
    estimateSize: () => estimate,
    overscan: 8,
    initialRect: { width: 800, height: 800 },
    getItemKey: (i) => getKey(items[i], i),
  });
  return (
    <div ref={scroller} className={cn("overflow-auto", className)} role={role} aria-label={label}>
      <div style={{ height: virtual.getTotalSize(), position: "relative" }}>
        {virtual.getVirtualItems().map((row) => (
          <div
            key={row.key}
            role={itemRole}
            data-index={row.index}
            ref={virtual.measureElement}
            className="absolute inset-x-0"
            style={{ transform: `translateY(${row.start}px)` }}
          >
            {render(items[row.index], row.index)}
          </div>
        ))}
      </div>
    </div>
  );
}
