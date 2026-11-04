import { cn } from "@/lib/utils";

/** Confidence as three steps plus the number: low < 0.7, medium < 0.9, high (the review threshold). */
export function confidenceLevel(c: number | null | undefined): "high" | "medium" | "low" | "none" {
  if (c === null || c === undefined) return "none";
  if (c >= 0.9) return "high";
  if (c >= 0.7) return "medium";
  return "low";
}

const FILL = { high: "bg-ok", medium: "bg-proposed", low: "bg-conflict", none: "bg-border" };
const STEPS = { high: 3, medium: 2, low: 1, none: 0 };

export function ConfidenceBar({ value, className }: { value: number | null | undefined; className?: string }) {
  const level = confidenceLevel(value);
  return (
    <span
      className={cn("inline-flex items-center gap-1.5", className)}
      title={`confidence ${level}`}
      aria-label={`confidence ${value === null || value === undefined ? "unknown" : value.toFixed(2)} (${level})`}
    >
      <span className="inline-flex gap-0.5" aria-hidden>
        {[1, 2, 3].map((i) => (
          <span key={i} className={cn("h-2.5 w-1.5 rounded-sm", i <= STEPS[level] ? FILL[level] : "bg-border")} />
        ))}
      </span>
      <span className="tabular text-xs text-muted">{value === null || value === undefined ? "—" : value.toFixed(2)}</span>
    </span>
  );
}

/** Compact form for table rows: a coloured dot and the number, which keeps it readable without colour. */
export function ConfidenceDot({ value }: { value: number | null | undefined }) {
  const level = confidenceLevel(value);
  return (
    <span
      className="inline-flex items-center gap-1"
      title={`confidence ${level}`}
      aria-label={`confidence ${value === null || value === undefined ? "unknown" : value.toFixed(2)} (${level})`}
    >
      <span className={cn("size-2 rounded-full", FILL[level])} aria-hidden />
      <span className="tabular text-xs">{value === null || value === undefined ? "—" : value.toFixed(2)}</span>
    </span>
  );
}
