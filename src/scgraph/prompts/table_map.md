You map one table region of a manufacturer's spreadsheet onto a supply-chain graph schema by writing a mapping spec. Code will execute the spec on every row, so decide each column once for the whole region.

The spec has two parts.

`produces`: what each row creates.
- An entity: `{"entity": "Supplier", "key": {"column": "<header>"}}`. For an entity with several key parts give a list of refs in the key order of the schema: `"key": [{"column": ...}, {"column": ...}]`.
- A relation: `{"relation": "SUPPLIES", "from": {"column": "<header>"}, "to": {"column": "<header>"}, "identity": {"site": {"column": "<header>"}}, "when_present": ["<header>"]}`. `from` and `to` are the key columns of the two endpoints. When an endpoint entity has a multi-part key, give the same list of refs as that entity's key, e.g. `"to": [{"column": "Supplier ID"}, {"column": "Category"}, {"column": "Indicator"}]`. `identity` gives the relation's context fields from the schema; fill every identity field the row has a column for, including optional ones, because identity fields are part of the edge's key. `when_present` is a list of column headers that must be non-empty for the relation to exist in a row; use it whenever the relation is optional in the data.
- Produce an entity only in the region that describes it (its master table, given as `describes`). A column that merely references another region's entities by id is a relation endpoint or identity; do not produce that entity again.
- A ref can be a constant instead of a column: `{"const": "value"}`.

`columns`: one entry for every column header of the region, each with a `role`:
- `key`: the column is only used in `produces` as a key, endpoint or identity.
- `fact`: the value is a field: `"target": "Type.field"` where Type is an entity or relation the region produces.
- `derived`: the value is computed from other data (formulas, lookups into other sheets, totals, exposure or impact figures). Never load it as a fact. Give `"metric"` and `"entity"` (the produced entity it describes).
- `ignore`: no usable data for the schema, or it duplicates another sheet. Say why.
Give `transform` only when the default for the field type is not enough: `{"bool": {"true": ["Yes"], "false": ["No"]}}`, `{"number": {"thousands": ".", "decimal": ","}}`, `{"date": {"format": "%d.%m.%Y"}}`, `[{"empty": ["n/a"]}, "number"]`, `{"constant": "EUR"}`. Defaults: numbers accept `1,234.5` and `12%`, ints accept `3.0`, dates accept ISO and `dd.mm.yyyy`, text is trimmed, empty cells are skipped.

Every column also gets `confidence`: the probability that your decision for this column is correct (0..1), and `rationale`: one short sentence.

For a `parameters` region, return `parameters` instead of entity rows: `{"name": "<parameter name>", "cell": "B30", "label_cell": "A30", "label": "<exact label text>"}` for each cell that holds one of the listed parameters, and give every column `role: ignore`. Return an empty `parameters` list when no cell holds a listed parameter; never add placeholders.

Rules:
- Use only types, fields and identities from the schema below, and the metric and parameter names listed there when they fit.
- `derived` is for outputs of an analysis and copies of values held elsewhere: exposure, impact, totals, rankings, lookups. A column with the `computed` flag is usually `derived`, but a computed column that is an input the schema has a field for (for example revenue split by a formula) is a `fact`. A column named like an exposure, impact, total or score is `derived` even without formulas. A column without the `computed` flag that holds entered values for a schema field (such as a site's recovery time) is a `fact`, even if another sheet derives something with the same name from it.
- An id column that points at another sheet's entities is an endpoint or identity of a relation, not a text field.
- Keep the raw text of a semi-structured column as a `fact` on a text field when the schema has one.
- Map every column whose meaning matches a schema field as a `fact`, even when it is sparse. `ignore` is for columns with no matching field, wholly empty columns and duplicates of other sheets. Never invent values.

Call the tool `submit_mapping` exactly once.

{schema}
