# StudyOS uses turn-local active Session and Schedule context

StudyOS binds an explicitly started learning Session to its Hermes conversation and injects a bounded view of the Learning Contract and current Activity through `pre_llm_call` user-message context, removing the binding on `finish`. This repeats a small amount of state each turn, but keeps the system prompt byte-stable, preserves prompt caching and message alternation, and prevents inactive projects from silently influencing unrelated conversations.

When the Learner explicitly starts `study-os` or requests StudyOS planning,
the same hook reads the active Learning Project's validated Schedules and
injects only phases whose date range, and events whose exact time range,
contain the current instant. Overlapping Schedules are preserved as concurrent
constraints. Outside those explicit entry turns, Schedule context is absent;
this keeps unrelated conversations isolated while making the current plan
available before the model chooses its next learning action.
