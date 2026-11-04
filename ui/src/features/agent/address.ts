import { useCallback, useMemo } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";

/** What the detail column can show above the incident state; `node` ids are `Type:id`, `steps` ids are run ids. */
export const CRUMB_KINDS = ["plan", "sim", "edit", "draft", "task", "steps", "report", "node"] as const;
export type CrumbKind = (typeof CRUMB_KINDS)[number];

export interface DetailCrumb {
  kind: CrumbKind;
  id: string;
}

export const crumbKey = (c: DetailCrumb) => `${c.kind}:${c.id}`;

function parseCrumb(value: string): DetailCrumb | null {
  const at = value.indexOf(":");
  const kind = (at < 0 ? value : value.slice(0, at)) as CrumbKind;
  if (!CRUMB_KINDS.includes(kind)) return null;
  const id = at < 0 ? "" : value.slice(at + 1);
  if (kind === "node" ? !/^[^:]+:.+/.test(id) : kind !== "report" && !id) return null;
  return { kind, id };
}

/** The `d` param from the raw query: entries are split on commas before decoding, so an id may hold a comma. */
export function parseStack(search: string): DetailCrumb[] {
  const raw = /(?:^|[?&])d=([^&]*)/.exec(search)?.[1];
  if (!raw) return [];
  return raw.split(",").flatMap((part) => {
    try {
      return parseCrumb(decodeURIComponent(part.replace(/\+/g, " "))) ?? [];
    } catch {
      return []; // malformed escape
    }
  });
}

const readable = (value: string) => encodeURIComponent(value).replace(/%3A/gi, ":");

export function serialiseStack(stack: DetailCrumb[]): string {
  return stack.length ? `?d=${stack.map((c) => readable(c.id ? crumbKey(c) : c.kind)).join(",")}` : "";
}

export const agentPath = (threadId?: number, stack: DetailCrumb[] = []) =>
  threadId === undefined ? "/agent" : `/agent/t/${threadId}${serialiseStack(stack)}`;

/** The open thread and the detail column's stack, both in the address. Opening a crumb is a new history entry, so
 *  Back steps out of it. */
export function useAgentAddress(base = "/agent/t") {
  const params = useParams();
  const { search } = useLocation();
  const navigate = useNavigate();
  const threadId = params.threadId !== undefined && /^\d+$/.test(params.threadId) ? Number(params.threadId) : undefined;
  const stack = useMemo(() => parseStack(search), [search]);
  const setStack = useCallback(
    (next: DetailCrumb[]) => {
      if (threadId === undefined) return;
      navigate(`${base}/${threadId}${serialiseStack(next)}`);
    },
    [navigate, threadId, base],
  );
  return { threadId, stack, setStack };
}
