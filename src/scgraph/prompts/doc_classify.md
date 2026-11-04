You read the beginning of a document and decide which parts of a supply-chain graph schema its text describes, so a later step extracts facts with only those parts of the schema.

Return the schema domains the text concerns and the entity and relation types it states facts about. Include a type only when the text states facts of that type (an id, a name, an attribute, a link), not when it is merely mentioned. Give a one-sentence summary of the document.

Say whether the document reports a supply disruption: an event such as a supplier's outage or fire, an export restriction, a closed route or port, a tariff or a shortage. Such a report (a supplier notice, an alert, a news item) becomes a disruption to plan, not facts about the network; for it the domains and types may be empty.

Call the tool `submit_document` exactly once.

{schema}
