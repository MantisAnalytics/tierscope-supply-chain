# Tierscope manual

The full reference for commands, the API, the web app and configuration. For a short introduction, see the [README](../README.md).

Operations (disruptions, the response agent, decisions, tasks, reports): [operations.md](operations.md).

Contents: [Graph](#graph) · [Ingest](#ingest) · [Review](#review) · [Editing](#editing) · [API](#api) · [Web app](#web-app) · [Documents](#documents) · [Tests](#tests) · [LLM usage](#llm-usage)

## Graph

The supply-chain graph lives in a SQLite database, `.cache/graph.sqlite` by default (set `SCGRAPH_GRAPH_DB` to move it; `.cache/` is git-ignored). Data gets in only as facts: a mapping spec says how each table region of a file maps to entities, relations and fields of the canonical schema, and a deterministic executor applies it to every row. Every field value keeps its source cell, extraction method, confidence and status (`active`; `flagged` while sources disagree; `proposed`, `rejected` or `superseded` under review, see [Review](#review)). Values from derived columns (Business Exposure, Business Impact) are stored apart as oracle values and never become graph fields.

Load a workbook with a hand-written spec, when you have one (`--reset` clears the database first; loading the same file again changes nothing). Without one, use `scgraph ingest` (below), where the model writes the spec:

```bash
uv run scgraph graph load data/<workbook>.xlsx --spec <your-spec>.json --reset
uv run scgraph graph stats                     # counts per type, flagged and referenced-only nodes
uv run scgraph graph show Supplier SUP-0001     # fields with provenance, and edges
uv run scgraph graph schema                    # domains, types, fields
```

Exposure is computed from the current graph with the data-capture template's own rules (reproduced in `src/scgraph/exposure/compute.py`):

```bash
uv run scgraph exposure show Component CMP-9552  # BE, adjusted BE, impacted assemblies with BOM paths
uv run scgraph exposure show Supplier SUP-0001    # BE, recovery time, Business Impact in EUR
uv run scgraph exposure top Supplier -n 10       # ranking by Business Exposure
uv run scgraph exposure check                    # compare with the oracle values; exit 1 on a mismatch
```

`exposure check` lists cells the spec marks as `known_anomalies` separately: values typed over a workbook's own formulas differ from our numbers without failing the check.

## Ingest

`scgraph ingest` builds the graph from a table file of any layout. The model writes the mapping spec; code executes it on every row. Per workbook: one call classifies the sheets into regions, one call per region writes its spec, a dry run checks every row (transform failures, duplicate keys, references between regions) with up to two repair calls, and semi-structured text columns (like Supplier `Risk Notes`) get a model-written parser, with only the cells it cannot parse sent back to the model. Each column's confidence is the model's own estimate capped by what the dry run shows.

```bash
uv run scgraph ingest data/<workbook>.xlsx
uv run scgraph ingest compare data/<workbook>.xlsx \
    --reference <your-spec>.json                     # agreement with a hand-written spec
```

Generated specs are written to `mappings/generated/<file>__<region>.json` with status `draft`, the model's rationale and the confidence of every column. A later file whose sheets have the same headers and column types (a re-upload, or a csv export of one sheet) reuses them with no model call; `--reuse-only` turns a missing match into an error instead of a call. `--no-load` stops before loading, `--specs-dir` uses another spec directory.

Columns whose confidence is below the review threshold do not reach the graph directly: each becomes one proposal in the review queue (see [Review](#review)). Accepting it approves that column in the stored spec, rejecting it sets the column to `ignore`. You can also review and approve a whole spec; approval sets every column's confidence to 1.0 for later ingests:

```bash
uv run scgraph ingest spec show mappings/generated/<file>__<region>.json
uv run scgraph ingest spec approve mappings/generated/<file>__<region>.json
```

A spec is plain JSON: edit it to correct a decision, or add `known_anomalies` (see Graph) before approving.

Messy exports are handled without extra model calls: csv and tsv files are read as UTF-8 (with or without a byte-order mark), UTF-16 with a byte-order mark, or Windows-1252, and the report says which (`read as cp1252`). Summary rows at the end of a table (`TOTAL`, `Subtotal`, `Sum`, `Gesamt`, `Summe`, `Totale`, `Итого` and similar in a key column) end the region above them; the spec records them as `excluded_rows` and the report lists them (`excluded row 458 'TOTAL'`). In the dry run a reference counts as resolved when a region of the same file has the id, the graph already holds it (accepted or waiting in a proposal), or another file of the same batch contains it, so a BoM loaded after its components spends no repair call on them and keeps its confidence.

### Batches

Manufacturers send several files at once, often zipped. Give `scgraph ingest` several files, zip archives, or both, and it ingests them as one batch:

```bash
uv run scgraph ingest exports/*.csv contracts.zip suppliers.xlsx   # tables first, then documents
uv run scgraph ingest batch.zip --reuse-only --no-load
```

- Archives are expanded into a temporary directory and deleted afterwards. Folders inside an archive are kept in the names (`batch.zip/exports/bom.csv` in reports and source locations); an entry that climbs out (`../../x.csv`) or is absolute keeps only its base name. `__MACOSX/` entries and hidden files are ignored; archives inside archives and unsupported types (images, `.xls`) are listed as skipped with the reason.
- A batch beyond 200 files or 200 MB uncompressed is refused before anything is ingested (declared sizes are checked first, actual bytes while extracting).
- Table files (`.xlsx`, `.xlsm`, `.csv`, `.tsv`) are ingested first in the order given, then documents (`.pdf`, `.txt`, `.md`), so a contract resolves mentions against the suppliers the batch's tables just described.
- Each file gets its own report, as a single-file ingest prints it, and its own journal entry. A file that fails is reported with its error and the others continue. The batch ends with the totals: files done, failed and skipped, facts applied and proposed, new proposals, summary rows excluded, LLM calls and cost.

In the API and the web app a batch runs as a background job (see [API](#api)); the graph stays usable while the model works, because the job takes the graph lock only to read known ids and to load. Jobs are kept in `.cache/jobs.sqlite` (`SCGRAPH_JOBS_DB`); a job still running when the server stops is marked failed at the next start and has to be submitted again.

## Review

Every load into the main graph (`graph load`, `ingest`, the API) passes a review gate. It is plain code, no model calls: a fact applies directly only when it is new or agrees with the graph, its confidence is at or above the threshold (`SCGRAPH_REVIEW_THRESHOLD`, default 0.9), and it does not touch a human-confirmed value, a deleted item or an unresolved mention. Everything else waits in a proposal and stays invisible in the graph, its statistics and exposure until a person accepts it.

Proposals are grouped by the decision behind them, not by cell:

| Kind | One proposal per |
|---|---|
| `mapping` | spec column below the threshold (with the values parsed from it), or a region's row keys |
| `statement` | document passage: the facts sharing one quote |
| `resolution` | mention that matched no node, or matched below the threshold |
| `conflict` | column (or field) whose values differ from the accepted ones; the accepted values stay active meanwhile |
| `restore` | deleted item that new evidence mentions again |

On our development workbook the generated specs gave nine `mapping` proposals (one per column below 0.9), not 1,594 cells. Accepting applies the whole proposal and marks its values human-confirmed; rejecting stores its facts as rejected, and the same value from the same source is not proposed again. Each proposal shows how accepting it would change Business Exposure and Business Impact of the suppliers, sites and components it reaches; `--sort impact` puts the largest change first.

```bash
uv run scgraph ingest data/<workbook>.xlsx --reuse-only
uv run scgraph proposals list --sort impact          # id, kind, facts, confidence, impact, summary
uv run scgraph proposals show 8                      # facts, spec rationale, current values, impact preview
uv run scgraph proposals accept 8 --comment "weights checked"
uv run scgraph proposals reject 3 --comment "photo links are placeholders"
uv run scgraph review threshold
```

A `resolution` proposal is accepted by naming the node the mention means, or by creating one: `proposals accept ID --target-id SITE-DE-03` or `--new-node [ID]`; later mentions with the same text resolve to it. `graph load` with a hand-written spec (all confidences 1.0) applies everything without proposals.

## Editing

A person can change the graph directly. Edits apply immediately with method `human`, confidence 1.0 and the author, are human-confirmed (`locked`), and are never overwritten by a later load: a disagreeing fact becomes a `conflict` proposal. The value an edit replaces stays as a `superseded` candidate.

One JSON shape serves the command line and the API:

```json
{"op": "create_node", "type": "Supplier", "id": "SUP-9001", "fields": {"name": "Acme GmbH", "country": "DE"}}
{"op": "set_field", "type": "Site", "key": ["SITE-DE-03"], "field": "recovery_time_months", "value": 9}
{"op": "add_edge", "type": "SUPPLIES", "key": ["SUP-9001", "CMP-9948", null], "fields": {}}
{"op": "delete", "type": "Supplier", "key": ["SUP-6350"], "field": null, "reason": "duplicate"}
{"op": "merge", "type": "Supplier", "from": "mention:Bosch GmbH", "into": "SUP-0001"}
```

Every edit may carry `evidence`: `{"id", "page", "quote", "url", "comment"}`, where `id` is an uploaded file (stored by content hash in `SCGRAPH_EVIDENCE_DIR`, 25 MB per file). The evidence shows in the provenance of what the edit produced. A batch applies all or none.

Deleting leaves a marker: the item stays hidden, the sources that supported it do not bring it back on re-ingest, and new evidence about it becomes a `restore` proposal. Deleting a node hides its edges. Merging A into B moves A's edges and values to B (B's confirmed values stay; differing values from A become conflict proposals) and makes later facts naming A attach to B.

```bash
uv run scgraph evidence add letter.pdf                     # prints the evidence id
uv run scgraph edit create_node Supplier SUP-9001 --field name='"Acme GmbH"' --field country=DE
uv run scgraph edit add_edge SUPPLIES SUP-9001,CMP-9948, --evidence <ID> --page 2 --quote "we supply CMP-9948"
uv run scgraph edit set_field Site SITE-DE-03 recovery_time_months 9 --comment "confirmed by buyer on call"
uv run scgraph edit delete Supplier SUP-6350 --reason duplicate
uv run scgraph edit merge Supplier SUP-9001 SUP-0001
uv run scgraph edit --file edits.json                      # a JSON list of edits
uv run scgraph journal                                     # every change, newest first
uv run scgraph journal --undo 42                           # undo an accept, reject or edit
```

Keys are comma-separated with empty parts for null (`SUP-9001,CMP-9948,` is a SUPPLIES edge without a site); values are JSON, anything else is text. The author is `--author`, else `SCGRAPH_AUTHOR`, else the OS user. Every applied change (ingest run, accept, reject, edit, undo) is a journal entry; an entry can be undone while no later entry changed the same rows, and an undone accept or reject returns its proposal to pending.

## API

`scgraph serve` serves a local HTTP API (FastAPI) over the same operations, for the UI and other clients. It is single-process and local: no authentication; the author of a change is the `X-Author` header (default as above). The UI's development origins (`http://localhost:5173`, `http://localhost:3000`) are allowed; set `SCGRAPH_CORS_ORIGINS` (comma-separated) for others.

```bash
uv run scgraph serve --port 8000        # docs at http://127.0.0.1:8000/docs
```

| Endpoint | |
|---|---|
| `GET /api/stats`, `GET /api/schema` | counts (with pending proposals by kind), the schema |
| `GET /api/graph?around=Type:id&depth=1..3&pending=true&limit=300&relations=` | subgraph (whole graph without `around`); `pending` adds what proposals would add, marked; at most `limit` nodes (max 1000), closest first, with `truncated` and `omitted`; `relations` follows only those edge types. A hop also follows an edge's identity (an assembly reaches its BOM; a virtual `ROOT` edge links it to its root component) |
| `GET /api/nodes/{type}/{id}` | every field with all candidates and provenance, and the edges; a merged or deleted id says so |
| `GET /api/search?q=&type=` | nodes by id or name, best first |
| `GET /api/exposure/{type}/{id}`, `GET /api/exposure/ranking?type=&by=exposure\|impact` | exposure with BOM paths, rankings |
| `GET /api/proposals?status=&kind=&sort=impact\|time`, `GET /api/proposals/{id}` | the queue, one proposal with its preview |
| `POST /api/proposals/{id}/accept` `{comment?, target_id?, new_node?}`, `POST /api/proposals/{id}/reject` `{comment?}` | decide; 409 when already decided |
| `POST /api/edits` `{edits, evidence_id?, comment?}` | apply a batch; 422 lists each failing edit |
| `POST /api/evidence` (multipart), `GET /api/evidence/{id}` | upload, download |
| `GET /api/journal?before=&limit=`, `POST /api/journal/{id}/undo` | journal, undo; 409 names the later entry that blocks it; an edit applied from an agent thread has `origin` `thread:<id>` |
| `POST /api/ingest` (multipart `file`, optional `reuse_only`) | ingest one file through the gate; returns the report |
| `POST /api/ingest/batch` (multipart `files`, one or more, zip archives too; optional `reuse_only`) | start a batch job and return it at once; 422 when the batch is beyond the limits (200 files, 200 MB) or holds nothing to ingest, and no job is created |
| `GET /api/ingest/jobs?limit=20`, `GET /api/ingest/jobs/{id}` | recent batch jobs, newest first; one job with its status (`queued`, `running`, `done`, `failed`), each file's status (`queued`, `running`, `done`, `failed`, `skipped`) and, once ingested, its report, the current file and the totals |
| `GET /api/overview` | key figures, pending proposals, top suppliers and sites by impact and exposure, latest journal; while no revenue is accepted, exposure is unavailable and `revenue_proposals` names the proposals it waits for |
| `GET /api/network` | the facility network, cached per graph version: every site and supplier (`latitude`/`longitude` empty when unknown) with `tier`, `exposure_pct`, `impact_eur`, `risk_flags` and `single_source_parts`; `supply_links` (supplier to site) with their `parts` (`id`, `description`, `annual_spend`, `single_source`, `exposure_pct`); `tier_links` (supplier to parent supplier); `exposure_available` |
| `GET /api/specs`, `GET /api/specs/{name}`, `POST /api/specs/{name}/approve` | stored mapping specs: list, show, approve with the author |
| `/api/ops/...`, `/api/agent/...` | disruptions, plans, decisions, tasks and reports; the agent's threads, edit blocks and events (see [operations.md](operations.md)) |

A proposal's preview has `depends_on`: when it targets an exposure input but shows no effect because no revenue is accepted yet, the pending revenue proposals it waits for. The impact sort puts it right after them.

## Web app

Tierscope (`ui/`, React + Vite + TypeScript) is the web interface, in light and dark themes. Its screens are Overview, Agent and Reports (see [operations](operations.md)) and Data, whose tabs are Ingest, Review, Specs and History (the journal). The earlier addresses `/review`, `/ingest`, `/specs` and `/journal` open the matching Data tab. The earlier `/disruptions` and `/actions` addresses lead to Agent, and a disruption's address to its thread in Agent or, once resolved, to Reports; the cross-disruption task board is gone (tasks are kept in their disruption and listed on the Overview).

The Overview is the home page and holds the network:

- **Key figures** in a strip on top: total assembly revenue, suppliers, sites, components, single-source components, pending proposals.
- **The network** in the centre, from `GET /api/network`. It shows sites and suppliers only: supply links from a supplier to a site, dashed tier links from a supplier to its parent supplier, marker size by the chosen metric (Business Impact or exposure), colour by exposure from slate blue to rose, type by shape and outline (sites are green-outlined rounded squares, suppliers violet-outlined circles), and a rose halo on suppliers with risk flags. Parts, assemblies and risk indicators are never nodes; they open in the side panel. It has two modes: **Map** places facilities by coordinates and says how many have none, **Graph** draws them without coordinates in columns by tier (highest tier on the left, sites on the right) or as rings.
- **The map** fills its area and opens on the box of the placed facilities (land between 60°S and 85°N when none has coordinates); reset returns there. It zooms from 0.6 to 48 times. Markers keep their on-screen size while zooming, larger markers are drawn under smaller ones, finer country outlines (`world-atlas` 50m) load once the zoom reaches 4, and from zoom 6 facilities at the same coordinates fan out on a small ring with a hairline to the true point (the tooltip says how many share it). The zoom buttons zoom about the selected facility.
- **Selecting a facility shows its chain**: for a supplier, every supplier upstream of it through tier links, the suppliers it feeds and the sites they supply; for a site, its suppliers and everything upstream of them. The chain stays visible whatever the filters say, and other facilities fade. A part or assembly in the side panel highlights the facilities it comes from and goes to.
- **Filters** in a panel that opens over the network: facility type, supplier tier (tier 1 by default; a supplier without a tier counts as tier 1), country, risk flags, single-source parts, the metric, and top N. When closed, the Filters button shows how many differ from the default.
- **The side panel** shows the company summary when nothing is selected: the visible slice when filters are active (counts, the largest Business Impact, single-source parts, top five), active disruptions and open tasks, top risks, and the review count. Selecting a facility or a supply link shows its details instead, under a breadcrumb (Summary / facility / part / assembly). A node shows its fields as a table (one row per field with value, confidence and markers for locked, flagged and other candidates; the arrow opens method, status and every candidate with its source), then the sections that apply to its type, leaving out any without rows. A site or supplier has key figures (Business Impact, exposure, tier, country), its supply chain by tier, its parts (part, description, site or supplier, spend, single source, exposure), its risk indicators (indicator, category, value, source, date), exposure and sources. A part has its suppliers and the sites it is delivered to, and the assemblies that use it with their BOM paths. An assembly has its BOM as a tree that loads each level when opened. A risk indicator has the supplier it belongs to. Every node ends with **All relations**, collapsed, where edges are added and deleted; the menu sets fields, merges, deletes and creates nodes. Opening a part, assembly, risk or link adds a crumb, and each crumb returns to that level. Hovering a supply link shows its parts, spend and single-source count; selecting it lists every part, each opening in the side panel. Escape, the Summary crumb or a click on empty space returns to the summary.
- **The address** keeps the view: mode (`view`), selection (`sel=Type:id`, `link=supplier>site`), the panel's drill-in (`trail=Component:CMP-9552,Assembly:ASM-3552717`, an id holding a comma percent-encoded), layout and every filter, defaults left out. Opening a crumb adds a history entry, so Back steps out of a drill-in; changing filters does not. The Overview link returns to the last view, and a copied address opens the same view. Search, top risks, review previews, disruptions and reports open nodes there: sites and suppliers selected, other types on the trail. Earlier `/explorer/Type/id` addresses and Parts lens addresses (`lens=parts&focus=…`) open the same way.

The app loads its data without internet access: the API is local, so requests do not wait on the browser's online status, and fonts and map shapes are bundled. The Ingest screen takes several files and zip archives at once (drop them or choose them), runs them as one batch job and shows each file's status while it runs, then each file's report (reused or generated specs, repair rounds, facts applied, new proposals with links, excluded summary rows, LLM calls and cost) and the batch totals. Recent batches stay listed after a reload. Its build is committed in `src/scgraph/api/static/` and served by `scgraph serve` on the same port as the API, so running it needs only `uv`:

```bash
uv run scgraph serve                    # open http://127.0.0.1:8000
```

The app asks for your name on the first change (accept, reject, edit, upload, undo) and keeps it in the browser; it is sent as `X-Author`.

Changing the UI needs Node 20+ and npm:

```bash
cd ui && npm install
npm run dev          # http://localhost:5173, proxies /api to the server on :8000 (run `uv run scgraph serve` too)
npm test             # Vitest, API calls mocked
npm run build        # typecheck, build into src/scgraph/api/static/, check the size budget, write build-info.json
npm run gen:api      # after changing API models: export openapi.json and regenerate src/api/schema.d.ts
```

Commit `src/scgraph/api/static/` with UI changes: `tests/test_ui_build.py` fails when `build-info.json` does not match the hash of the UI sources (`scripts/ui_build_info.py`), and `tests/test_api_ui.py` fails when `ui/src/api/openapi.json` is older than the API.

Performance (01.11, `npm run build`, Vitest and Chromium on our development graph): the initial route is 150.5 KB gzip (budget 600 KB, checked by `scripts/check-bundle.mjs`), with Agent (11.9 KB) and Reports as lazy chunks; the graph code (Cytoscape) is a lazy 150.8 KB chunk and the map (d3-geo with bundled `world-atlas` 110m shapes) a lazy 66.1 KB chunk, and neither loads until its mode is shown; the 50m outlines are a further lazy 227.5 KB chunk loaded on zoom. The facility graph with every tier (296 facilities) lays out in about 0.2 s in the browser (longest main-thread task 215 ms) and 0.14 s headless. Layouts run once without animation, and the review queue, journal and fact lists are virtualised.

## Documents

`scgraph ingest` also takes PDF (`.pdf`) and plain text (`.txt`, `.md`). Tables found on PDF pages go through table ingest, so a PDF export of an already-mapped sheet reuses its stored spec with no mapping call. Text is classified once (which schema domains it is about) and then extracted in chunks of about 1,000 tokens that never cut a line. Every extracted fact carries a verbatim quote and its location (page and character span for PDF, line range for text); a fact whose quote is not in the text is rejected, and one whose value is not in its quote is capped at confidence 0.5 and flagged in the report.

Classification also says whether the document reports a supply disruption (a supplier's notice of a fire, an export restriction, a closed port, a tariff, a shortage) rather than describing the network. Such a document adds no facts to the graph: it becomes a draft disruption, the same way as pasted news, and the agent plans it at once. Its report (single file or in a batch, under the file) names the draft with its warnings, and the draft keeps the document's name and text as its source. On the command line the draft is saved and its planning waits for the server.

Names in text are matched to graph nodes in this order: exact id, then a unique name after dropping case, punctuation and legal forms (Inc., GmbH, Co., Ltd., ...), then the model chooses among up to five similar candidates. A fact attached through a match gets the lower of its extraction and match confidence, and its provenance records how the mention was resolved. A mention that matches nothing becomes a node `mention:<text>`, counted as an unresolved mention in `graph stats`.

## Tests

`uv run pytest` runs every test with no network and no keys: model calls go to a scripted fake transport. Tests marked `live` call a real provider and run only with `SCGRAPH_LIVE_TESTS=1` and keys set.

## LLM usage

All LLM calls go through `scgraph.llm.LLMClient`. Callers never pick a provider. The client reads it from configuration, so the same code runs on Muse Spark or Muse Glimmer.

```python
from scgraph.config import Config
from scgraph.llm import LLMClient, Request, run_tool_loop

client = LLMClient(Config.from_env())
reply = client.create(Request(system="...", messages=[{"role": "user", "content": "..."}]))
result = run_tool_loop(client, request_with_tools, {"lookup_part": handler})
```

Requests are limited to what both Muse providers support: `system` (a string), `messages`, client-side `tools` (`name`, `description`, `input_schema`), `text` / `tool_use` / `tool_result` blocks, `max_tokens` and `temperature`. Anything else is rejected before the call.

### Environment

Copy `.env.example` to `.env`. Real environment variables take precedence over `.env`.

| Variable | Default | Meaning |
|---|---|---|
| `SCGRAPH_LLM_PROVIDER` | `meta` | `meta` (Muse Spark) or `glimmer` (self-hosted Muse Glimmer) |
| `MODEL_API_KEY` | | Meta Model API key |
| `MUSE_API_KEY` / `MUSE_BASE_URL` | / `http://localhost:8000/v1` | Key and address of the self-hosted Muse Glimmer server |
| `SCGRAPH_META_MODEL` / `SCGRAPH_GLIMMER_MODEL` | `muse-spark-1.1` / `muse-glimmer` | Model per provider |
| `SCGRAPH_LLM_CACHE` | `record` | Cache mode, see below |
| `SCGRAPH_BUDGET_META_USD` / `SCGRAPH_BUDGET_GLIMMER_USD` | `5` / `5` | Cumulative spend cap per provider |
| `SCGRAPH_LLM_PRICES` | | Extra or overriding prices, JSON `{"model": [input_usd_per_mtok, output_usd_per_mtok]}` |
| `SCGRAPH_RUN_ID` | random per invocation | Run id written to the ledger |
| `SCGRAPH_LLM_DB` | `.cache/llm.sqlite` | Cache and ledger database |
| `SCGRAPH_GRAPH_DB` | `.cache/graph.sqlite` | Graph database |
| `SCGRAPH_REVIEW_THRESHOLD` | `0.9` | Confidence at or above which new facts apply without review |
| `SCGRAPH_AUTHOR` | OS user | Author of human changes when none is given |
| `SCGRAPH_EVIDENCE_DIR` | `.cache/evidence` | Uploaded evidence files |
| `SCGRAPH_CORS_ORIGINS` | `http://localhost:5173,http://localhost:3000` | Origins the API allows |
| `SCGRAPH_OPS_DB` / `SCGRAPH_JOBS_DB` | `.cache/ops.sqlite` / `.cache/jobs.sqlite` | Operations and agent database; background jobs |
| `SCGRAPH_AGENT_TRIGGERS` | `on` | Disruption events start the planner; `off` only records them |
| `SCGRAPH_BUDGET_AUTO_USD` | `2` | Cumulative spend cap for event-started agent turns (ledger runs `auto-*`) |
| `SCGRAPH_AGENT_SPEND_LIMIT_EUR` | `250000` | Action costs per disruption shown on a plan as the spending limit; in the app every plan waits for a person |

### Providers

- `meta` (default): `api.meta.ai` (Messages API-compatible), model `muse-spark-1.1`, bearer auth with `MODEL_API_KEY`.
- `glimmer`: Meta's open-weights Muse Glimmer (30B), served by vLLM on your own GPU machine at `MUSE_BASE_URL` (vLLM also speaks the Messages API); model `muse-glimmer`, bearer auth with `MUSE_API_KEY`, the model card's sampling (temperature 1.0, top_p 0.95, top_k 64), no per-token price. If the server is reachable only through an SSH tunnel, open a SOCKS proxy and pass it on:

  ```bash
  ssh -D 127.0.0.1:1080 -N <gpu-host>   # keep it open
  ALL_PROXY=socks5://127.0.0.1:1080 HTTP_PROXY=socks5://127.0.0.1:1080 SCGRAPH_LLM_PROVIDER=glimmer uv run scgraph llm smoke --provider glimmer
  ```

  It reasons before every answer (about 25 tokens a second), so a planner turn takes minutes; the server runs 32 requests at once, so benchmarks run episodes in parallel.

### Cache modes

Responses are cached in `.cache/llm.sqlite`, keyed by provider, model, system, messages, tools and generation parameters, and by the run seed when the caller sets one (`LLMClient(run_seed=...)`), so repeated benchmark runs are not served one cached answer. The seed is never sent to a provider; without one the key is as before, so earlier caches stay valid.

- `record` (default): return the cached response if there is one, otherwise call the provider and store the answer.
- `replay`: cached responses only. A miss is an error and nothing goes over the network. No API key is needed. Use it for offline demos.
- `off`: always call the provider and store nothing.

To clear the cache, delete `.cache/llm.sqlite`. This also deletes the ledger.

### Ledger and budgets

Every request, live or cached, writes a ledger entry: provider, model, tokens, cost in USD and cache hit, tagged with the run id. A live call is refused once recorded spend for its provider reaches that provider's cap. Cache hits are always allowed. One call can overshoot the cap by its own cost.

### Commands

```bash
uv run scgraph llm smoke --provider meta     # one tool-call round trip, cache off; exit 1 on failure (a few cents)
uv run scgraph llm smoke --provider glimmer  # the same against your Muse Glimmer server
uv run scgraph llm spend                     # totals per provider and model, with budgets
uv run scgraph llm spend --run RUN_ID        # totals for one run
```
