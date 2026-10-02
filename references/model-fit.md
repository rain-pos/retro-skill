# Model fit — do the instructions still fit the model that ran them?

Apply this only to files the retro has evidence about: a skill, agent, or rules file that
was loaded in the run and is linked to friction (questions, rework, over- or under-doing,
literal misreads). Read the file, check the signals, and judge each hit against the model
that actually executed it, not the session model.

## Calibrate to the reader's role

| the file is read by | tends to | so in its instructions |
|---|---|---|
| an orchestrator, or any current-generation model on open-ended work | follow text literally; plan and verify on its own; over-trigger on emphasis; under-narrate when told to hold updates | say things once at normal volume; delete "be thorough / do not be lazy / think step by step"; state scope explicitly; give the goal, not a step script for judgment work |
| a worker on a narrow, contract-style job (often a cheaper model) | need the contract spelled out; drop instructions stated far away | keep the brief complete and local; reinforcing repeats of a critical rule are fine here |
| anyone | fill missing context with safe defaults | audience, quality bar, constraints, and reasons are never cruft |

A file read by both an orchestrator and a worker gets conflicting advice. Then the fix is
not deletion: split the file, or keep the repeat and remove only the emphasis and the
stale facts.

## Signals (greppable)

Run these over the file; a hit is a lead to read, not a verdict.

| signal | grep | what it usually means |
|---|---|---|
| pressure language | `MUST\|NEVER\|ALWAYS\|CRITICAL\|IMPORTANT` in caps, several per file, no "because" nearby | over-triggering, rigid behaviour on current models |
| hedged requirements | `try to\|if possible\|ideally` on something that is required | read literally as optional |
| thinking incantations | `think step by step\|think harder\|don't overthink\|<thinking>\|<scratchpad>` | redundant with adaptive thinking; control depth with `effort` |
| cadence choreography | `every [0-9]+ (tool calls\|messages\|steps)`, `at most [0-9]+ (words\|bullets)` | written against a chatty model; now starves output |
| update suppressors | `hold (all )?(findings\|results)\|don't narrate\|no interim` | current models under-narrate with these present |
| tool discouragement | `only use tools when\|minimize tool calls` | followed literally; tools go unused |
| retired model names | a full `claude-*` model id in the file that is not a key of `prices.json` (aliases like `sonnet` are fine) | workaround for a retired model, or a pin that will silently drift |
| history in rules | past tense, ticket or PR ids, "no longer", "now works differently" | a diff against a prompt the model never saw |
| dead paths | file paths, commands, flag names in the file | verify each exists; a wrong path arrives looking verified |
| trigger enumeration | description that lists many near-synonym phrases | grows per missed trigger; generalize to intent categories |
| over-scripted judgment | `STEP [0-9]`, long numbered scripts for open-ended work | the model's own plan is usually better; keep order only where order matters |
| scoring vocabulary | `graded\|rubric\|hidden test` | describes the grader instead of the requirement |

## Keep list

Do not flag: context and reasons; exact scripts for fragile operations (destructive
commands, auth, migrations); tool contract detail; prohibitions against failures that
reproduced in this run; calibrated urgency in a skill's trigger `description`; a single
end-of-file recap; repeats that are identical and serve a weaker reader.

## Model and effort choices

- An agent definition without `model:` inherits the session model; check whether that was
  intended. Recommend a cheaper model only when the run shows the job was mechanical (grep,
  lint, inventory, formatting, running tests) and it ran on the most expensive model in use.
- A high thinking share on a mechanical step means effort is too high for it. Skills and
  agents accept `effort:` in frontmatter: suggest a lower effort for phases whose thinking
  share was high and whose output was routine; a higher one for a review or synthesis step
  that produced shallow results.
- Do not recommend a model switch for judgment steps (review, synthesis, verification) on
  cost grounds alone.

## Deep pass

For a file with several hits, offer `/claude-api prompt-audit <file>` (the bundled audit of
instruction files with a proposed diff; `/doctor prompt-audit` is the same audit where
`/doctor` is available). Do not run it inside the retro.
