# StudyOS budgets prompt fragments instead of capping them

StudyOS assembles `prompt_context.load` from explicitly delimited regions inside each `SKILL.md` — the text between `<!-- prompt-context:begin -->` and `<!-- prompt-context:end -->`, with unmarked files falling back to their whole text — and fits those regions into one shared token pool instead of inlining whole files under fixed per-kind character caps. Only the marked region is charged to the budget, so a skill document can carry unbounded reference prose that never reaches the model. Tokens are estimated CJK-aware, counting CJK codepoints at one token each and the rest at `ceil(n / 4)`, because the global Hermes estimator under-counts the Chinese in 考研 content roughly fourfold.

The forcing evidence was headroom. `study-os` occupied 1997 of its 2000
characters, `study-plan` 2499 of 2500, and the worst reachable combination
consumed 5980 of the 6000-character ceiling, leaving 20 characters for the
project summary. One added sentence anywhere returned zero fragments and
silently degraded the agent to generic Hermes with no StudyOS rules — a total,
invisible failure triggered by editing documentation.

The per-kind `*_max_chars` values stop being hard caps that reject an oversized
fragment and fail the entire request, and become priority-ordered token
reserves — a guaranteed floor rather than a ceiling — drawn from one shared
pool, so exceeding a limit now truncates the lowest-priority fragment with a
warning instead of returning zero fragments. Degradation follows a fixed
ladder: truncate the project summary, drop it, truncate or drop the domain
fragment, then truncate intent and base to their allocations, cutting at the
nearest section, paragraph, or line boundary. A missing domain skill warns
instead of failing, and base and intent are never dropped because they carry
the routing contract. This costs a deliberately conservative estimator and
warnings that must be read to notice loss, but it makes StudyOS routing
survive its own documentation.

Two consequences the first cut got wrong. Reserves are floors on the read path,
so nothing may spend them as a ceiling on the write path either — and the same
argument retires the write ceiling altogether. `prompt_summary.md` is project
memory, not prompt text: `update_prompt_summary` stores it whole and reports, in
tokens and in characters, how much of it `prompt_context.load` can reach. Any
prompt-policy number used as a storage ceiling gives that field two denotations
(a bound on one file, and a bound on the sum of four fragments), makes lowering
the injected prompt destroy stored memory, and — because a boundary-preferring
cut can land far below its ceiling — still refuses text the reader would have
delivered. The read path pays for its own robustness where the cost is incurred:
a fragment of *n* tokens is at most *4n* characters, so the reader scans only
that far into a file the user, a sync client or a merge can make arbitrarily
large, which is identical to reading all of it.

And because a boundary cut lands below its grant, the allocator asks each
fragment what its grant really costs and passes the difference down the ladder.
That slack is kept apart from the budget the funding decisions are made against:
it may enlarge a fragment the pool already funded, never fund one the pool could
not, because a fragment resurrected on three reclaimed tokens arrives as a
clipped heading — the stub the ladder drops fragments to avoid. The reclaim
matters most in the low-pool branch, where every fragment is cut and a blind
split burned 17-21% of the pool.
