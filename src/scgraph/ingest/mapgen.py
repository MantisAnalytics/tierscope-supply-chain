"""Mapping spec generation: one model call per data or parameters region."""

from __future__ import annotations

from typing import Any

from scgraph.ingest.classify import RegionPlan
from scgraph.ingest.layout import layout_of
from scgraph.ingest.profile import ColumnProfile, Profiler, _short
from scgraph.ingest.prompts import load_prompt
from scgraph.ingest.step import StepResult, call_tool
from scgraph.llm import LLMClient
from scgraph.mapping.execute import execute
from scgraph.mapping.spec import SpecError, parse_spec
from scgraph.mapping.tables import Region, TableError, is_blank

PREVIEW_ROWS = 15
ROLES = ("key", "fact", "derived", "ignore")

SUBMIT_MAPPING = {
    "name": "submit_mapping",
    "description": "Submit the mapping spec for this region: what each row produces and a decision per column.",
    "input_schema": {
        "type": "object",
        "properties": {
            "produces": {"type": "array", "items": {"type": "object"},
                         "description": "entity and relation entries, see the format in the instructions"},
            "columns": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "header": {"type": "string", "description": "header text, or column letter if headerless"},
                        "role": {"type": "string", "enum": list(ROLES)},
                        "target": {"type": "string", "description": "Type.field, for role fact"},
                        "metric": {"type": "string", "description": "for role derived"},
                        "entity": {"type": "string", "description": "entity the derived value describes"},
                        "transform": {"description": "optional transform, see instructions"},
                        "confidence": {"type": "number"},
                        "rationale": {"type": "string"},
                    },
                    "required": ["header", "role", "confidence", "rationale"],
                },
            },
            "parameters": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}, "cell": {"type": "string"},
                                   "label_cell": {"type": "string"}, "label": {"type": "string"},
                                   "confidence": {"type": "number"}, "rationale": {"type": "string"}},
                    "required": ["name", "cell"],
                },
            },
        },
        "required": ["produces", "columns"],
    },
}


class Mapper:
    """Builds and checks generated specs for the regions of one file."""

    def __init__(self, client: LLMClient, profiler: Profiler, plans: list[RegionPlan], *, run_id: str) -> None:
        self.client, self.profiler, self.plans, self.run_id = client, profiler, plans, run_id

    def read(self, plan: RegionPlan) -> tuple[Region, list[ColumnProfile]]:
        return self.profiler.region(plan.sheet, header_row=plan.header_row, start=plan.start_row,
                                    end=plan.end_row, stop_at_blank=plan.stop_at_blank)

    def view(self, plan: RegionPlan) -> dict[str, Any]:
        region, columns = self.read(plan)
        preview = [[f"{k}{row.number}={_short(v)}" for k, v in row.cells.items() if not is_blank(v)]
                   for row in region.rows[:PREVIEW_ROWS]]
        return {
            "region": {"id": plan.id, "sheet": plan.sheet, "kind": plan.kind, "header_row": plan.header_row,
                       "start_row": plan.start_row, "end_row": plan.end_row, "stop_at_blank": plan.stop_at_blank,
                       "describes": plan.describes, "rows": len(region.rows)},
            "columns": [c.to_dict() for c in columns],
            "preview": preview,
            "semi_structured": [{"column": s.column, "targets": s.targets} for s in plan.semi_structured],
            "other_regions": [{"id": p.id, "sheet": p.sheet, "describes": p.describes}
                              for p in self.plans if p.kind == "data" and p.id != plan.id],
        }

    def generate(self, plan: RegionPlan) -> StepResult[dict[str, Any]]:
        return call_tool(self.client, system=load_prompt("table_map", plan.domains), user=self.view(plan),
                         tool=SUBMIT_MAPPING, parse=lambda data: self.build(plan, data),
                         lenient=lambda data: self.build(plan, data, allow_no_parameters=True))

    def repair(self, plan: RegionPlan, spec: dict[str, Any], findings: dict[str, Any]) -> StepResult[dict[str, Any]]:
        user = {"previous_spec": _decisions(spec), "findings": findings, **self.view(plan)}
        return call_tool(self.client, system=load_prompt("table_repair", plan.domains), user=user,
                         tool=SUBMIT_MAPPING, parse=lambda data: self.build(plan, data),
                         lenient=lambda data: self.build(plan, data, allow_no_parameters=True))

    def build(self, plan: RegionPlan, data: dict[str, Any], *, allow_no_parameters: bool = False) -> dict[str, Any]:
        """Model output -> graph-core spec with authoring metadata and layout. Raises ValueError when invalid.

        A parameters region without parameters is asked again once; the second answer may confirm there are none.
        """
        region, profiles = self.read(plan)
        spec: dict[str, Any] = {
            "version": 1,
            "file_kind": self.profiler.table.kind,
            "metadata": {"author": "model", "model": self.client.default_model, "run_id": self.run_id,
                         "status": "draft", "source_file": self.profiler.path.name, "region_kind": plan.kind,
                         "describes": plan.describes},
            "regions": [],
            "parameters": [],
        }
        if plan.kind == "parameters":
            labels = {}
            for p in data.get("parameters") or []:
                entry = {"name": p["name"], "sheet": plan.sheet, "cell": p["cell"].upper()}
                if p.get("label_cell"):
                    entry["label"] = {"cell": p["label_cell"].upper(), "equals": p.get("label", "")}
                    labels[entry["label"]["cell"]] = entry["label"]["equals"]
                entry["confidence"] = p.get("confidence", 1.0)
                entry["rationale"] = p.get("rationale", "")
                spec["parameters"].append(entry)
            if not spec["parameters"] and not allow_no_parameters:
                raise ValueError("this parameters region returned no `parameters`; list every cell that holds a "
                                 "listed parameter, or return an empty list again if none does")
            spec["layout"] = {"kind": "parameters", "sheet": plan.sheet, "labels": labels}
        else:
            columns, keys, errors = {}, {}, []
            for c in data["columns"]:
                header = c["header"]
                if region.column(header) is None:
                    errors.append(f"column {header!r} is not in the region")
                    continue
                if c["role"] not in ROLES:
                    errors.append(f"column {header!r}: role must be one of {ROLES}")
                    continue
                entry = {"role": c["role"], "confidence": float(c["confidence"]), "rationale": c.get("rationale", "")}
                for name in ("target", "metric", "entity", "transform"):
                    if c.get(name) not in (None, ""):
                        entry[name] = c[name]
                (keys if c["role"] == "key" else columns)[header] = entry
            decided = {region.column(h) for h in (*columns, *keys)}
            undecided = [p.header or p.column for p in profiles if p.fill_rate > 0 and p.column not in decided]
            if undecided:
                errors.append(f"columns without a decision: {undecided}")
            if errors:
                raise ValueError("; ".join(errors))
            rows: dict[str, Any] = {"start": plan.start_row}
            if plan.end_row:
                rows["end"] = plan.end_row
            if plan.stop_at_blank:
                rows["stop_at_blank"] = plan.stop_at_blank
            spec["regions"] = [{
                "id": plan.id, "sheet": plan.sheet, "header_row": plan.header_row, "rows": rows,
                "confidence": 1.0, "produces": data["produces"], "columns": columns, "key_columns": keys,
            }]
            spec["layout"] = {"kind": "region", "sheet": plan.sheet, "header_row": plan.header_row,
                              **layout_of(profiles)}
            spec["semi_structured"] = [{"column": s.column, "targets": s.targets} for s in plan.semi_structured]
        try:
            execute(parse_spec(spec), self.profiler.table)
        except (SpecError, TableError) as e:
            raise ValueError(str(e)) from None
        return spec


def _decisions(spec: dict[str, Any]) -> dict[str, Any]:
    """The parts of a spec the model wrote, for the repair prompt."""
    if not spec["regions"]:
        return {"parameters": spec["parameters"]}
    region = spec["regions"][0]
    return {"produces": region["produces"], "columns": region["columns"], "key_columns": region.get("key_columns", {})}
