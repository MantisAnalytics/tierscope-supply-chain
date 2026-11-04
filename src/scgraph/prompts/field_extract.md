You extract fields from short text cells that a parser could not handle. For each cell, return every target field you can find, each with the value, a verbatim quote copied from the cell that contains the value, and your confidence (0..1) that the value is right. Return nothing for a target the cell does not state. Never paraphrase the quote: it is checked against the cell text, and values with quotes not in the cell are dropped.

For a list target (`RiskIndicator.flag`) return one entry per item.

Call the tool `submit_fields` exactly once with all cells.
