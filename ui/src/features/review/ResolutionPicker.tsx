import { Link } from "react-router-dom";
import { explorerPath } from "@/lib/utils";

export type Resolution = { target_id: string } | { new_node: true } | null;

/** For a mention: one of the candidate nodes, or a new node named after the mention. */
export function ResolutionPicker({ mention, candidates, type, value, onChange }: {
  mention: string;
  candidates: string[];
  type: string;
  value: Resolution;
  onChange: (r: Resolution) => void;
}) {
  return (
    <fieldset className="grid gap-1.5">
      <legend className="mb-1 text-sm">
        Which {type} does <strong>“{mention}”</strong> mean?
      </legend>
      {candidates.map((c) => (
        <div key={c} className="flex items-center gap-2 rounded-md border px-2.5 py-1.5 text-sm has-[:checked]:border-interactive has-[:checked]:bg-interactive-soft">
          <label className="flex flex-1 cursor-pointer items-center gap-2">
            <input type="radio" name="resolution" checked={!!value && "target_id" in value && value.target_id === c} onChange={() => onChange({ target_id: c })} />
            <span>{c}</span>
          </label>
          <Link to={explorerPath(type, c)} className="text-xs text-interactive hover:underline">view</Link>
        </div>
      ))}
      <label className="flex items-center gap-2 rounded-md border px-2.5 py-1.5 text-sm has-[:checked]:border-interactive has-[:checked]:bg-interactive-soft">
        <input type="radio" name="resolution" checked={!!value && "new_node" in value} onChange={() => onChange({ new_node: true })} />
        <span>None of these: create a new {type}</span>
      </label>
    </fieldset>
  );
}
