import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
import { nodeAddress } from "@/features/network/view";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

const eur = new Intl.NumberFormat("en-GB", { style: "currency", currency: "EUR", maximumFractionDigits: 0 });

/** Euro amounts: millions as "€12.3M", smaller in full. */
export function formatEur(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (Math.abs(value) >= 1e6) return `€${(value / 1e6).toLocaleString("en-GB", { maximumFractionDigits: 1 })}M`;
  return eur.format(value);
}

export function formatPct(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined) return "—";
  return `${value.toLocaleString("en-GB", { maximumFractionDigits: digits, minimumFractionDigits: digits })}%`;
}

export function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return value.toLocaleString("en-GB");
}

export function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (typeof value === "number") return value.toLocaleString("en-GB", { maximumFractionDigits: 4 });
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export function formatTime(ts: string): string {
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  return d.toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" });
}

/** Display name of a field or type: `recovery_time_months` -> "Recovery time months". */
export function humanize(name: string): string {
  const s = name.replace(/_/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** Where a node opens: the Overview with the node selected (sites and suppliers) or in the side panel (others). */
export function explorerPath(type: string, id: string): string {
  return nodeAddress(type, id);
}
