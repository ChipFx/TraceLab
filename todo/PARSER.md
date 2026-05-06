# PARSER — Pending thought

## Think about the below proposal

The ambiguity between integer column indices and numeric column names lives in
`_parse_addgroup_value` in `core/data_loader.py`. After `csv.reader` strips
quotes, `"101"` and `101` are indistinguishable — both become the Python string
`"101"`, and `int("101")` succeeds, so the value is stored as the integer `101`
and treated as a 1-based column index (which is out of range for a 33-column
file → silently dropped).

The current workaround in `csv_parsers/tracelab_native.py` resolves this with a
"column-name first, then index" heuristic, which is safe for all files TraceLab
itself produces (the exporter always quotes member names).

A cleaner, semantically airtight fix would be to make `_parse_addgroup_value`
quote-aware before handing off to `csv.reader`. Re-scan the raw `inner` string
for originally-quoted tokens, and only allow `int()` conversion on tokens that
were *not* quoted:

```python
import re
quoted_tokens = set(re.findall(r'"([^"]*)"', inner))

for item in items[1:]:
    if item in quoted_tokens:
        members.append(item)        # was explicitly quoted → always a name
    else:
        try:
            members.append(int(item))   # unquoted → could be a 1-based index
        except ValueError:
            members.append(item)
```

This would let `_parse_addgroup_value` return the right type from the start and
remove the need for the column-name-first heuristic in the resolution code.

**Trade-off**: slightly more complex parsing in one place vs. a heuristic in
another. Not urgent — the current fix is safe for everything TraceLab produces
and for any plausible manually-written file.
