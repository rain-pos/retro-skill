# Project adapter

The only file to edit when installing this skill. Paths are relative to the repo root.
Every item is optional: delete a line and SKILL.md step 0 supplies the default.

- **Artifacts directory:** `tasks/retros/` (keep the path in backticks; the analyzer reads it from this line). Add it to `.gitignore`: the analyzer's `.data/` subfolder contains prompt text.
- **Ledger:** `tasks/retros/ledger.md`.
- **Knowledge hand-off:** none. If the project has a place for lessons about how skills and agents behave (a knowledge base, a lessons file, a wiki command), name the command to offer here. Offer only; the retro never writes there itself.
- **Skills:** `.claude/skills/<name>/SKILL.md`; skills shipped inside a plugin appear as `<plugin>:<name>`.
- **Agents:** `.claude/agents/*.md`, plus agents bundled inside a skill or plugin (`.claude/skills/<name>/agents/*.md`).
- **Model conventions:** none. If the team has a rule for which model does what (for example: a cheaper model for exploration and mechanical steps, a stronger one for implementation, review, synthesis), state it here so model-fit findings follow it instead of the job's nature alone.
- **Language:** English by default; follow the user's language setting or the language of their prompts. Detect, do not assume.
- **House rules for instruction files:** none. If the project has a rule for how instruction files are written (length, style, what not to put in them), point to it here; recommendations that touch instruction files then follow it, and the retro does not restate rules already there.
