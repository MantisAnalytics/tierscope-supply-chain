You wrote a mapping spec for a table region. Code executed it on every row and found problems, listed in the message with up to five failing raw values per column. Return a corrected spec through `submit_mapping`, in the same format as before: keep what worked, fix what failed.

Typical fixes: a transform that does not fit the values (a number given a date transform, Yes/No labels without a bool transform, thousands separators), the wrong key column, a missing `when_present` on an optional relation, a reference to a column of the wrong entity. If a problem is in the data itself and no transform can fix it, keep the column and lower its confidence.

Call the tool `submit_mapping` exactly once.

{schema}
