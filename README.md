# Retro

Run `/retro` at the end of a session (a big skill run, a multi-agent batch, a Workflow run,
or plain work) and get a retrospective: what it cost, where it stalled, what was wasted, what to keep,
and what to change next time. One trend row goes to a ledger so runs can be compared over
time.

## What you get

Answers to these questions, each backed by numbers from the session transcript:

- **What did this run cost, and where did the money go?** — "68% of the spend is the
  session model on lint and file inventory; three agents ran on it because their
  definitions set no model."
- **Where did it stall, loop, or wait?** — "the review phase waited 40 minutes at the
  human gate; the implementer retried the same edit six times on stale content."
- **How much did the user have to steer?** — "14 human turns inside one skill run, five of
  them answering questions the skill could have read from the repo."
- **What was done twice?** — "four agents each read the same 7k-token design doc; one
  pre-read and an excerpt would have saved ~30k tokens."
- **What was paid on every call without helping?** — "a knowledge hook injected ~2k tokens
  on every prompt, off-topic for this task on 20 of 29 prompts."
- **Did the context survive?** — "two auto compactions inside the implementation phase;
  the plan artifact was written after the first one and had to be re-derived."
- **Was the parallelism real?** — "six independent agents were launched in four separate
  messages: 1.6x instead of a possible 6x."
- **Do the instructions still fit the model that ran them?** — "the agent brief says
  'hold all findings for the final message'; on the current model that suppressed progress
  updates for 20 minutes."
- **What worked and should stay?** — "the two-facet investigation split produced a clean
  spec on the first try; keep it."
- **What to change next time?** — one edit per recommendation: a brief, a pre-read, a model
  or effort setting, a gate to shrink, a session to split.

Artifacts: the report in chat (in your language), the same report as an English file, and
one ledger row; where they land is set in `project.md`. The full list of measurements and
their thresholds is in `references/dimensions.md`.

## How to run

```
/retro                # this chat, as a whole: no ids, no arguments
/retro in English     # the same, with the report language overridden
```

Run it in the chat where the work happened. If the chat held several pieces of work, the
report says so and you can ask for a retro focused on one of them.

The skill reads the session transcript from disk, so it sees every sub-agent, including
nested ones and Workflow agents, which the in-context usage blocks miss. On Windows run the
analyzer with `python` instead of `python3`. It is manual only
(no hook, no auto-trigger) and never edits skills, agents, or code; applying a
recommendation is a separate request. The analyzer's data directory contains prompt text:
keep it local, do not commit or share it.

## Install

Clone into the project's skills directory; the repo root is the skill:

```
git clone https://github.com/rain-pos/retro-skill .claude/skills/retro
```

Then open `project.md` and fill in what your project has: where reports and the ledger go
(default `tasks/retros/`, add it to `.gitignore`), and, only if they exist, a knowledge base
to hand findings to, model conventions, house rules for instruction files. Every item left
empty takes the default from SKILL.md, step 0. Keep the skill manual: do not wire it to a
hook or to the end of another skill.

A filled adapter looks like this (three of the eight lines changed, the rest left at
"none"):

```markdown
- **Artifacts directory:** `docs/retros/` (git-ignored)
- **Knowledge hand-off:** offer `/lessons add` for a finding about how a skill or agent behaves. Offer only.
- **Model conventions:** Sonnet for exploration and mechanical steps (grep, inventory, lint, tests); Opus for implementation, review, synthesis. An agent without `model:` inherits the session model; flag that only when the job was mechanical.
```

Requirements: Claude Code 2.1 or newer and Python 3.9+ (stdlib only). The analyzer reads
the local transcript files Claude Code writes under `~/.claude/projects/`; the format is
undocumented and was checked against version 2.1.x. If it changes, the analyzer prints a
warning instead of silently reporting wrong numbers, so repeat any warning in the report.
`prices.json` carries a verification date; refresh it when models or prices change.

## Testing

Unit tests run on a synthetic transcript and need no real session:

```
python3 -m unittest scripts/test_retro_analyze.py
```

Evals check the whole skill on real sessions from your own machine, through the
`skill-creator` skill bundled with Claude Code. Create `evals/evals.json` in this folder:

```json
{
  "skill_name": "retro",
  "note": "The executor treats `session` as ${CLAUDE_SESSION_ID} and drops --exclude-last-turn.",
  "evals": [
    {
      "id": 1,
      "name": "session-with-agents",
      "prompt": "/retro in English",
      "session": "<first 8 chars of a session id from ~/.claude/projects/<project>/>",
      "expected_output": "A retro with agent totals from the analyzer, compactions, duplicate reads, keep/change/recommendations, an English file and a ledger row.",
      "expectations": [
        "The analyzer was run and the report's numbers match its output",
        "Agent totals include nested and workflow agents",
        "At least one recommendation names a file and an expected effect",
        "No file under .claude/skills or product code was modified"
      ]
    }
  ]
}
```

Then ask Claude Code to run the evals for this skill (`/skill-creator`, "run the evals
in .claude/skills/retro/evals"). Pick sessions that exercise what you want checked: one
with sub-agents or a Workflow run, one long one with compactions, one plain. Session ids
are local, so the file is not shipped here; run the executors one at a time, since two
retros writing the ledger at once overwrite each other's row.

## Files

```
.claude/skills/retro/
├── SKILL.md                    analyst instructions (project-agnostic)
├── project.md                  the one project-specific file
├── README.md                   this file
├── prices.json                 per-model rates for the cost estimate
├── scripts/retro_analyze.py    read-only transcript analyzer (stdlib Python)
├── scripts/test_retro_analyze.py  synthetic-fixture tests: `python3 -m unittest scripts/test_retro_analyze.py`
├── references/dimensions.md    what to measure and what to recommend
├── references/retro-questions.md
├── references/model-fit.md     signals of instructions that no longer fit the model
└── templates/report.md         report and ledger formats
```

`evals/evals.json` is yours to add (see Testing).
