import type { components } from "./schema";

type S = components["schemas"];
export type Stats = S["Stats"];
export type Overview = S["Overview"];
export type Ranked = S["Ranked"];
export type JournalEntry = S["JournalEntry"];
export type Network = S["Network"];
export type NetworkEntity = S["NetworkEntity"];
export type NetworkPart = S["NetworkPart"];
export type SupplyLink = S["SupplyLink"];
export type TierLink = S["TierLink"];
export type Subgraph = S["Subgraph"];
export type GraphNode = S["GraphNode"];
export type GraphEdge = S["GraphEdge"];
export type NodeDetail = S["NodeDetail"];
export type NodeEdge = S["NodeEdge"];
export type FieldValue = S["FieldValueOut"];
export type ExposureDetail = S["ExposureDetail"];
export type SearchHit = S["SearchHit"];
export type Proposal = S["Proposal"];
export type ProposalDetail = S["ProposalDetail"];
export type FactView = S["FactView"];
export type Impact = S["Impact"];
export type Effect = S["Effect"];
export type Decision = S["Decision"];
export type EditResult = S["EditResult"];
export type EvidenceOut = S["EvidenceOut"];
export type UndoResult = S["UndoResult"];
export type IngestReport = S["IngestReport"];
export type SpecSummary = S["SpecSummary"];
export type SpecDetail = S["SpecDetail"];

/** One failing edit of a refused batch. */
export interface EditError {
  index: number;
  message: string;
}

/** A refused request: the server's message, and for edit batches the error of each failing edit. */
export class ApiError extends Error {
  status: number;
  errors: EditError[];
  detail: unknown;

  constructor(status: number, message: string, detail: unknown, errors: EditError[] = []) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.errors = errors;
  }
}

const AUTHOR_KEY = "tierscope.author";

export function storedAuthor(): string | null {
  try {
    return localStorage.getItem(AUTHOR_KEY)?.trim() || null;
  } catch {
    return null;
  }
}

export function storeAuthor(name: string): void {
  localStorage.setItem(AUTHOR_KEY, name.trim());
}

function messageOf(status: number, detail: unknown): { message: string; errors: EditError[] } {
  if (typeof detail === "string") return { message: detail, errors: [] };
  if (Array.isArray(detail)) {
    // request validation: [{loc, msg}]
    const parts = detail.map((d: { loc?: unknown[]; msg?: string }) =>
      [d.loc?.filter((x) => x !== "body").join("."), d.msg].filter(Boolean).join(": "),
    );
    return { message: parts.join("; ") || `request failed (${status})`, errors: [] };
  }
  if (detail && typeof detail === "object") {
    const d = detail as { message?: string; errors?: EditError[] };
    return { message: d.message ?? `request failed (${status})`, errors: d.errors ?? [] };
  }
  return { message: `request failed (${status})`, errors: [] };
}

export interface RequestOptions {
  method?: "GET" | "POST";
  json?: unknown;
  form?: FormData;
  signal?: AbortSignal;
}

/** Fetch an API path; changes carry the stored author in `X-Author`. Throws `ApiError` on refusal. */
export async function api<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = options.method ?? (options.json !== undefined || options.form ? "POST" : "GET");
  const headers: Record<string, string> = { Accept: "application/json" };
  let body: BodyInit | undefined;
  if (options.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.json);
  } else if (options.form) {
    body = options.form;
  }
  const author = storedAuthor();
  if (method !== "GET" && author) headers["X-Author"] = author;
  const response = await fetch(`/api${path}`, { method, headers, body, signal: options.signal });
  const text = await response.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!response.ok) {
    const detail = data && typeof data === "object" && "detail" in data ? (data as { detail: unknown }).detail : data;
    const { message, errors } = messageOf(response.status, detail);
    throw new ApiError(response.status, message, detail, errors);
  }
  return data as T;
}

export function query(params: Record<string, string | number | boolean | null | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== null && v !== undefined && v !== "") q.set(k, String(v));
  const s = q.toString();
  return s ? `?${s}` : "";
}

export const nodePath = (type: string, id: string) => `${encodeURIComponent(type)}/${id.split("/").map(encodeURIComponent).join("/")}`;
