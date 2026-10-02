# Dimensions — what to measure and what to say about it

Each dimension: where the number is in the analyzer summary, what healthy looks like, and
the recommendation pattern when it is not. Thresholds are heuristics for a coding-agent
session; state them as such.

## Cost and tokens

- **Where:** `totals` table (main / agents / all), `cost by model`, `by model x effort`;
  for one skill run, its row in `skill runs` (main cost and agent cost separately).
- **Read:** output tokens are the work; cache read is the context re-sent each call; cost
  follows both. Cost per unit of work (per fixed task, per finding, per completed phase)
  says more than raw spend.
- **Flag:** agent cost above main-session cost without a matching share of the output; one
  model carrying almost all cost while doing mechanical steps (see Model fit).
- **Say:** "X% of cost is <model> on <step>; move <step> to <cheaper model or lower effort>".

## Cache efficiency

- **Where:** `hit` columns; `cache write 5m/1h`.
- **Read:** hit = cache read ÷ (input + cache read + cache write). Healthy for a long
  session is above 90%; a sub-agent below 80% usually ran a short job where the write
  dominates (normal) or read the same large files as its siblings (fixable).
- **Write side:** the 1h write rate is 2x input, the 5m rate 1.25x; relative read prices
  per model are in `prices.json`, and on the models with the cheapest reads a miss costs the
  most relative to a hit. The `cache prefix rebuilds` section lists every large write with
  its likely cause (gap longer than the TTL, model or effort switch, other) and what those
  writes cost; quote it when a switch or a pause is part of the story.
- **Say:** "cache rewritten N times after gaps longer than TTL; keep the session moving or
  split it", "agents A, B, C each read <file> (~Nk tokens): have the orchestrator pass the
  excerpt".

## Time and parallelism

- **Where:** header line (`span`, `active`, `tools`, `waiting for human`, `resumes`); agent
  batches (`wall`, `sum`, `parallelism`); workflow phases (`longest`).
- **Read:** span is wall-clock and can be days; active is the union of work gaps under the
  idle cap and tool-call intervals, for the main session and for every agent. An agent
  continued through SendMessage over a day has a long span and a short active; use active.
  A batch is a cluster of agents whose active intervals overlap; parallelism = Σ active ÷
  the cluster's wall. An agent continued after a pause can appear in several clusters, one
  per working stretch. Independent agents that ran one after another form separate
  one-agent clusters (1.0x) when they could have been one.
- **Say:** "agents A and B have disjoint inputs but ran serially; dispatch in one message".
  Name the critical path (the longest agent or phase).

## Agents and delegation

- **Where:** sub-agent table (type, model, depth, span, api, tools, output, errors, cost),
  batches, workflow runs.
- **Read:** nested agents (depth > 1) are real cost the parent's `<usage>` never showed.
  Many tiny agents (a few api calls, under a minute) usually mean work the orchestrator
  could have done itself. A single agent with a huge cache-read and few output tokens is
  re-reading context on every turn: its brief was thin or its task too long.
- **Say:** merge / split / brief better / pass the file once. Quote the agent id and type.

## Model fit and effort

- **Where:** `model timeline`, `by model x effort`, agent `model` column, `thinking`.
- **Read:** `model-fit.md` § Model and effort choices.
- **Say:** "agent <name> ran on <model> for <mechanical job>; set `model: <alias>` in its
  frontmatter", "skill <name>: set `effort: medium` for its implementation phase". Check
  the actual frontmatter before recommending; the roster is in `project.md`.

## Compaction

- **Where:** `compactions` list (trigger, pre → post tokens, duration) and the `compact`
  column of skill runs.
- **Read:** every compaction drops detail the model had; work right after one is at risk
  of repeating or contradicting earlier decisions. Auto compactions inside a skill run mean
  the run is longer than one context can hold.
- **Say:** "checkpoint <artifact> before step N so a compaction does not lose it", "split
  the skill at the natural gate", "one chat, one task: this session mixed N tasks".

## Friction

- **Where:** `friction` (tool errors by tool, permission denials with samples, api errors,
  hook errors, outputs spilled to files).
- **Read:** repeated Edit errors = the model edited from stale content; permission-rule
  denials = an allowlist gap; automode-blocked = the classifier, not a rule; api errors
  with retries = infrastructure, not the prompt; spilled outputs = tool calls too broad.
- **Say:** a friction class that recurred (three or more times) always gets one
  recommendation: for permission-rule denials the `settings.json` allow entry or the
  command form to use instead (the samples show the denied command), for automode blocks
  the action to take out of the loop, for repeated tool errors the tool or sequence to
  prefer, for hook errors the hook to fix.

## Context tax

- **Where:** `context tax` (system prompt, skill listing, hook injections by hook name,
  skill bodies loaded, compact summaries, stop hooks).
- **Read:** everything here is paid on every api call after it lands. A hook that injects
  on every prompt and was wrong for the task (an unrelated knowledge pack) is pure cost.
  A skill body of tens of thousands of tokens loaded for a small question is a candidate
  for progressive disclosure.
- **Say:** "hook <name> injected ~Nk tokens across M prompts, K of them off-topic; tighten
  its trigger", "skill <name> body is ~Nk tokens; move <section> to references/".

## Duplicate reads

- **Where:** `files read by more than one reader`.
- **Read:** the same file read by several agents is the classic waste; the same file read
  many times by one reader is a loop or a lost-context symptom.
- **Say:** pre-read once and pass the excerpt, or point agents at a summary artifact.

## Human in the loop

- **Where:** header (`human turns`, `questions to user`), skill runs (`turns`, `q->user`),
  turn table (`active`, `wait after`, `agents`, `skills`, `prompt`), the narrative file.
- **Read:** many turns inside one skill run = the skill needed steering; questions to the
  user (AskUserQuestion calls plus replies that ended with a question mark) = underspecified
  briefs or missing inputs; long `wait after` at a gate = the checkpoint asks for more than
  a human can answer quickly. A skill-run row is an upper bound: for a one-shot skill (a
  lookup, a capture) use its invoking-turn line; for a multi-phase skill use the turn table.
- **Say:** which input to collect up front, which gate to shrink, which question the
  skill should answer itself from the repo.

## Session hygiene

- **Where:** header (`resumes`, span vs active), branches list, skill runs.
- **Read:** a session resumed many times across days, several branches, or several
  unrelated skill runs is one that compacts, loses context, and is hard to retro. Plan,
  implementation, tests, and verification each deserve a fresh chat when they are large.
- **Say:** where the split should have been.
