import { Boxes, Building2, Cog, Factory, Flag, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

export const TYPE_ICONS: Record<string, LucideIcon> = {
  Assembly: Boxes,
  Component: Cog,
  Supplier: Building2,
  Site: Factory,
  RiskIndicator: Flag,
};

/** Type colours shared by the graph, the map and lists (both themes read well on them). Kept apart from the
 * interactive blue, which marks selection, and rose (the risk role) only for risk indicators. */
export const TYPE_COLORS: Record<string, string> = {
  Assembly: "#9B4DEB",
  Component: "#0E9AA7",
  Supplier: "#5F6AE8",
  Site: "#1F9D55",
  RiskIndicator: "#D6336C",
};

export function TypeIcon({ type, className }: { type: string; className?: string }) {
  const Icon = TYPE_ICONS[type] ?? Cog;
  return <Icon className={cn("size-4 shrink-0", className)} style={{ color: TYPE_COLORS[type] }} aria-label={type} />;
}
