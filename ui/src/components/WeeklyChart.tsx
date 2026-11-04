import { useId, useState } from "react";
import { cn, formatEur } from "@/lib/utils";

export interface Series {
  name: string;
  values: number[];
}

// Fixed series colours by role; the rest cycle. CSS variables so both themes work.
const ROLE_COLOURS: Record<string, string> = {
  "Do nothing": "var(--risk)",
  "Standard playbook": "var(--proposed)",
  "Optimiser reference": "var(--muted)",
  "Agent plan": "var(--brand)",
  "Accepted plan": "var(--ok)",
};
const CYCLE = ["var(--brand)", "var(--interactive)", "var(--ok)", "var(--proposed)", "var(--lavender)"];

export function seriesColour(name: string, index: number): string {
  return ROLE_COLOURS[name] ?? CYCLE[index % CYCLE.length];
}

/** A small inline-SVG line chart of weekly values (no chart library, no animation). */
export function WeeklyChart({
  series,
  height = 180,
  format = formatEur,
  className,
  label,
}: {
  series: Series[];
  height?: number;
  format?: (v: number) => string;
  className?: string;
  label: string;
}) {
  const id = useId();
  const [hover, setHover] = useState<number | null>(null);
  const weeks = Math.max(1, ...series.map((s) => s.values.length));
  const max = Math.max(1, ...series.flatMap((s) => s.values));
  const width = 640;
  const pad = { l: 56, r: 12, t: 10, b: 24 };
  const x = (w: number) => pad.l + (w / Math.max(1, weeks - 1)) * (width - pad.l - pad.r);
  const y = (v: number) => pad.t + (1 - v / max) * (height - pad.t - pad.b);
  const ticks = [0, 0.5, 1].map((f) => f * max);
  const step = weeks > 20 ? 4 : weeks > 10 ? 2 : 1;
  return (
    <figure className={cn("flex flex-col gap-2", className)}>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="w-full"
        role="img"
        aria-labelledby={`${id}-title`}
        onMouseLeave={() => setHover(null)}
      >
        <title id={`${id}-title`}>{label}</title>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={pad.l} x2={width - pad.r} y1={y(t)} y2={y(t)} stroke="var(--border)" strokeWidth={1} />
            <text x={pad.l - 6} y={y(t) + 4} textAnchor="end" fontSize={10} fill="var(--muted)" className="tabular">
              {format(t)}
            </text>
          </g>
        ))}
        {Array.from({ length: weeks }, (_, w) => w)
          .filter((w) => w % step === 0)
          .map((w) => (
            <text key={w} x={x(w)} y={height - 6} textAnchor="middle" fontSize={10} fill="var(--muted)" className="tabular">
              {w}
            </text>
          ))}
        {series.map((s, i) => (
          <polyline
            key={s.name}
            fill="none"
            stroke={seriesColour(s.name, i)}
            strokeWidth={2}
            strokeLinejoin="round"
            strokeDasharray={s.name === "Do nothing" ? "5 3" : undefined}
            points={s.values.map((v, w) => `${x(w)},${y(v)}`).join(" ")}
          />
        ))}
        {hover !== null && (
          <line x1={x(hover)} x2={x(hover)} y1={pad.t} y2={height - pad.b} stroke="var(--muted)" strokeWidth={1} strokeDasharray="2 2" />
        )}
        {Array.from({ length: weeks }, (_, w) => (
          <rect
            key={w}
            x={x(w) - (width - pad.l - pad.r) / weeks / 2}
            y={pad.t}
            width={(width - pad.l - pad.r) / weeks}
            height={height - pad.t - pad.b}
            fill="transparent"
            onMouseEnter={() => setHover(w)}
          />
        ))}
      </svg>
      <figcaption className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted">
        {series.map((s, i) => (
          <span key={s.name} className="flex items-center gap-1.5">
            <span className="inline-block h-0.5 w-4" style={{ background: seriesColour(s.name, i) }} aria-hidden />
            {s.name}
            {hover !== null && <span className="font-medium text-fg tabular">{format(s.values[hover] ?? 0)}</span>}
          </span>
        ))}
        <span className="ml-auto">{hover !== null ? `week ${hover}` : "weeks from now"}</span>
      </figcaption>
    </figure>
  );
}
