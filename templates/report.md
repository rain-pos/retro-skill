# Report template

Chat report in the chosen language; the file under the artifacts directory in English.
Every number traces to the analyzer summary; fill only the cells the summary gives and
write `n/a` elsewhere. For a retro focused on one skill run, batch, or workflow, the
Numbers table is that row of the summary (skill runs, batches, or workflow runs) instead
of the main / agents / all rows. Sections with nothing to say get one line, not a paragraph.
Size: Keep 2–5 items, Change 3–6, Recommendations at most 5, one or two lines each; the
whole report fits on one screen of chat.

```
## Retro — <label>

**Scope:** session <topic> (or: focused on <skill run | agent batch | workflow>) · <date range> · data: analyzer (<id8>)
**Language:** <chosen language> (<setting | guessed | asked | argument>)

### Numbers
| scope | api calls | output | cache read | hit | active | cost (API-equivalent) |
|---|---|---|---|---|---|---|
| main | … | … | … | … | … | … |
| agents (N total: k nested, w in workflows) | … | … | … | … | Σ active | … |
| all | … | … | … | … | = main | … |
Model / effort: <timeline>. Cost by model: <…>.
Agents: <N> (<types>), parallelism <…>x, critical path <agent or phase> (<active>).
Compactions: <N> (<triggers>). Friction: <errors / denials / api errors>. Context tax: ~<N>k tokens total (<top source>).

### Keep (worked, do not change)
- <what> — <evidence>

### Change (hard, wasteful, missed)
- <what> — <evidence: metric row or narrative event>

### Recommendations
1. <target file or parameter> — <change> → <expected effect>
2. …

### Ledger
Row appended to (or replaced in) <ledger path>.
```

## Ledger row

One Markdown table in the ledger file; one row per retro:

```
| date | id8 | label | scope | agents | output tok | cache hit | active | cost | top recommendation |
|------|-----|-------|-------|--------|------------|-----------|--------|------|--------------------|
```

`scope` ∈ `session`, `skill`, `batch`, `workflow`; `agents` = every sub-agent journal
counted, nested and workflow agents included (write `118 (0 nested, 101 workflow)`); `active` = active time from the summary, not wall-clock (agents too: a reviewer kept for a day counts its working stretches, not the day); `cost` =
the analyzer's API list-price equivalent (subscriptions are not billed per token; `n/a`
without prices); write `\|` for any pipe inside a cell.
