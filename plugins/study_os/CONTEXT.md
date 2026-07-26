# StudyOS Learning

StudyOS supports a learner pursuing observable capabilities across exams, engineering, research, and other domains. It separates source knowledge, performed activities, immutable evidence, and derived judgments so progress remains explainable.

## Application Boundary

`StudyOSApplication` is the transport-independent boundary for HTTP and
non-model clients. Callers submit a `StudyQuery` or `StudyCommand`; the
application owns workspace resolution, validation, persistence ordering, and
stable error semantics. The FastAPI adapter lives in
`dashboard/plugin_api.py`, mounted canonically at
`/api/plugins/study_os`. The bundled `/api/study` prefix is a compatibility
alias only.

The model-facing surface remains the two established tools,
`study_activity` and `study_coach`. Their schemas are deliberately not folded
into the HTTP query/command catalog, preserving the narrow model-tool waist.

`StudyReviewReadModel` owns Vault-scoped due-review, queue, statistics, and
concept projections. HTTP, overview, and model-tool adapters reuse this module
instead of rebuilding selection rules. `StudyNoteCatalog` owns safe Vault note
discovery and parsing underneath it; neither module expands the model schema.

`outcomes.py` owns Intervention Outcome derivation. It stores nothing: an
accepted Plan Proposal already records `decided_at` and the status each
Intervention was reasoning about, so effectiveness is recomputed from attempts
rather than tracked alongside them.

`adherence.py` owns Plan Adherence derivation, the other half of the same
question: `outcomes.py` asks whether an accepted recommendation helped, this
asks whether it happened at all. It stores nothing either — an applied event
carries its own provenance and every attempt its own timestamp — and it
measures only events a Plan Proposal wrote, because holding the recommender to
plans the learner authored would credit it for advice it never gave.

`calibration.py` owns every correction the recommender makes to its own
constants, and is the only consumer of `outcomes.py` and `adherence.py`:
observed activity duration replacing a Domain Pack default, a day-capacity
factor replacing an unexamined phase budget, and a bounded priority delta from
measured effectiveness. Each correction is sample-gated, bounded, and reports
the number it started from, so a surprising plan traces back to an observation
rather than to a score. Non-adherence never reaches the effectiveness path; it
reaches capacity, where "the day was too full" is the finding it supports.

`day_plan.py` owns the Day Plan projection: study-window derivation, event
packing, per-phase budgets, and which Schedule an event belongs to. It is pure
and writes nothing; `plan_proposal.apply` is the only path that turns its
output into Schedule events, and it is restricted to events. A capacity factor
may tighten a day's budget but never inflate it — a phase's `effort_minutes` is
the Learner's own statement of intent, and measurement may show that statement
optimistic without volunteering them for more.

`prompt_budget.py` owns Prompt Context Fragment budgeting: marked-region
extraction, CJK-aware token estimation, and allocation of one shared pool
across the `base`, `intent`, `domain`, and `project_summary` fragments in that
priority order. Fragments degrade rather than fail — an over-budget request
retires whole fragments from the bottom up with a warning each, and `base` and
`intent` are only ever truncated, never dropped, because those two carry the
routing contract. A missing `base` or `intent` source is an error; a pool too
small to give each of them a body plus an ellipsis is the one remaining
`PROMPT_CONTEXT_TOO_LARGE`.

The `project_summary` fragment is the exception to "a region of a Skill
document": it is `prompt_summary.md`, written by the learner through
`study_project(action="update_prompt_summary")`. It is project *memory*, so the
write path stores it whole and only warns how much of it the reader can reach;
no prompt-policy number is a storage ceiling. The read path bounds its own cost
instead, scanning at most four characters per pool token — the most any grant
can reach — because that file is editable outside StudyOS and is read on every
turn.

## Language

**Learner**:
The human whose demonstrated capabilities StudyOS models.
_Avoid_: User profile, student record

**Learning Project**:
A bounded learning purpose that owns objectives, sessions, evidence, and planning constraints.
_Avoid_: Course, vault, exam plan

**Objective**:
An observable capability the learner intends to demonstrate under stated success criteria.
_Avoid_: Topic, chapter, vague goal

**Learning Contract**:
The explicit agreement for one Session: its mode, objective, time budget, assistance level, and required evidence dimensions.
_Avoid_: Prompt settings, study preferences

**Session**:
An ordered, resumable sequence of Activities governed by one Learning Contract.
_Avoid_: Chat, daily log

**Activity**:
A bounded task offered to the Learner to produce a response or Artifact that can be evaluated.
_Avoid_: Tool call, lesson content, schedule event

**Artifact**:
A learner-produced output such as an answer, derivation, code change, benchmark, critique, or experiment result.
_Avoid_: Generated explanation, source material

**Evidence Event**:
An immutable observation of learner performance, including evaluator provenance, assistance used, and links to its Activity and Artifacts. An Attempt is the interactive-response form of an Evidence Event.
_Avoid_: Mastery update, progress score

**Competency Snapshot**:
A derived, revisable projection of demonstrated capability across evidence dimensions at a point in time.
_Avoid_: Mastery state, permanent level

**Evidence Dimension**:
The kind of capability an Evidence Event supports: recall, recognition, execution, explanation, near transfer, or far transfer.
_Avoid_: Difficulty, review level

**Intervention**:
An evidence-backed recommendation for what should happen next and why.
_Avoid_: Generic advice, automatic schedule mutation

**Intervention Queue**:
A time-sensitive, derived ordering of the most valuable current Interventions across a Learning Project. It is a read model, not durable learning truth or a Schedule.
_Avoid_: To-do list, fixed curriculum, mastery queue

**Intervention Outcome**:
A derived comparison between the verification status an accepted Intervention was reasoning about and the evidence recorded after its decision. An accepted Intervention with no later evidence is not-attempted, which is a statement about adherence and never about whether the recommendation was sound.
_Avoid_: Success rate, mastery gain, intervention score

**Plan Adherence**:
A derived comparison between the events an accepted Day Plan wrote into a Schedule and the evidence recorded on that date. An event that has not yet ended is pending, not missed, and evidence matching no planned event is off-plan study rather than absence.
_Avoid_: Completion percentage, discipline score, streak

**Calibration**:
A bounded, sample-gated correction the recommender applies to its own stated constants from what it has measured about itself. It never overrides a judgment about the Learner and never changes which Intervention an Objective receives.
_Avoid_: Learning rate, model training, adaptive difficulty

**Plan Proposal**:
A durable candidate that preserves selected Interventions and their evidence provenance for Learner review. Acceptance records a decision; applying it to a Schedule remains a separate explicit act.
_Avoid_: Automatic plan, scheduled task, accepted Schedule

**Day Plan**:
A projection of the Intervention Queue onto one date's concrete events, placed inside a study window derived from the Learner's own timestamped attempts and bounded by the covering phase's remaining effort. It is carried on a Plan Proposal; applying it writes events and never phases.
_Avoid_: Schedule, to-do list, fixed timetable

**Source Anchor**:
A version-aware reference to the exact source location supporting an Activity or claim.
_Avoid_: Unqualified URL, free-form citation

**Domain Pack**:
A domain-specific vocabulary, activity policy, and rubric that uses the shared StudyOS evidence and Session model.
_Avoid_: Separate learning backend, prompt-only persona

Each built-in Domain Pack is one discoverable module under `domain_packs/`
exporting `PACK: DomainPack`. The Pack owns its Activity Adapter, optional
prompt Skill, Intervention duration, project defaults, and Schedule template.
`domain_pack` is authoritative; `domain` is used only when a manifest has no
Pack id. A new domain therefore adds one module and its contract tests without
editing the shared runtime or model-tool schemas.

**Activity Adapter**:
A Domain Pack implementation that proposes Activities and validates domain-specific Evidence Events at the StudyOS seam.
_Avoid_: Tool wrapper, domain database

**Prompt Context Fragment**:
A delimited region of a Skill document injected as bounded routing instruction for one turn, charged against a shared token budget by priority. Prose outside the markers stays in the document as reference and never reaches the model.
_Avoid_: Whole skill file, system prompt edit, capped prompt template
