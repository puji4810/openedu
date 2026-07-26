# StudyOS keeps proactive proposals separate from Schedules

StudyOS derives Intervention Queues without writing state and persists selected items only as Plan Proposals; accepting a proposal records the Learner's decision but still does not mutate a Schedule, which must pass an explicit `schedule.validate` and `schedule.save`. Cron sessions may save new proposals but are blocked from deciding them or saving Schedules. This adds a deliberate review step, but prevents unattended evidence analysis or model judgment from silently reallocating the Learner's time.
