"""Pure budget math for StudyOS prompt fragments.

`prompt_context.load` used to inline whole ``SKILL.md`` files under hard
per-kind character caps and fail closed: one oversized file returned zero
fragments and silently degraded the agent to "generic Hermes with no StudyOS
rules". This module owns the arithmetic that replaces those cliffs.

Three ideas, in dependency order:

1. **Marked regions.** ``extract_prompt_fragment`` pulls only the text between
   ``<!-- prompt-context:begin -->`` / ``<!-- prompt-context:end -->`` markers,
   so a document can carry unlimited prose that never reaches the model. A file
   with no markers falls back to its whole text, so unmarked documents keep
   their old behaviour.
2. **CJK-aware estimation.** Hermes' global estimator is ``ceil(len / 4)``,
   which under-counts Chinese by roughly 4x. StudyOS content is 考研-facing, so
   ``estimate_tokens`` counts CJK codepoints at one token each and the rest at
   ``ceil(n / 4)`` -- bit-for-bit the global estimator on pure ASCII. Over-
   estimating is the safe direction: it truncates a paragraph early *and emits
   a warning*, whereas under-estimating overflows the real model context far
   from here with no diagnostic.
3. **One shared pool.** ``allocate`` distributes ``total_max_tokens`` across the
   fragments in priority order ``base > intent > domain > project_summary``,
   funding each kind up to what it wants before the next one is considered.
   Because a boundary cut lands below its grant, ``allocate`` takes a ``measure``
   hook that reports what each kind really spends and hands the slack down the
   ladder, so a funded fragment is never truncated against an under-used pool.
   Slack can only ever *enlarge* a fragment the pool already funded; it may not
   fund one the pool could not, because a fragment resurrected on a few reclaimed
   tokens is exactly the stub this module refuses to emit.

The legacy ``*_max_chars`` policy fields are reinterpreted, not removed: the
same numbers become per-kind *reserves* via ``chars_to_reserve_tokens``. A
reserve does two jobs, and only these two:

* For a ``protected`` kind it is a genuine floor -- higher-priority kinds may
  not spend it, so ``base`` can never eat the pool ``intent`` needs.
* For a ``drop_below_reserve`` kind it is the *viability threshold*: a
  fragment that cannot be delivered at ``min(wanted, reserve)`` is dropped
  outright rather than emitted as a stub. Half a heading and a clipped
  sentence presented as "the domain's operating rules" is worse for the model
  than an honest omission plus a warning.

The four default reserves sum to 1925 tokens against a pool of 1800. That
over-subscription is deliberate and harmless: reserves are not summed
entitlements, so it simply means ``domain`` and ``project_summary`` compete for
whatever ``base`` and ``intent`` leave behind. Do not "fix" it by scaling the
reserves down.

The resulting degradation ladder, which ``allocate`` implements directly:
truncate ``project_summary`` -> drop ``project_summary`` -> truncate ``domain``
-> drop ``domain`` -> truncate ``intent`` -> truncate ``base``. Protected kinds
are never dropped while the pool holds ``MIN_VIABLE_TOKENS`` per protected
kind; below that no fragment can carry a body at all and the caller reports a
pathological policy.

Known, bounded limitations of the estimator: CJK Extension B and beyond
(U+20000+) and emoji are counted at ``ceil(n / 4)`` rather than ~1-3 tokens
each. Neither occurs in StudyOS content today.

Pure module: no I/O, no state, no external dependencies.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

FRAGMENT_BEGIN_MARKER = "<!-- prompt-context:begin -->"
FRAGMENT_END_MARKER = "<!-- prompt-context:end -->"
FRAGMENT_JOINER = "\n\n"

ELLIPSIS = "…"
MIN_BOUNDARY_RETENTION_RATIO = 0.6
# One token for the ellipsis :func:`truncate_to_tokens` always appends, one for
# the body. Below this a grant can only ever produce the empty string.
MIN_VIABLE_TOKENS = 2
SECTION_PATTERNS = ("\n## ", "\n### ")
PARAGRAPH_PATTERN = "\n\n"
LINE_PATTERN = "\n"

RESERVE_KINDS = ("base", "intent", "domain", "project_summary")

# Closed intervals, inclusive at both ends. Western punctuation (smart quotes,
# U+2026, en/em dashes) is deliberately excluded: it is cheap in BPE and
# pervasive in the English markdown, so counting it as CJK would inflate every
# ASCII document.
CJK_RANGES: tuple[tuple[int, int], ...] = (
    (0x1100, 0x11FF),  # Hangul Jamo
    (0x3000, 0x303F),  # CJK Symbols and Punctuation
    (0x3040, 0x309F),  # Hiragana
    (0x30A0, 0x30FF),  # Katakana
    (0x3130, 0x318F),  # Hangul Compatibility Jamo
    (0x3400, 0x4DBF),  # CJK Unified Ideographs Extension A
    (0x4E00, 0x9FFF),  # CJK Unified Ideographs
    (0xAC00, 0xD7AF),  # Hangul Syllables
    (0xF900, 0xFAFF),  # CJK Compatibility Ideographs
    (0xFF00, 0xFFEF),  # Halfwidth and Fullwidth Forms
)


def is_cjk(codepoint: int) -> bool:
    """Return True when ``codepoint`` falls in one of :data:`CJK_RANGES`."""

    return any(low <= codepoint <= high for low, high in CJK_RANGES)


def estimate_tokens(text: str) -> int:
    """Estimate the token cost of ``text``, counting CJK at one token each.

    Non-CJK characters are charged ``ceil(n / 4)``, identical to Hermes'
    ``agent.model_metadata.estimate_tokens_rough``, so this function agrees
    with the global estimator on pure ASCII input.

    Monotonicity lemma (relied on by :func:`truncate_to_tokens`): for any text
    and any ``0 <= i <= j <= len(text)``,
    ``estimate_tokens(text[:i]) <= estimate_tokens(text[:j])``. Appending a
    character either adds exactly one CJK token or raises ``ceil(n / 4)`` by
    zero or one; it never decreases.

    Subadditivity lemma (relied on for ellipsis accounting):
    ``estimate_tokens(a + b) <= estimate_tokens(a) + estimate_tokens(b)``,
    because CJK counts add exactly and
    ``ceil((x + y) / 4) <= ceil(x / 4) + ceil(y / 4)``.
    """

    if not text:
        return 0
    cjk_count = sum(1 for char in text if is_cjk(ord(char)))
    other_count = len(text) - cjk_count
    return cjk_count + (other_count + 3) // 4


def _cjk_prefix_counts(text: str) -> list[int]:
    """Return ``counts`` where ``counts[i]`` is the CJK count in ``text[:i]``."""

    counts = [0] * (len(text) + 1)
    running = 0
    for index, char in enumerate(text):
        if is_cjk(ord(char)):
            running += 1
        counts[index + 1] = running
    return counts


def _estimate_prefix(counts: Sequence[int], index: int) -> int:
    """O(1) equivalent of ``estimate_tokens(text[:index])``."""

    cjk_count = counts[index]
    return cjk_count + (index - cjk_count + 3) // 4


def _boundary_cut(text: str, hard: int) -> int:
    """Return the preferred cut index at or below ``hard``.

    Prefers a markdown section start, then a blank-line paragraph break, then a
    line break, then the hard index itself. A candidate must retain at least
    :data:`MIN_BOUNDARY_RETENTION_RATIO` of the fitting prefix, otherwise a
    single stray heading near the start of a document would throw away almost
    the whole budget to land on a pretty boundary.
    """

    floor_index = int(hard * MIN_BOUNDARY_RETENTION_RATIO)
    for patterns in (SECTION_PATTERNS, (PARAGRAPH_PATTERN,), (LINE_PATTERN,)):
        candidate = max(text.rfind(pattern, 0, hard) for pattern in patterns)
        if candidate > 0 and candidate >= floor_index:
            return candidate
    return hard


def _finish_truncation(text: str, cut: int) -> tuple[str, bool]:
    body = text[:cut].rstrip()
    if not body:
        return "", True
    return body + ELLIPSIS, True


def truncate_to_tokens(text: str, max_tokens: int) -> tuple[str, bool]:
    """Truncate ``text`` to ``max_tokens``, returning ``(text, was_truncated)``.

    Text that already fits is returned byte-identical with no ellipsis. Past
    that point the ellipsis is reserved from the budget up front, so the result
    provably satisfies ``estimate_tokens(result) <= max_tokens``.
    """

    if max_tokens <= 0:
        return "", text != ""
    if estimate_tokens(text) <= max_tokens:
        return text, False
    body_budget = max_tokens - estimate_tokens(ELLIPSIS)
    counts = _cjk_prefix_counts(text)
    # Binary search is valid only because estimate_tokens is monotonic over
    # prefixes; see the lemma in its docstring.
    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if _estimate_prefix(counts, mid) <= body_budget:
            low = mid
        else:
            high = mid - 1
    return _finish_truncation(text, _boundary_cut(text, low))


def truncate_to_chars(text: str, max_chars: int) -> tuple[str, bool]:
    """Character-domain twin of :func:`truncate_to_tokens`.

    Used for ``total_max_chars``, which survives as a secondary hard ceiling on
    the summed fragment characters.
    """

    if max_chars <= 0:
        return "", text != ""
    if len(text) <= max_chars:
        return text, False
    return _finish_truncation(text, _boundary_cut(text, max_chars - len(ELLIPSIS)))


def _shortfall_split(
    pool: int,
    kinds: Sequence[str],
    floors: Mapping[str, int],
    measure: Callable[[str, int], int] | None = None,
) -> dict[str, int]:
    """Share ``pool`` between protected ``kinds`` that cannot all reach a floor.

    Every kind is seeded with :data:`MIN_VIABLE_TOKENS` when the pool can seed
    them all, so no protected fragment collapses to the empty string while
    another is delivered nearly whole; the rest is split in proportion to the
    floors, with the rounding remainder handed out in priority order.

    This branch is where boundary slack actually accumulates under the shipped
    policy: *every* kind here is truncated, by construction, so every one of
    them lands below its share. Measured against the real skill corpus a blind
    split burns 17-21% of a small pool. So ``measure`` is applied here too --
    one forward pass in priority order, each kind topped up from the slack its
    predecessors left, capped by its own floor, and never resurrecting a kind
    the split itself could not seed.
    """

    result = {kind: 0 for kind in kinds}
    if pool <= 0 or not kinds:
        return result
    if pool >= MIN_VIABLE_TOKENS * len(kinds):
        for kind in kinds:
            result[kind] = min(floors[kind], MIN_VIABLE_TOKENS)
    remaining = pool - sum(result.values())
    total = sum(floors[kind] for kind in kinds)
    if remaining > 0 and total > 0:
        leftover = remaining
        for kind in kinds:
            share = min(remaining * floors[kind] // total, floors[kind] - result[kind], leftover)
            result[kind] += share
            leftover -= share
        for kind in kinds:
            if leftover <= 0:
                break
            if result[kind] < floors[kind]:
                result[kind] += 1
                leftover -= 1
    if measure is None:
        return result
    slack = 0
    for kind in kinds:
        if result[kind] <= 0:
            continue
        bonus = min(slack, floors[kind] - result[kind])
        result[kind] += bonus
        slack -= bonus
        grant = result[kind]
        slack += grant - min(grant, max(0, int(measure(kind, grant))))
    return result


def allocate(
    pool_tokens: int,
    requests: Sequence[tuple[str, int, int]],
    *,
    protected: Sequence[str] = (),
    drop_below_reserve: Sequence[str] = (),
    measure: Callable[[str, int], int] | None = None,
) -> dict[str, int]:
    """Split ``pool_tokens`` across ``(kind, wanted, reserve)`` requests.

    ``requests`` must already be in priority order, highest first; this
    function never reorders them. Two opt-in roles refine what ``reserve``
    means, mirroring the ladder step by step:

    ``protected``
        Kinds that carry the routing contract. Their floor -- ``min(wanted,
        reserve)`` -- is withheld from every higher-priority kind, so ``base``
        can never eat what ``intent`` needs, and they are never dropped.
    ``drop_below_reserve``
        Kinds that are worthless in fragmentary form. Granted nothing unless
        they can be delivered at their floor, which is what turns "half a
        heading plus a clipped sentence" into a clean, warned omission.

    Anything else is simply funded up to ``wanted`` with whatever is left.
    Once a kind is dropped for lack of budget every lower-priority kind is
    dropped too: the ladder retires whole fragments from the bottom up, so a
    ``project_summary`` must never appear in a payload that had to sacrifice
    ``domain``.

    ``measure``
        Optional ``(kind, grant) -> consumed`` hook that reports what the kind
        will *actually* spend once it is cut to ``grant``. A boundary-preferring
        cut almost always lands below its grant, and without this hook that
        slack was simply burned: lower-priority fragments stayed truncated while
        the pool sat under-used. Callers must pass the same cut the payload will
        use -- for tokens ``estimate_tokens(truncate_to_tokens(text, grant)[0])``
        -- otherwise the reclaim over-spends. The result is clamped into
        ``[0, grant]``, so a buggy hook can never breach the pool.

        Reclaimed slack is kept in a pool of its own, apart from the budget the
        funding decisions are made against, and that separation is the whole
        contract: **slack enlarges a fragment the pool already funded; it never
        funds one the pool could not.** Letting it decide funding put a
        ``project_summary`` the blind pass had cleanly dropped back into the
        payload as ``'# Summar…'`` -- three tokens of heading, sold to the model
        as the project's memory, replacing an honest "dropped" warning. A
        fragment's viability is a property of its own threshold, not of how much
        the fragment above it happened to leave behind.

        Still a single forward pass: slack only ever moves to a *lower*
        priority, never back into the kind that produced it, so there is no
        iteration and no fixed point to converge to. Feeding a kind's own slack
        back to itself is deliberately not done -- it can only be spent by
        rejecting the boundary the cut already chose, trading a clean section
        break for a mid-word one.

    Monotone in the grants, which is what makes the reclaim safe: every kind
    sees the same ``remaining`` it would have seen without ``measure`` plus
    whatever slack reached it, so no grant shrinks, no delivered fragment gets
    worse, and no kind that was funded becomes dropped. Note that a grant is a
    *bound on a cut*, not a disbursement: reusing slack necessarily means the
    grants can sum above ``pool_tokens``. What the pool bounds is consumption --
    ``sum(measure(kind, grant)) <= pool_tokens`` always.

    When the pool cannot even cover the protected floors, the protected kinds
    share it proportionally (see :func:`_shortfall_split`) and everything else
    is dropped. Deterministic: integer arithmetic only, no sorting, no floats.
    """

    allocations: dict[str, int] = {}
    order: list[tuple[str, int, int]] = []
    for kind, wanted, reserve in requests:
        if kind in allocations:
            raise ValueError(f"duplicate prompt fragment kind: {kind}")
        allocations[kind] = 0
        order.append((kind, max(0, int(wanted)), max(0, int(reserve))))
    pool = max(0, pool_tokens)
    floors = {kind: min(wanted, reserve) for kind, wanted, reserve in order}
    protected_kinds = set(protected)
    atomic_kinds = set(drop_below_reserve)
    # Suffix sums of the protected floors: reserved[i] is what must stay unspent
    # at index i so that every protected kind after i can still reach its floor.
    reserved = [0] * (len(order) + 1)
    for index in range(len(order) - 1, -1, -1):
        kind = order[index][0]
        reserved[index] = reserved[index + 1] + (floors[kind] if kind in protected_kinds else 0)
    if pool < reserved[0]:
        protected_order = [kind for kind, _wanted, _reserve in order if kind in protected_kinds]
        allocations.update(_shortfall_split(pool, protected_order, floors, measure))
        return allocations
    remaining = pool
    reclaimed = 0
    for index, (kind, wanted, _reserve) in enumerate(order):
        if wanted <= 0:
            continue
        if kind in protected_kinds or kind in atomic_kinds:
            threshold = floors[kind]
        else:
            threshold = min(wanted, MIN_VIABLE_TOKENS)
        available = remaining - reserved[index + 1]
        if available < threshold:
            break
        # The funding decision reads ``remaining`` only, so it is exactly the
        # decision the blind pass would have made; ``reclaimed`` may then top the
        # grant up towards what this kind wanted.
        grant = min(wanted, available)
        remaining -= grant
        bonus = min(reclaimed, wanted - grant)
        grant += bonus
        reclaimed -= bonus
        allocations[kind] = grant
        if measure is not None:
            reclaimed += grant - min(grant, max(0, int(measure(kind, grant))))
    return allocations


def chars_to_reserve_tokens(max_chars: int) -> int:
    """Convert a legacy ``*_max_chars`` cap into a token reserve.

    ``ceil(max_chars / 4)`` is the identity conversion for the ASCII-dominant
    markdown the legacy caps were authored against, so every author intuition
    baked into 2000/2500/2000/1200 survives unchanged.
    """

    return (max(0, int(max_chars)) + 3) // 4


def resolve_reserves(policy: Mapping[str, Any]) -> dict[str, int]:
    """Resolve the per-kind token reserves for an already-merged policy dict.

    A ``{kind}_reserve_tokens`` field takes precedence when present and not
    ``None``; otherwise the reserve derives from ``{kind}_max_chars``. All four
    kinds are always present in the result so callers can index unconditionally.
    """

    reserves: dict[str, int] = {}
    for kind in RESERVE_KINDS:
        override = policy.get(f"{kind}_reserve_tokens")
        if override is not None:
            reserves[kind] = max(0, int(override))
            continue
        reserves[kind] = chars_to_reserve_tokens(policy[f"{kind}_max_chars"])
    return reserves


def extract_prompt_fragment(
    text: str,
    *,
    source: str | None = None,
) -> tuple[str, str | None]:
    """Return ``(fragment, warning)`` for one prompt source document.

    Every ``<!-- prompt-context:begin -->`` / ``<!-- prompt-context:end -->``
    region contributes its inner text, in document order, joined by a blank
    line. A document with no begin marker yields its whole text unchanged, so
    unmarked sources keep working. An unterminated begin marker extends to the
    end of the document and produces a warning; this never raises.
    """

    if FRAGMENT_BEGIN_MARKER not in text:
        return text, None
    regions: list[str] = []
    warning: str | None = None
    cursor = 0
    while True:
        begin = text.find(FRAGMENT_BEGIN_MARKER, cursor)
        if begin < 0:
            break
        start = begin + len(FRAGMENT_BEGIN_MARKER)
        end = text.find(FRAGMENT_END_MARKER, start)
        if end < 0:
            regions.append(text[start:])
            label = source or "prompt source"
            warning = (
                f"{label} has an unterminated {FRAGMENT_BEGIN_MARKER} marker; "
                "used the remainder of the document"
            )
            break
        regions.append(text[start:end])
        cursor = end + len(FRAGMENT_END_MARKER)
    fragment = FRAGMENT_JOINER.join(region.strip() for region in regions if region.strip())
    return fragment.strip(), warning
