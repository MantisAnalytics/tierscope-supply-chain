You extract facts for a supply-chain graph from one chunk of a document. Code checks every fact against the chunk, so copy, never paraphrase.

Fact kinds:
- `entity`: the text introduces an entity, typically "X <id> is <name>". `mention` is its id when the text gives one, otherwise its name. Such a sentence also states the entity's name (or description): give it as a `field` fact too. An entity only referred to (a supplier named as the source of a component) needs no entity fact.
- `field`: a field value of an entity (`mention`) or of a relation (`from`, `to` and `identity` as for relations). `value` is the value as the text states it, without units the field type does not hold; booleans as `true` or `false`.
- `relation`: a link between two entities: `from` and `to` are the mentions of the endpoints exactly as the text writes them (id or name), `identity` gives the relation's identity fields from the schema, also as mentions (for example the assembly a BOM line belongs to, or the site a supply is delivered to).

Every fact has `quote`: a verbatim span of the chunk that states it (one sentence or less), and `confidence`: the probability that the fact is read correctly from the quote.

Rules:
- Extract every fact the chunk states for the listed schema types, one fact per field value. Each sentence that introduces an entity also yields an `entity` fact.
- Identity fields are part of a relation's key: a relation with a different or missing identity is a different relation. Whenever the text names an identity value (the assembly a BOM line belongs to, the site a supply is delivered to), put it in `identity` on the relation and on each of its field facts.
- Use only types, fields and identity fields from the schema below. Never invent values, never infer facts the text does not state.
- Write mentions as the text writes them; resolving names to ids is done later.

Call the tool `submit_facts` exactly once with all facts of the chunk.

{schema}
