---
name: study-research
description: Guide research and replication learning with StudyOS.
---

# StudyOS Research Domain Pack

Use only with `domain_pack:"research.v1"`. Tie claims to exact sources. Call
`study_activity(resource="prompt_context", action="load", data={"intent":"teaching"})`;
never mutate system prompts.

<!-- prompt-context:begin -->
## Research Flow

1. Read the objective and source anchors. Separate claim, reported evidence,
   learner inference, and uncertainty. Reading and agent explanation are not
   evidence.
2. Perform the ActivitySpec. For replication, record method, environment,
   command, result, and divergence; for explanation, name an assumption and a
   limitation. Preserve failed and partial results.
3. Report unverified dimensions.

## Research Integrity

Tie every claim to an exact locator and a versioned artifact. One replication
supports only its tested setup. Change one variable for near transfer; require
a falsifiable hypothesis and rejection condition for far transfer.
<!-- prompt-context:end -->

## Session Mechanics

The `study_coach` tool schema is authoritative for field names, enums, and
required keys. This section only records how the research flow above maps onto
the Session lifecycle.

1. **Start.** Open an explicit contract with
   `study_coach(action="start", data={...})`. Include one objective,
   source-backed `objective_ids`, assistance, time budget, and evidence
   targets. `objective_ids` are optional, but any id you pass must already
   exist in the active Learning Project, and in this pack each one should trace
   back to a source anchor rather than to an inference.
2. **Advance.** After each observed learner response, call
   `study_coach(action="advance", data={...})` with `evaluator` and
   `source_anchors`, plus `artifact_refs` for execution or transfer. A
   replication run, a script, a log, or a diff belongs in `artifact_refs`; the
   paper, repository, or ticket it came from belongs in `source_anchors` with
   its `version` and `locator` filled in whenever they are known.
3. **Snapshot and finish.** Use `snapshot` to choose the next probe and
   `finish` when stopping. Reuse the same `session_id` across `start`,
   `advance`, `snapshot`, and `finish`.

## Why These Rules

- **Reading is not evidence.** A learner who has read a paper, and an agent
  that has explained it, have both produced zero observations of learner
  capability. Only an evaluated learner response advances a Session.
- **Failures are results.** A replication that diverges is the most
  informative outcome in this pack. Recording it as `fail` or `partial`, with
  the divergence described, is what makes the next probe worth running.
- **Scope claims to what was tested.** Environment, version, and command
  determine what a single replication supports. Near transfer changes one
  variable at a time; far transfer needs a hypothesis stated in advance
  together with the condition that would reject it.
