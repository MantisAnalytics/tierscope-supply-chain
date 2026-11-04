# Operations: disruptions, the response agent, decisions and reports

Tierscope's data layer (ingest, review, the graph and its exposure) tells a manufacturer which suppliers could stop
its factories. Operations is what happens when one does: a disruption is described on the graph, the weekly model
shows what it costs if nobody acts, a model acting as supply manager proposes a response, a person decides on every
action, accepted actions are followed up as tasks, and everything ends in a report.

```
Data (ingest, review) -> Overview (graph, exposure) -> Agent (impact, plans, decisions, tasks) -> Reports (resolved)
```

## In the app

- **Agent** (`/agent`): one place to work with the agent, from a first question or an alert to a resolved
  disruption. The list on the left has **Disruptions** (drafts and active ones; those waiting for a person first,
  marked with the reason: plan ready, agent failed, budget or task overdue) and **Conversations** (newest first, older
  than a week folded). **New** opens an empty thread with suggested questions, a box to paste a news or alert text
  and the scenario library with each scenario's cost if nothing is done. A pasted text is drafted by the model (names
  matched to the graph), saved at once as a **draft** with its warnings and planned by the agent. A document dropped
  in Data that the model recognises as a disruption report becomes a draft the same way. Drafts are planned like
  active disruptions but do not count among active disruptions until confirmed. Every new or edited disruption is
  planned automatically (see Triggers).

  The thread shows the conversation, each agent answer with one card (plan, edit, draft, decision, event) and its
  steps folded. The column on the right shows the disruption's incident state (cost if nothing is done, weekly lost
  revenue, current plan, tasks) and whatever you open from the thread: a plan version with its actions to accept or
  reject (with a comment) grouped by component and the plans compared (do nothing, the standard playbook, the
  optimiser reference, the agent), an edit block to apply or dismiss, a draft to edit, confirm or discard, a task, a
  node. Quick actions above the message box follow the disruption's state: Plan a response, Ask for a new version,
  Confirm or Discard a draft, **Mark resolved**.
- **Tasks**: an accepted action becomes a task (open, in progress, done, cancelled) with owner, due week and notes,
  edited in its disruption's column in Agent. A task's due week counts from the week its disruption was confirmed (or
  created, when it never was a draft); once it has passed while the task is open or in progress, the task is
  **overdue** and its disruption is marked "task overdue" in the list. The Overview's side panel lists the open tasks
  of all disruptions with owner, due week and the overdue mark, each linking to its thread.
- **Resolving**: Mark resolved asks for a closing note and records the outcome at that moment (accepted actions and
  their simulated cost against doing nothing, the references, the tasks, the plan version, the model cost of the
  disruption's runs) together with its report, executive summary included. The disruption becomes `resolved`, its
  thread is archived and read-only, and it can no longer be edited, planned or messaged. Open tasks stay open. A
  discarded draft is `archived`, not resolved, and does not appear in Reports.
- **Reports** (`/reports`): resolved disruptions, newest first, with the cost if nothing was done, the cost of the
  accepted plan, the amount saved and the model cost; one opens as its read-only thread next to its report (executive
  summary, resolution, impact, decisions, outcome against the references, follow-up, the agent's run, assumptions).
  The report of a resolved disruption is the one written at resolution, so later graph changes do not alter it. The
  portfolio risk report and the agent scorecard are there too. Each downloads as Markdown and prints cleanly.

The earlier addresses `/disruptions`, `/disruptions/{id}`, `/actions` and `/reports/disruption/{id}` lead to the
matching place in Agent or Reports.

## Command line

```bash
uv run scgraph ops library               # ready disruptions for the current graph, with their do-nothing cost
uv run scgraph ops library --save 1 2    # save library entries 1 and 2
uv run scgraph ops list
uv run scgraph ops assess 1              # affected parts, do nothing, playbook, optimiser
uv run scgraph ops plan 1                # run the planner now, as a turn of the disruption's thread (calls the model)
uv run scgraph ops report 1 > report.md  # also: portfolio, scorecard, benchmark

uv run scgraph ops suite                 # generate benchmarks/ops_suite.json from your graph (seed 7)
uv run scgraph ops bench --split test --runs 3            # the planner on the held-out cases (calls the model)
uv run scgraph ops bench-compare         # every result set in benchmarks/results/ side by side
uv run scgraph ops bench-rescore benchmarks/results/<name> # score stored runs against current references

# episode benchmark: a year of ShockBench events per episode (see "Episode benchmark")
(cd benchmark && uv run python scripts/export_episodes.py ../benchmarks/shockbench_episodes.json.gz)  # Python 3.13
uv run scgraph ops episodes-suite --workers 16            # map, cut harm levels, references (CPU only, ~1.5 h)
uv run scgraph ops episodes-bench --split dev --limit 3   # a pilot on three dev episodes (calls the model)
uv run scgraph ops episodes-suite --refresh-replanner     # recompute the re-planner after a change to reviews or views
uv run scgraph ops episodes-suite --add-tune 10           # add a tune split of 10 more episodes per harm level
uv run scgraph ops episodes-bench --split test --workers 8 # the 100 held-out episodes (dev, tune, test or all)

uv run scgraph agent ask "which assemblies depend on supplier <supplier-id>?"   # a new general thread
uv run scgraph agent ask "plan again without suppliers in China" --thread 1
uv run scgraph agent thread              # list threads; `agent thread N` prints one
uv run scgraph agent events              # recorded events, newest first
```

Saving or editing a disruption on the command line records its event; the planner turn waits queued until the server
(`scgraph serve`) runs, which picks up queued threads on start.

Operations keep their own database, `.cache/ops.sqlite` (`SCGRAPH_OPS_DB`); background jobs (agent runs, batch
ingests) are recorded in `.cache/jobs.sqlite` (`SCGRAPH_JOBS_DB`). The model is the one configured for the app:
by default Muse Spark through the Meta Model API (`MODEL_API_KEY`; `SCGRAPH_META_MODEL` picks another Meta model),
or, with `SCGRAPH_LLM_PROVIDER=glimmer`, the self-hosted Muse Glimmer (`MUSE_API_KEY`, `MUSE_BASE_URL`). Budget caps
and the response cache apply.

## The weekly model

Units are weeks of demand: every bought-in component is needed at 1.0 per week, so no unit data is needed. Over the
horizon (26 weeks by default), week by week:

1. Supply arrives from the cheapest available source: the usual supplier at its capacity during the disruption, then
   the sources the plan opens (second source, new supplier, spot buy). A supplier back at full capacity delivers up to
   1.25 a week until stock is rebuilt. A route closure delays arrivals instead of cutting capacity.
2. Plants start with the assumed stock cover (3 weeks by default, set per disruption).
3. Scarce units of a component are shared equally among the assemblies that contain it, or in the plan's priority
   order. An assembly makes the share allowed by its scarcest component.
4. Lost revenue = weekly assembly revenue x lost output. Total cost = lost margin (the graph's `margin` parameter,
   19% for the reference data) + action costs + tariffs and freight surcharges.

Assumed rules and costs of the actions, shown in the app and in every report:

| Action | Allowed when | Effect | Cost |
|---|---|---|---|
| Activate approved second source | the part is not single-source | from +2 weeks, up to 70% of demand | EUR 10,000 + 5% premium |
| Qualify new supplier | same sub-commodity, not hit by the disruption | from +12 weeks (High criticality) or +6, up to 100% | EUR 80,000 (High) or 30,000 + 10% premium |
| Spot buy | any affected part | from +1 week, up to 50% of demand | 150% premium |
| Air freight | part on a delayed route | bridges the delay from +1 week | 60% of the value shipped |
| Buy buffer stock | before the disruption starts | up to 8 extra weeks, 0.5 a week | 3% of the value bought |
| Allocation priority | always | order in which assemblies get scarce parts | none |

Component value per week is annual spend / 52; where spend is missing, the median of the commodity stands in.

**References.** For every disruption the same model scores three plans: doing nothing; the standard playbook (second
source where allowed, otherwise spot buy half the demand, air freight on delayed lanes); and the optimiser reference.
The optimiser runs greedy passes over the action catalogue, then a coordinate search from two starts (the greedy result
and a plan with every allowed action on every part): part by part it tries the allowed combinations of actions with
the rest of the plan fixed, then swaps (one action alone in place of several) and later start weeks (using the stock on
hand first), accepting only changes that lower the cost. It is a strong heuristic, not a proof of optimality; most
disruptions take one to five seconds, the largest about twenty. A plan's **skill** is its cost reduction against doing
nothing divided by the optimiser's: 1 matches the optimiser, 0 is doing nothing, above 1 beats it. Stored references
carry a version and are recomputed when the method changes.

## The agent

One engine (`src/scgraph/agent/`) that people talk to and that events start:

- **Tools**, shared by every profile: graph questions (`find_entities`, `node_info` with provenance,
  `impacted_assemblies`, `single_source`, `exposure_by`, `path_between`), the disruption tools
  (`disruption_overview`, `component_details`, `alternative_suppliers`, `simulate_plan`) and the writes
  (`submit_plan`, `propose_edit`, `draft_disruption`).
- **Profiles**: `planner` (ops and graph tools and `submit_plan`; ends on an accepted submission, after two nudges it
  fails) runs on events and plan requests; `chat` (graph tools, `draft_disruption`, `propose_edit`, and in a
  disruption's thread also the planning tools) answers people and ends when it replies in text. Prompts are
  `agent/prompts/planner.md` and `chat.md`; a run's prompt version is the start of the template's SHA-256.
- **Context, not prompts**: each turn's system prompt is the template plus a generated context section: world,
  current week, approval mode per write kind, spending limit, the current plan version with each action's decision
  and comment, and every rejected action. In a disruption's thread the first message is the disruption overview.
- **Recorded loop** (`agent/loop.py`): every model message, tool call and error is stored as a step the moment it
  happens and shown live; tool results are clipped at 6,000 characters; at most 18 model turns and 8,000 output
  tokens per reply; in the last 3 turns the tool results say how many are left. A reply cut off at that limit runs none of its tool calls (the last one is incomplete): the model
  is told and sends them again.
- **Planner prompt** (agent v4): the rules and costs above, how the money works (an assembly makes only as much as its
  scarcest part; a second source or new supplier replaces tariffed units; spot buys fill only what cheaper sources
  leave) and a working method: simulate a plan that covers every affected part with what it allows, remove or shrink
  one action at a time and keep each change that lowers the cost, and submit the cheapest plan simulated. The overview
  lists the actions each part allows and the tariffs per part; simulation results give the money per part.
- **Tools built for a smaller open model** (from the traces of Muse Glimmer's benchmark runs):
  - `simulate_plan` names the assemblies each short component blocks (`blocked_assemblies`), and with
    `fill_gaps: true` adds, for every component the plan leaves short, the actions the rules allow (air freight on a
    delayed route, else a second source and a spot buy at the maximum share), returned as `added_actions`. Glimmer
    used to add one action at a time and stop when no single fix showed a gain, since an assembly waits for all its
    short parts.
  - Every simulation has a `simulation_id`; `submit_plan` takes it to submit that plan exactly as simulated, so a
    plan of 90 actions need not be copied by hand. A submitted plan that costs clearly more than the cheapest plan
    simulated in the turn (EUR 50,000 and 5%) is sent back once, naming the cheaper simulation; submitting it again
    is accepted.
  - `component_details` and `alternative_suppliers` take several components per call (`components`).
  - Inputs are read leniently: ids with stray whitespace, `actions` sent as a string (with text after the list, read
    as the summary when none is given), a tool name with a suffix (`component_details.component`). Errors show an
    example of the expected input.

A submitted plan whose actions break a rule comes back with the reasons. Doing nothing is a plan: an empty plan with
its reason in the summary is recorded as done. `src/scgraph/ops/agent.py` keeps `run_agent` for one disruption in
memory.

**World.** The agent reads and changes anything only through a `World` (`agent/world.py`): the current week, the
company snapshot and graph version, its view of a disruption, simulation on that view, graph reads, and submissions.
`LiveWorld` is the app (week 0, the view is the stored disruption). A world may withhold fields: a view with an
estimated duration range shows the range to the agent, never the true duration, and simulation runs on the view.

**Approval gate** (`agent/gate.py`). Every write passes the gate, whose mode per kind (`plan`, `edit`, `draft`,
`task`) is `human` (stored as a proposal for a person) or `auto` (applied at once with `agent:<model>` as author, and
the block says so). In the app every kind is `human`. The gate refuses to auto-apply a plan whose action costs exceed
`SCGRAPH_AGENT_SPEND_LIMIT_EUR` (default 250,000) per disruption and tells the agent both amounts; in `human` mode the
limit is shown on the plan.

**Plan versions.** Each submitted plan is the next version of the disruption's plan, with its actions and reasons,
summary, simulated outcome, skill and the graph version it was computed on. A new version keeps the accepted actions
(marked kept), proposes only actions not yet accepted, and supersedes the undecided proposals of earlier versions. A
plan made on an older graph version is **stale** when re-simulating it on the current graph moves its total cost by
more than 1% or refuses one of its actions; an unrelated edit does not make it stale.

**Threads** (`agent/threads.py`). Every disruption has exactly one thread, created with it; a general thread starts
with any question. Messages hold typed blocks: text, plan, simulation, edit, draft disruption, decision and event;
non-text blocks point at the stored object, so a plan block always shows the current decisions. A thread runs one
turn at a time: messages and events that arrive during a turn are queued and answered in order. Accepting or
rejecting an action, applying or dismissing an edit block and saving or discarding a draft post decision blocks the
next turn sees. A thread's `needs` says why a person is needed: `plan ready`, `agent failed` or `budget`; it clears
when a person posts or decides. The model sees the last 20 messages, non-text blocks as one-line summaries; tool calls
of earlier turns are not replayed.

**Graph edits from a thread.** `propose_edit` checks edits against the schema and the graph without applying them,
records the current values and previews the effect on exposure and Business Impact (deletes and merges have no
preview). Applying the block goes through graph editing with the person as author and `thread:<id>` as the journal
origin, and can be undone from the journal; if a value changed since the proposal, the block becomes stale and is
refused.

**A draft from a conversation.** `draft_disruption` in a general thread stores a draft without a thread of its own;
saving it binds the general thread to the disruption and starts the planner.

**Triggers** (`agent/triggers.py`). `disruption.created` (with its source: form, library, news, document, chat) and
`disruption.updated` (with the changed fields) are recorded and shown in the disruption's thread as event blocks, and
start the planner there in the background, also for drafts. `SCGRAPH_AGENT_TRIGGERS=off` records and shows events
without running the agent. Event-started turns bill to ledger runs named `auto-*`; once their spend reaches
`SCGRAPH_BUDGET_AUTO_USD` (default 2) no model call is made and the thread says planning was skipped for budget
(**Plan a response** starts it by hand). Provider caps apply as well.

**Run metadata.** Every turn records its thread, profile, prompt version, provider, model, world, approval modes, run
seed, triggering event, start and end time, tokens, cost, steps and the plan version it submitted.

| Setting | Default | Meaning |
|---|---|---|
| `SCGRAPH_AGENT_TRIGGERS` | `on` | Events start the planner; `off` only records and shows them |
| `SCGRAPH_BUDGET_AUTO_USD` | `2` | Cumulative model spend for event-started turns |
| `SCGRAPH_AGENT_SPEND_LIMIT_EUR` | `250000` | Action costs per disruption above which a plan is not auto-applied |
| `SCGRAPH_OPS_DB` / `SCGRAPH_JOBS_DB` | `.cache/ops.sqlite` / `.cache/jobs.sqlite` | Operations and agent tables; background jobs (4 workers) |

API: `GET /api/agent/threads?status=&disruption=&needs=`, `POST /api/agent/threads` `{text}`,
`GET /api/agent/threads/{id}` (messages with resolved blocks, turn steps, queue),
`POST /api/agent/threads/{id}/messages` `{text}`, `POST /api/agent/edits/{id}/apply` (409 when stale) and
`/dismiss` `{comment?}`, `GET /api/agent/events?thread=&limit=`. `POST /api/ops/disruptions/{id}/runs` asks for a plan
in the thread (same `{run_id, job_id}` answer); action decisions post to the thread;
`POST /api/ops/disruptions/{id}/confirm` and `/discard` decide on drafts. Thread lists carry `disruption_status`, and
`needs` includes `task overdue`.

Resolving: `POST /api/ops/disruptions/{id}/resolve` `{note}` (author from `X-Author`; 422 for a resolved or archived
disruption), `GET /api/ops/resolved` (resolved disruptions with `do_nothing_cost`, `plan_cost`, `saved`,
`model_cost_usd`, `accepted_actions`, `thread_id`). Edits, plan requests, messages and action decisions on a resolved
disruption answer 422 with the reason. `GET /api/ops/summary` lists the open tasks of all disruptions (`tasks`, each
with `overdue`, `elapsed_week`, `thread_id`), `overdue_tasks` and `agent_needs`, the number of disruptions waiting for
a person.

## Agent benchmark

**Suite.** `scgraph ops suite` draws 33 disruptions from the graph with a fixed seed into `benchmarks/ops_suite.json`:
the six library disruptions plus outages of one or several suppliers (50 to 100% loss), export restrictions starting now or
announced weeks ahead, route closures with surcharges, tariffs of 10 to 50%, and commodity shortages following
ShockBench episodes, with stock cover from 1 to 6 weeks. Cases where nothing worth EUR 25k can be saved are dropped (so
are all plant outages: no action in the catalogue delivers into a plant that is down). **Dev** (11 cases: the library
and one per type) is what the planner was developed on; **test** (22 cases) is held out and only reported. The suite
file keeps a fingerprint of the graph and refuses to run on another one.

**Runs.** `scgraph ops bench` runs the planner (`run_agent`: the `planner` profile over a one-disruption world, the
same code path as `scgraph ops plan`) three times per case with the response cache off, scores every plan in the
simulator against the three references, and writes `runs.jsonl.gz` (plans, steps, tokens, cost) and a summary to
`benchmarks/results/<name>/`. A run without a valid plan counts as doing nothing; a provider failure (timeout, dropped
connection) starts the run over; a budget or credentials error stops the benchmark. Means are over cases with a 95%
bootstrap interval over cases; a run below -1 counts as -1 in the means.

**Results on the 22 held-out cases: Muse Glimmer, agent v4, 3 runs per case** (`benchmarks/results/muse-glimmer-v4-test`;
`scgraph ops bench-compare`; also Reports, Agent benchmark):

| Disruption type | Cases | Agent skill [95% interval] | Playbook skill | Runs at least as good as the playbook | Runs near the optimiser |
|---|---|---|---|---|---|
| **All** | 22 | **0.98 [0.96, 1.00]** | 0.84 | 98% | 92% |
| Supplier outage | 5 | 1.00 | 0.99 | 100% | 100% |
| Export restriction | 5 | 1.00 | 1.00 | 100% | 100% |
| Route closure | 3 | 0.99 | 0.97 | 100% | 100% |
| Tariff | 5 | 0.98 | 0.46 | 100% | 93% |
| Supply shortage | 4 | 0.92 | 0.83 | 92% | 67% |

All 66 runs submitted a valid plan. Over the cases doing nothing costs EUR 823M in total, the playbook EUR 166M, the
agent EUR 104M (mean over its runs) and the optimiser EUR 49M. A run takes about 184,000 tokens and 16 tool calls;
tokens cost nothing on the self-hosted model.

The planner was developed on the dev cases and on the episode benchmark's dev and tune splits; these cases were not
used for it. The optimiser's swap and timing passes were added during development, after early runs found plans
cheaper than it; result sets are scored against the current references (`bench-rescore`). Earlier planner versions
and their result sets are not part of this repository.

The suite and the per-run files are built on the customer's graph; only the summary
(`benchmarks/results/muse-glimmer-v4-test/summary.md`) is published.

## Episode benchmark

The agent benchmark gives the planner one disruption, known in full at week 0. The episode benchmark gives it a
year: several events that overlap, announcements weeks ahead (some of them decoys that never happen), durations that
are not known while an event runs, and a plan to review as news arrives. The years come from **ShockBench-Flow**
(Mantis Analytics' benchmark, version 0.1.2, used unchanged): its generator draws calibrated, partly self-exciting
sequences of economic, operational and other events, with the messages that announce them and decoy threads. The
suite keeps the economic and operational ones (see "Which events").

**Episodes.** `benchmark/scripts/export_episodes.py` draws 2,000 episodes of ShockBench's `small` task (52 weeks, its
Development-phase world: 14 regions, 7 straits) from its public generator on our own root (20261102), so none of them
is a ShockBench dev or hidden episode. Per episode it keeps the events that start in the 52 weeks (or up to 4 weeks
before) and the decoys, with the weeks each message is published: `benchmarks/shockbench_episodes.json.gz`.

**Mapping onto our graph** (`ops/shock_episodes.py`). ShockBench's goods (fuel, wafers, chips) are not our development
dataset's commodities (only 11 of 160 parts are electromechanical, none from Taiwan, Korea or China), so an event hits our
suppliers and lanes by the places it names. These are our assumptions, not ShockBench's:

| ShockBench event | On our graph |
|---|---|
| Tariff by region R on imports from C (or from every other region) | a tariff at ShockBench's rate on components from C's suppliers delivered to plants in R |
| Sanction or export control on a route from A to B | an export restriction on A's suppliers for their deliveries to B's plants |
| Militarised or weather closure, piracy, of a strait | a delay on the lanes through it (Suez 2 weeks, Malacca 1, Taiwan Strait 1, Hormuz 2, Panama 1, Turkish Straits 2; none below 50% severity) and a risk surcharge (30% or 25% by kind, times severity) |
| Port strike in R | a delay on lanes out of and into R, its length times severity |
| Energy shock in R | energy-intensive suppliers in R (castings, forgings, steel, chemicals) lose that share of output |
| Material outage | a market shortage of chemicals (fuel sources, 30% deep at full severity) or electromechanics (materials, 50%) |
| Regional conflict in R (with C) | R's suppliers lose that share of output; trade between R and C stops |

Countries belong to ShockBench's regions by a fixed table (Europe including the UK, Switzerland and Norway; Hong Kong
with China; South-East Asia; the rest as ROW); a strait's lanes are fixed region pairs (Suez: East and South-East Asia,
India and the Gulf to Europe; Malacca: East Asia to Europe, India and the Gulf; and so on). Events that reach no
component are dropped. That dataset's lanes are mostly regional (Europe to Europe 54 component lanes, Europe to the US 27,
the US to the US 19, China to China 17), so tariffs, sanctions on Europe-US trade and energy shocks matter most and
strait closures rarely do.

**What the planner sees.** At the start of week t: real events from their first published message (a tariff
proposal, a threat of sanctions, a military threat) or from their start if unannounced; decoys from their message
until their stated start, then they lapse. A final tariff notice or a legal publication of sanctions is only
published for real events, as in ShockBench. Until an event ends its duration is the median of its kind over the
2,000 episodes, and once it has lasted that long the estimate doubles (start + median, then + 2 x median, ...) (tariffs 52 weeks, sanctions 128, militarised closures 35, regional conflicts 33, piracy 12, energy shocks 2, port strikes 2, weather closures 1); until
it starts its severity is the median too. The truth (real events, real durations and severities, no decoys) is only
used for scoring.

**Reviews.** The planner (the app's `planner` profile and prompt) gets a turn in each week where an event is
announced or starts, and in each week where an event in force outlasts its estimate, at most one every two weeks
(about 5 to 6 per episode), with the plan in force as context and a short message naming the news ("still in force
after 2 weeks, past its estimate"). An overrun check never displaces a review of news. Actions that have started stay; actions it adds start in the review week or later. The plan
in force at the end is scored on the truth over the 52 weeks; actions taken against a threat that did not come true
are paid for.

**References and score.** Per episode: doing nothing (0); the hindsight optimiser, the optimiser run on the truth
(1, ShockBench's clairvoyant plan); the playbook reacting in the week each part is hit (no foresight, ignores
announcements); and an informed re-planner, the optimiser run on each review week's view with started actions fixed
(ShockBench's `mpc_det` counterpart). The score is ShockBench's pooled Resilience Skill Score, (56):
RSS = sum_s p_s mean_s(J_nothing - J) / sum_s p_s mean_s(J_nothing - J_optimiser) over four harm levels with weights
0.50, 0.30, 0.15, 0.05, and a 90% interval from a bootstrap of episodes within levels. Harm is the do-nothing cost on
our graph, cut at its 50/80/95% quantiles over the candidates (EUR 0, 24.9M and 186M over 1,884 episodes that
touch us); per level 5 dev and 25 test episodes are taken in episode order, and a `tune` split of the next 10 per
level (`benchmarks/episode_suite.json`, suite version 3, written by `scgraph ops episodes-suite`). Changes to the agent are tried on dev and tune; test stays
held out for the reported score. The low
level holds calm years, where a planner can only lose money by reacting to decoys.

**Which events.** The suite keeps ShockBench's economic and operational events: tariffs, energy shortages, raw
material outages, port stoppages and weather closures (`shock_episodes.EVENT_TYPES`, since suite version 2). Trade
restrictions on routes, strait closures by military action, regional conflicts and piracy are left out: the hackathon
excludes anything political or involving war, and the Meta Model API's content filter refused prompts about them.
The prompts use plain trade and logistics words throughout.

**References** (CPU only, suite version 3; 1,884 candidate episodes, more than half of which cost us nothing, so the
lowest level is calm years where a planner can only lose money):

| Policy | Test (100) | Level 2 | Level 3 | Level 4 | Total cost, EUR M | Dev (20) | Tune (40) |
|---|---|---|---|---|---|---|---|
| Hindsight optimiser | 1 | 1 | 1 | 1 | 61 | 1 | 1 |
| Informed re-planner | 0.94 [0.92, 0.96] | 0.90 | 0.94 | 0.95 | 455 | 0.95 | 0.95 |
| Playbook, reacting when hit | 0.90 [0.86, 0.93] | 0.85 | 0.93 | 0.89 | 824 | 0.89 | 0.88 |
| Do nothing | 0 | 0 | 0 | 0 | 7,692 | 0 | 0 |

Before the overrun reviews (suite version 2) the re-planner scored 0.82 on test: a port stoppage of 32 weeks looked
like one of 2 until it ended, and nothing prompted a second look. `scgraph ops episodes-suite --refresh-replanner`
recomputes only the re-planner after such a change (do nothing, the hindsight optimiser and the playbook use the
truth only); `--add-tune N` adds the tune split.

**Muse Glimmer, agent v4** (Meta's open-weights model, 30B, self-hosted with vLLM; `SCGRAPH_LLM_PROVIDER=glimmer`, see
the manual):

| Split | Episodes | Agent [90% interval] | Level 2 | Level 3 | Level 4 | Re-planner | Playbook | Agent total cost, EUR M |
|---|---|---|---|---|---|---|---|---|
| **Test** (held out) | 100 | **0.96 [0.94, 0.97]** | 0.87 | 0.95 | 0.98 | 0.94 | 0.90 | 279 (re-planner 455, playbook 824) |
| Dev | 20 | 0.96 [0.93, 0.99] | 0.82 | 0.95 | 1.00 | 0.95 | 0.89 | 28 |
| Tune | 40 | 0.97 [0.94, 0.98] | 0.45 | 0.98 | 0.97 | 0.95 | 0.88 | 97 |

The agent was developed on dev and tune only; the test episodes were run once, with the final code. It beats the
playbook on 86% of the test episodes and costs EUR 176M less in total than the informed re-planner. Failed reviews: 11
of 552 on test (a review that fails keeps the plan in force). Where it still loses is the smaller years (level 2): a
cheap action chosen early, such as a spot buy of 2%, stays fixed once started, and announcements that never come true
cost a little.

What made the difference, from the traces of earlier runs on Glimmer (scored about 0.77 on test before these changes
and before the overrun reviews): Glimmer added one action at a time and stopped when no single fix showed a gain
(`blocked_assemblies` and `fill_gaps`), found cheap plans and then submitted others because copying 90 actions is hard
(`simulation_id` and the cheaper-plan check), and spent most of its turns on one lookup per call (several components
per call: 7 tool calls per review instead of 16).

A Glimmer turn takes minutes (it reasons before every answer at about 25 tokens a second; the server runs 32 requests
at once, about 500 tokens a second in all), so the 100 test episodes took about 6 hours with 18 in parallel, next to the agent benchmark; tokens
cost nothing, the GPU machine about USD 0.9 an hour.

Planner results: the summaries in `benchmarks/episode_results/` (`muse-glimmer-v4-test`, `final-v4-dev`,
`final-v4-tune`; `scgraph ops episodes-bench`; also Reports, Agent benchmark). The suite and the per-episode runs
are built on the customer's graph and are not published.

## Semiconductor shortages from ShockBench

`benchmarks/shockbench.json` holds, for 50 ShockBench-Flow episodes (task tiny), the weekly share of chip demand served
at the market under the send-the-maximum reference policy. The shortage disruption takes an episode's first shortage window as its weekly capacity curve. The file is
written by `benchmark/scripts/export_shockbench.py`, which needs ShockBench's own environment (Python 3.13); the app
only reads it.
