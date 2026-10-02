---
name: retro
description: Retrospective of the current Claude Code session — its skill runs, multi-agent batches and Workflow runs — what it cost (tokens, cache, dollars), where it stalled or looped, what was duplicated or missed, which model/effort choices were off, and what to change next time. Appends one trend row to a ledger. Manual only, run after the work is done.
argument-hint: "[in <language>]"
disable-model-invocation: true
allowed-tools: Bash(python3 *retro_analyze.py*), Bash(python *retro_analyze.py*)
---

# Retro

You are the analyst. The work already happened; look back at it and say what it cost,
what went well, what was hard or wasteful, and what to change next time. You never re-run
the work and never edit skills, agents, or product code: you recommend, and applying a
recommendation is a separate step the user asks for.

## 0. Load the project adapter

Read `${CLAUDE_SKILL_DIR}/project.md`. Every item it does not state takes the default
below; a missing file means all defaults.

| item | default |
|---|---|
| artifacts directory | `tasks/retros/` (relative to the repo root) |
| ledger | `<artifacts directory>/ledger.md` |
| knowledge hand-off | none |
| skills and agents | `.claude/skills/*/SKILL.md`, `.claude/agents/*.md`, `.claude/skills/*/agents/*.md` |
| model conventions | none: judge model fit by the job's nature only (`references/model-fit.md`) |
| language | English by default; the user's setting or prompts decide |
| house rules for instruction files | none |

## 1. Scope

Arguments: `$ARGUMENTS` (empty = the whole current session).

| argument | scope |
|---|---|
| (none) | the current session |
| `in <language>` | the current session, report language override |

When the session holds several distinct pieces of work (the skill-runs table, the turn
table, or the narrative show it), say so in one line inside the report and offer a
retro focused on one of them afterwards (the user asks in prose; you narrow the analysis).

## 2. Collect metrics (deterministic, always)

Run the bundled analyzer (`python` instead of `python3` on Windows); it prints a compact summary:

```
python3 "${CLAUDE_SKILL_DIR}/scripts/retro_analyze.py" --project-dir "${CLAUDE_PROJECT_DIR}" --session ${CLAUDE_SESSION_ID} --exclude-last-turn
```

- Output files go to `<artifacts directory from project.md>/.data/` by default (`--out`
  overrides). The analyzer prints warnings at the end (stale prices, fast-mode calls, a
  transcript format it no longer understands, a data directory that is not git-ignored):
  repeat them in the report.
- A skill the user asked for in prose (no slash command) shows up as a run with `via=model`;
  if a skill has no run row at all, use the turns whose `skills` column names it.
- The summary is the source of truth for every number in the report. In-context `<usage>`
  blocks from Agent results undercount async and nested agents; do not use them for totals.
- `--out` writes two files: `<id8>.json` (full data; read it only for a specific question
  the summary does not answer) and `<id8>-narrative.md` (every human prompt, mid-turn user
  messages, the assistant text before each prompt, agent hand-backs, questions asked, in
  order). The summary prints both paths and the narrative size; for a long session read
  the narrative in parts or grep it for the scope instead of loading it whole. Re-running
  overwrites both files; that is expected.

If the transcript cannot be found, say so and offer the in-context retro (qualitative only,
every number `n/a`). Never invent a number.

## 3. Report language

Use `setting=` from the summary header unless it is `-`; else `guess=` unless it is
`cyrillic` or `other`; else ask once. The explicit `in <language>` argument wins over all of these.
The chat report and the conversation use that language; the report file and the ledger row
are English.

## 4. Analyse

Work through `${CLAUDE_SKILL_DIR}/references/dimensions.md`: each dimension names the
summary rows to read, what healthy looks like, and the recommendation pattern when it is
not. Keep two lines of evidence apart:

- **quantitative**: the summary tables;
- **qualitative**: what you saw in the run plus the narrative file; after a compaction, or
  for turns you did not watch, the narrative file is the only source. Memory of work you did
  not watch is not evidence: read the narrative or mark the qualitative sections "not
  assessed". Use `references/retro-questions.md` as prompts.

When friction points at a specific instruction file (a skill, an agent definition, a rules
file), read that file and apply `references/model-fit.md`. Judge fit against the model
that executed the file: the sub-agent's `model` column, or the model timeline for the time
range of a skill run.

## 5. Recommend

Every recommendation names a target, a change, and the expected effect, and is small
enough to do in one edit. The target is a repo-relative path that exists (check it with
Glob before writing; no globs, no nicknames like "prompt 12") plus the heading or line to
edit; a parameter is named with its file. Order by payoff.

Before writing a recommendation into a file, ask who reads that file:

- **Match generality to reach.** A file read by every task (a rules section, a shared
  prompt, a convention file) takes only the general rule, stated without this session's
  specifics. The specific instance (the function, the paths, the store) goes to the
  narrowest artifact that will be read next: the next task's spec, the knowledge hand-off
  from `project.md`, or nowhere. One recommendation per shared file per retro; put the
  rest in narrower places.
- **An existing rule that was not followed.** Before proposing a rule, grep the project's
  instruction files for it: `CLAUDE.md` and the files it loads, `.claude/rules/`, and the
  house rules `project.md` names. If the rule already exists, do not propose it again anywhere: say
  where it lives, why it did not apply this time (the prompt never pointed to it, a
  research list was trusted as complete, a plan step skipped it), and recommend the
  change that removes that cause.
- **Recurring friction** (a denial, a tool error, a hook failure seen three or more times)
  gets at least one line saying what to change so it stops, even when bigger items come
  first.

Also list what worked and should stay; a retro that only says "change" is not trustworthy.

Offer, do not perform: applying a recommendation is a new task the user asks for.

## 6. Output

1. Report to chat in the chosen language, following `${CLAUDE_SKILL_DIR}/templates/report.md`
   line by line: the Scope and Language lines, the table headers, the section names. The chat
   report and the file carry the same content, targets included; only the language differs.
2. Write the same report in English to `<artifacts-dir>/<YYYY-MM-DD>-<id8>-<label>.md`,
   `label` = the session's dominant topic, slugified. Before writing, glob
   `<artifacts-dir>/*-<id8>-*.md`: if a file with the same Scope line exists, reuse its name
   with the next free `-2`, `-3` suffix instead of inventing a new label.
3. Append one row to the ledger (format in the template); create the file with its header
   if missing. If the file exists with a different header, append the row under a new table
   with the template header and say so in the report. The ledger keeps one row per `id8`
   and scope (a repeat replaces that row), while report files keep the history. A repeat
   does not read the earlier report as input: the transcript is the source, not the
   previous verdict.
4. If `project.md` names a knowledge hand-off, offer it in one line for findings that would
   help people outside this session (how a skill or agent behaves). Offer only.
5. End with the "want me to apply recommendation N?" line.

Writes: the analyzer's `.data/` files, the report file, the ledger row. Nothing else.
