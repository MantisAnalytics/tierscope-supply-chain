import { useQuery } from "@tanstack/react-query";
import { api } from "./client";

export interface EntityDef {
  identity: string[];
  fields: Record<string, string>;
}
export interface RelationDef {
  from: string;
  to: string;
  identity: Record<string, string>; // field -> referenced entity type or value type
  fields: Record<string, string>;
  occurrence_fields: string[];
}
export interface GraphSchema {
  entities: Record<string, EntityDef>;
  relations: Record<string, RelationDef>;
}

type Raw = Record<string, { entities: Record<string, EntityDef>; relations: Record<string, RelationDef> }>;

/** The schema's domains merged into one set of entity and relation types. */
export function flatten(raw: Raw): GraphSchema {
  const out: GraphSchema = { entities: {}, relations: {} };
  for (const domain of Object.values(raw)) {
    Object.assign(out.entities, domain.entities);
    Object.assign(out.relations, domain.relations);
  }
  return out;
}

export const useSchema = () =>
  useQuery({ queryKey: ["schema"], queryFn: async () => flatten(await api<Raw>("/schema")), staleTime: Infinity });

/** A typed value from form text: numbers, booleans, else the text. */
export function parseValue(text: string, type: string | undefined): unknown {
  const t = text.trim();
  if (type === "int" || type === "float") {
    const n = Number(t.replace(",", "."));
    return t === "" || Number.isNaN(n) ? t : n;
  }
  if (type === "bool") return ["yes", "true", "1", "y"].includes(t.toLowerCase()) ? true : ["no", "false", "0", "n"].includes(t.toLowerCase()) ? false : t;
  return t;
}
