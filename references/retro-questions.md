# Retro questions

Prompts, not a form: answers go under Keep or Change in the report, and a question with
nothing to say is skipped. Evidence comes from the metrics where they apply and from the
narrative file (or your own view of the run) for the rest; an invented answer is worse
than none.

## Keep

- What went cleanly on the first try, and which instruction, brief, or setup made that happen?
- Which agent, phase, or tool choice was clearly right (good model for the job, good split, good brief)?
- What should the next run copy exactly as it was?

## Change

- Where did the run stall, loop, retry, or wait? What was it waiting for?
- Where did the user have to step in: a question, a correction, a re-brief, a manual check?
- What was read, computed, or explained more than once? By whom?
- What did the run miss that was caught later (by the user, a reviewer, a test)?
- Which step drifted outside its scope, and what let it?
- Which instruction was followed too literally, or ignored, by the model that executed it?
- Where did the money go, by model and by step, and does that match where the value was created?
- What could a cheaper model or a lower effort have done just as well?
- What behaved differently from what the skill or agent text assumes (a newer model, a changed tool, a changed repo)?
- Which gate, artifact, or reminder produced no signal and only cost time?
