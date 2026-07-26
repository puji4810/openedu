from __future__ import annotations

import random
from pathlib import Path

import pytest

from agent.model_metadata import estimate_tokens_rough
from plugins.study_os.prompt_budget import (
    CJK_RANGES,
    ELLIPSIS,
    FRAGMENT_BEGIN_MARKER,
    FRAGMENT_END_MARKER,
    MIN_BOUNDARY_RETENTION_RATIO,
    MIN_VIABLE_TOKENS,
    _cjk_prefix_counts,
    _estimate_prefix,
    allocate,
    chars_to_reserve_tokens,
    estimate_tokens,
    extract_prompt_fragment,
    resolve_reserves,
    truncate_to_chars,
    truncate_to_tokens,
)
from plugins.study_os.schemas import DEFAULT_PROMPT_POLICY


SKILLS_DIR = Path(__file__).resolve().parents[2] / "plugins" / "study_os" / "skills"
SKILL_FILES = sorted(SKILLS_DIR.glob("*/SKILL.md"))
STUDY_PLAN_SKILL = SKILLS_DIR / "study-plan" / "SKILL.md"


def _skill_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# estimate_tokens
# ---------------------------------------------------------------------------


def test_estimate_tokens_empty_string_returns_zero():
    assert estimate_tokens("") == 0
    assert estimate_tokens("   ") > 0


def test_estimate_tokens_pure_ascii_matches_hermes_rough():
    for length in range(0, 41):
        text = "abcd efgh"[: length % 9] + "x" * (length - min(length, 9))
        text = text[:length].ljust(length, "y")
        assert estimate_tokens(text) == estimate_tokens_rough(text), text


def test_estimate_tokens_pure_cjk_is_one_per_codepoint():
    assert estimate_tokens("考研数学高等数学") == 8


def test_estimate_tokens_mixed_script_arithmetic():
    text = "复习 review 错题"

    assert estimate_tokens(text) == 6
    assert estimate_tokens_rough(text) == 3


def test_estimate_tokens_counts_every_declared_cjk_range():
    representatives = (
        0x1100,
        0x3002,
        0x3042,
        0x30A2,
        0x3131,
        0x3400,
        0x4E00,
        0xAC00,
        0xF900,
        0xFF0C,
    )

    assert len(representatives) == len(CJK_RANGES)
    for codepoint in representatives:
        assert estimate_tokens(chr(codepoint)) == 1, hex(codepoint)


def test_estimate_tokens_excludes_western_punctuation():
    assert estimate_tokens("‘’“”") == 1
    assert estimate_tokens("…–—…") == 1


def test_estimate_tokens_ellipsis_costs_one_token():
    assert estimate_tokens(ELLIPSIS) == 1


def test_estimate_tokens_is_monotonic_over_prefixes():
    text = _skill_text(STUDY_PLAN_SKILL)

    previous = 0
    for index in range(len(text) + 1):
        current = estimate_tokens(text[:index])
        assert current >= previous
        previous = current


def test_estimate_tokens_is_subadditive():
    alphabet = "ab 考研\n研究,x"
    rng = random.Random(20260726)
    for _ in range(200):
        left = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 30)))
        right = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 30)))
        assert estimate_tokens(left + right) <= estimate_tokens(left) + estimate_tokens(right)


@pytest.mark.parametrize("path", SKILL_FILES, ids=[path.parent.name for path in SKILL_FILES])
def test_cjk_prefix_counts_matches_full_estimator(path: Path):
    text = _skill_text(path)
    counts = _cjk_prefix_counts(text)

    assert len(counts) == len(text) + 1
    assert counts[0] == 0
    for index in range(0, len(text) + 1, 7):
        assert _estimate_prefix(counts, index) == estimate_tokens(text[:index])
    assert _estimate_prefix(counts, len(text)) == estimate_tokens(text)


# ---------------------------------------------------------------------------
# truncate_to_tokens
# ---------------------------------------------------------------------------


def test_truncate_returns_input_unchanged_when_it_fits():
    text = "## A\nalpha\n\n## B\nbeta"
    tokens = estimate_tokens(text)

    for budget in (tokens, tokens + 1):
        assert truncate_to_tokens(text, budget) == (text, False)


def test_truncate_preserves_trailing_boundary_when_text_fits():
    assert truncate_to_tokens("a\n\n", 50) == ("a\n\n", False)


def test_truncate_zero_and_negative_budget():
    assert truncate_to_tokens("text", 0) == ("", True)
    assert truncate_to_tokens("text", -5) == ("", True)
    assert truncate_to_tokens("", 0) == ("", False)
    assert truncate_to_tokens("", -5) == ("", False)


@pytest.mark.parametrize("path", SKILL_FILES, ids=[path.parent.name for path in SKILL_FILES])
def test_truncate_result_never_exceeds_budget(path: Path):
    text = _skill_text(path)

    for budget in range(0, 700, 13):
        result, _truncated = truncate_to_tokens(text, budget)
        assert estimate_tokens(result) <= budget, (budget, len(result))


def test_truncate_prefers_section_boundary():
    text = "## A\n" + "alpha " * 30 + "\n## B\n" + "beta " * 30 + "\n## C\n" + "gamma " * 60
    budget = estimate_tokens(text) - 40

    result, truncated = truncate_to_tokens(text, budget)

    assert truncated is True
    assert result.endswith(ELLIPSIS)
    assert "## C" not in result
    assert "beta" in result


def test_truncate_section_tier_takes_later_of_h2_and_h3():
    text = "## A\n" + "alpha " * 20 + "\n### B\n" + "beta " * 20 + "\n" + "gamma " * 40
    budget = estimate_tokens(text) - 30

    result, truncated = truncate_to_tokens(text, budget)

    assert truncated is True
    assert "### B" in result
    assert "gamma" not in result


def test_truncate_falls_back_to_paragraph_then_line():
    paragraph_text = "## S\n" + "alpha " * 20 + "\n\n" + "beta " * 20
    result, truncated = truncate_to_tokens(paragraph_text, estimate_tokens(paragraph_text) - 10)
    assert truncated is True
    assert result.endswith("alpha" + ELLIPSIS)

    line_text = "intro line\n" + "\n".join("body line %02d" % index for index in range(40))
    result, truncated = truncate_to_tokens(line_text, estimate_tokens(line_text) - 5)
    assert truncated is True
    assert result.endswith(ELLIPSIS)
    assert result[: -len(ELLIPSIS)].endswith(("0", "1", "2", "3", "4", "5", "6", "7", "8", "9"))


def test_truncate_retention_floor_rejects_degenerate_boundary():
    text = "# T\n## S\n" + "x" * 4000
    budget = 500

    result, truncated = truncate_to_tokens(text, budget)

    assert truncated is True
    body = result[: -len(ELLIPSIS)]
    assert len(body) > 4 * MIN_BOUNDARY_RETENTION_RATIO * 100
    assert body.startswith("# T\n## S\nxxx")


def test_truncate_single_unbroken_line_hard_cuts():
    result, truncated = truncate_to_tokens("x" * 10000, 100)

    assert truncated is True
    assert result.endswith(ELLIPSIS)
    assert estimate_tokens(result) == 100


def test_truncate_empty_body_returns_empty_not_bare_ellipsis():
    assert truncate_to_tokens("some text that will not fit", 1) == ("", True)


def test_truncate_appends_exactly_one_ellipsis():
    text = "alpha " * 200 + ELLIPSIS

    result, truncated = truncate_to_tokens(text, 50)

    assert truncated is True
    assert result.count(ELLIPSIS) == 1
    assert result.endswith(ELLIPSIS)


def test_truncate_rstrips_before_ellipsis():
    text = "word   　\n\n" + "filler " * 200

    result, _truncated = truncate_to_tokens(text, 2)

    assert result == "word" + ELLIPSIS


def test_truncate_is_idempotent_at_the_same_budget():
    text = _skill_text(STUDY_PLAN_SKILL)

    once, first_truncated = truncate_to_tokens(text, 120)
    twice, second_truncated = truncate_to_tokens(once, 120)

    assert first_truncated is True
    assert second_truncated is False
    assert twice == once


def test_truncate_to_tokens_never_looks_past_four_times_its_budget():
    """The lemma the reader's bounded scan of prompt_summary.md rests on.

    ``estimate_tokens`` charges four non-CJK characters one token and each CJK
    character one, so a prefix of more than ``4 * budget`` characters cannot fit
    -- both the binary search and the boundary scan stay inside ``4 * budget + 4``
    characters, and reading only that far is identical to reading the file.
    """

    corpus = (
        "progress note " * 8000,
        "复习进度记录。" * 8000,
        "z" * 100000,
        "".join("\n## S%d\n" % index + "alpha " * 20 for index in range(800)),
        ("复习 progress 记录 note\n\n" * 4000),
    )
    for budget in (0, 1, 2, 3, 40, 300, 1800):
        for text in corpus:
            assert truncate_to_tokens(text, budget) == truncate_to_tokens(
                text[: 4 * budget + 4], budget
            ), (budget, text[:20])


def test_truncate_to_chars_mirrors_token_variant():
    text = "z" * 1200

    assert truncate_to_chars(text, 1200) == (text, False)
    clipped, truncated = truncate_to_chars(text, 399)
    assert truncated is True
    assert len(clipped) == 399
    assert clipped.endswith(ELLIPSIS)
    assert truncate_to_chars(text, 0) == ("", True)
    assert truncate_to_chars("", 0) == ("", False)


# ---------------------------------------------------------------------------
# allocate
# ---------------------------------------------------------------------------


def test_allocate_empty_requests_returns_empty_dict():
    assert allocate(1800, []) == {}


def test_allocate_grants_everything_when_pool_is_ample():
    requests = [("base", 100, 500), ("intent", 700, 625), ("domain", 50, 500)]

    result = allocate(10_000, requests)

    assert result == {"base": 100, "intent": 700, "domain": 50}


def test_allocate_two_pass_redistributes_surplus_by_priority():
    requests = [
        ("base", 305, 500),
        ("intent", 625, 625),
        ("domain", 371, 500),
        ("project_summary", 2400, 300),
    ]

    result = allocate(1800, requests)

    assert result == {"base": 305, "intent": 625, "domain": 371, "project_summary": 499}
    assert sum(result.values()) == 1800


def test_allocate_pool_smaller_than_demand_starves_lowest_priority():
    requests = [
        ("base", 2000, 500),
        ("intent", 2000, 625),
        ("domain", 2000, 500),
        ("project_summary", 2000, 300),
    ]

    result = allocate(900, requests)

    # Nothing is protected here, so the ladder retires fragments from the
    # bottom up until the highest-priority one is whole or the pool is gone.
    assert result == {"base": 900, "intent": 0, "domain": 0, "project_summary": 0}
    assert sum(result.values()) == 900


def test_allocate_protects_the_routing_pair_from_a_greedy_base():
    requests = [("base", 2000, 500), ("intent", 2000, 625), ("domain", 2000, 500)]

    result = allocate(2000, requests, protected=("base", "intent"))

    # base may spend everything except the floor intent is owed.
    assert result == {"base": 1375, "intent": 625, "domain": 0}


def test_allocate_shortfall_splits_protected_kinds_proportionally():
    requests = [("base", 232, 500), ("intent", 315, 625), ("domain", 214, 500)]

    result = allocate(233, requests, protected=("base", "intent"))

    # 233 cannot hold both floors (232 + 315), so neither is starved to zero:
    # each is seeded with MIN_VIABLE_TOKENS and the rest splits by floor.
    assert result["base"] > 0
    assert result["intent"] > 0
    assert result["domain"] == 0
    assert sum(result.values()) == 233
    assert result["intent"] > result["base"]


@pytest.mark.parametrize("pool", [4, 5, 20, 100, 232, 233, 546, 547, 1800])
def test_allocate_never_zeroes_a_protected_kind(pool: int):
    requests = [("base", 232, 500), ("intent", 315, 625), ("domain", 214, 500)]

    result = allocate(pool, requests, protected=("base", "intent"))

    for kind in ("base", "intent"):
        assert result[kind] >= MIN_VIABLE_TOKENS, (pool, result)
    assert sum(result.values()) <= pool


def test_allocate_drops_a_below_reserve_kind_instead_of_stubbing_it():
    requests = [("base", 232, 500), ("intent", 315, 625), ("domain", 214, 500)]

    stubbed = allocate(560, requests, protected=("base", "intent"))
    whole = allocate(761, requests, protected=("base", "intent"), drop_below_reserve=("domain",))
    dropped = allocate(760, requests, protected=("base", "intent"), drop_below_reserve=("domain",))

    # Without the role, 13 leftover tokens become a useless domain stub.
    assert stubbed["domain"] == 13
    assert dropped["domain"] == 0
    assert whole["domain"] == 214


def test_allocate_never_delivers_below_a_kind_that_was_dropped():
    requests = [
        ("base", 1000, 500),
        ("intent", 750, 625),
        ("domain", 214, 500),
        ("project_summary", 300, 300),
    ]

    result = allocate(1800, requests, protected=("base", "intent"), drop_below_reserve=("domain",))

    # base and intent stay whole; domain cannot reach its reserve, and
    # project_summary must not sneak into the 50 tokens domain gave up.
    assert result == {"base": 1000, "intent": 750, "domain": 0, "project_summary": 0}


def test_allocate_first_request_reserve_exceeds_pool():
    requests = [("base", 900, 500), ("intent", 900, 625)]

    result = allocate(300, requests)

    assert result == {"base": 300, "intent": 0}


def test_allocate_request_wanting_zero_consumes_nothing():
    requests = [("base", 0, 500), ("intent", 400, 625)]

    result = allocate(500, requests)

    assert result == {"base": 0, "intent": 400}


def test_allocate_never_exceeds_pool_property():
    rng = random.Random(99)
    kinds = ("base", "intent", "domain", "project_summary")
    for _ in range(300):
        count = rng.randint(0, 4)
        requests = [
            (kinds[index], rng.randint(0, 3000), rng.randint(0, 800))
            for index in range(count)
        ]
        pool = rng.randint(-50, 2500)

        result = allocate(pool, requests)

        assert sum(result.values()) <= max(0, pool)
        assert set(result) == {kind for kind, _, _ in requests}
        for kind, wanted, _reserve in requests:
            assert 0 <= result[kind] <= max(0, wanted)


def test_allocate_honors_protected_floors_property():
    rng = random.Random(7)
    kinds = ("base", "intent", "domain", "project_summary")
    protected = ("base", "intent")
    for _ in range(300):
        requests = [
            (kind, rng.randint(0, 900), rng.randint(0, 700)) for kind in kinds
        ]
        protected_floors = sum(
            min(wanted, reserve) for kind, wanted, reserve in requests if kind in protected
        )
        pool = protected_floors + rng.randint(0, 500)

        result = allocate(pool, requests, protected=protected)

        # A protected kind always reaches its floor once the pool covers the
        # protected floors, however greedy the kinds above it are.
        for kind, wanted, reserve in requests:
            if kind in protected:
                assert result[kind] >= min(wanted, reserve), (pool, requests, result)


def test_allocate_ladder_retires_fragments_bottom_up_property():
    rng = random.Random(4242)
    kinds = ("base", "intent", "domain", "project_summary")
    for _ in range(300):
        requests = [(kind, rng.randint(1, 900), rng.randint(1, 700)) for kind in kinds]
        pool = rng.randint(0, 2500)

        result = allocate(
            pool,
            requests,
            protected=("base", "intent"),
            drop_below_reserve=("domain",),
        )

        granted = [result[kind] for kind in kinds]
        dropped = [index for index, grant in enumerate(granted) if grant == 0]
        if dropped:
            # Every kind below the first drop is dropped too.
            assert granted[dropped[0] :] == [0] * (len(kinds) - dropped[0]), (
                pool,
                requests,
                result,
            )
        assert sum(granted) <= pool


def test_allocate_negative_pool_clamps_to_zero():
    result = allocate(-100, [("base", 500, 500), ("intent", 500, 500)])

    assert result == {"base": 0, "intent": 0}


def test_allocate_clamps_negative_wanted_and_reserve():
    result = allocate(1000, [("base", -400, 500), ("intent", 300, -100)])

    assert result == {"base": 0, "intent": 300}


def test_allocate_duplicate_kind_raises_value_error():
    with pytest.raises(ValueError):
        allocate(1000, [("base", 100, 100), ("base", 100, 100)])


def test_allocate_is_deterministic_and_order_preserving():
    requests = [("base", 10, 900), ("intent", 900, 10), ("domain", 400, 400)]

    first = allocate(1000, requests)
    second = allocate(1000, requests)

    assert first == second
    assert list(first) == ["base", "intent", "domain"]


# ---------------------------------------------------------------------------
# allocate: slack reclaim
# ---------------------------------------------------------------------------


def _token_measure(texts: dict[str, str]):
    """The exact cost hook the prompt loader passes to :func:`allocate`."""

    return lambda kind, grant: estimate_tokens(truncate_to_tokens(texts[kind], grant)[0])


def test_allocate_without_measure_burns_the_boundary_slack():
    texts = {"base": "## A\n" + "alpha " * 30 + "\n## B\n" + "beta " * 400, "intent": "x" * 4000}
    # base wants more than the pool, so it is capped at 60 -- but the cut lands
    # on the "## B" heading and spends only 47 of those 60 tokens.
    requests = [("base", 60, 50), ("intent", 400, 50)]

    blind = allocate(100, requests)
    reclaimed = allocate(100, requests, measure=_token_measure(texts))

    assert estimate_tokens(truncate_to_tokens(texts["base"], 60)[0]) == 47
    assert blind == {"base": 60, "intent": 40}
    assert reclaimed == {"base": 60, "intent": 53}


def test_allocate_reclaim_never_exceeds_the_pool_in_real_consumption():
    texts = {
        "base": "## A\n" + "alpha " * 40 + "\n## B\n" + "beta " * 40,
        "intent": "## C\n" + "gamma " * 60 + "\n## D\n" + "delta " * 60,
        "project_summary": "考研" * 800,
    }
    requests = [
        ("base", estimate_tokens(texts["base"]), 500),
        ("intent", estimate_tokens(texts["intent"]), 625),
        ("project_summary", estimate_tokens(texts["project_summary"]), 300),
    ]

    for pool in range(0, 600, 7):
        result = allocate(
            pool,
            requests,
            protected=("base", "intent"),
            measure=_token_measure(texts),
        )
        spent = sum(
            estimate_tokens(truncate_to_tokens(texts[kind], grant)[0])
            for kind, grant in result.items()
        )
        assert spent <= pool, (pool, result)


def test_allocate_reclaim_never_resurrects_a_fragment_the_blind_pass_dropped():
    """Slack enlarges a funded fragment; it must not mint an unfunded stub.

    project_summary's viability threshold is MIN_VIABLE_TOKENS, which only asks
    "can this produce a non-empty string". Letting reclaimed slack answer the
    funding question therefore delivered ``'# Summar…'`` as the project's
    memory, and replaced an honest "dropped" warning with "truncated to 3
    tokens" -- the degradation this module exists to refuse.
    """

    texts = {
        "base": "".join("\n## S%d\n" % index + "alpha " * 20 for index in range(60)),
        "intent": "## C\n" + "gamma " * 30,
        "domain": "## E\ndomain rules",
        "project_summary": "# Summary\n\n" + "progress note " * 400,
    }
    requests = [
        (kind, estimate_tokens(text), reserve)
        for (kind, text), reserve in zip(texts.items(), (500, 625, 500, 2000))
    ]

    blind = allocate(547, requests, protected=("base", "intent"), drop_below_reserve=("domain",))
    reclaimed = allocate(
        547,
        requests,
        protected=("base", "intent"),
        drop_below_reserve=("domain",),
        measure=_token_measure(texts),
    )

    # base is granted 500 either way and spends only 478 of them, but those 22
    # tokens cannot buy what the pool itself could not fund: domain would land
    # under its reserve, project_summary at a clipped heading.
    assert blind["base"] == reclaimed["base"] == 500
    assert blind["domain"] == blind["project_summary"] == 0
    assert reclaimed == blind


def test_allocate_reclaim_flows_through_the_shortfall_split():
    """The low-pool branch is where slack accumulates: every kind is cut there."""

    texts = {
        "base": "".join("\n## S%d\n" % index + "alpha " * 20 for index in range(60)),
        "intent": "".join("\n## T%d\n" % index + "gamma " * 20 for index in range(60)),
    }
    requests = [
        (kind, estimate_tokens(text), reserve)
        for (kind, text), reserve in zip(texts.items(), (500, 625))
    ]
    measure = _token_measure(texts)

    for pool in range(4, 600, 7):
        blind = allocate(pool, requests, protected=("base", "intent"))
        reclaimed = allocate(pool, requests, protected=("base", "intent"), measure=measure)

        assert sum(measure(kind, grant) for kind, grant in reclaimed.items()) <= pool, pool
        assert reclaimed["base"] == blind["base"], pool
        assert reclaimed["intent"] >= blind["intent"], pool

    # base's share stops at the last "## S" heading below it, and every token it
    # leaves there reaches intent instead of being burned.
    assert allocate(599, requests, protected=("base", "intent")) == {"base": 267, "intent": 332}
    assert allocate(599, requests, protected=("base", "intent"), measure=measure) == {
        "base": 267,
        "intent": 345,
    }


def test_shortfall_reclaim_never_seeds_a_kind_the_split_left_at_zero():
    """Below MIN_VIABLE_TOKENS per kind the pool seeds nobody it cannot seed."""

    texts = {"base": "## A\n" + "alpha " * 40, "intent": "## C\n" + "gamma " * 40}
    requests = [("base", estimate_tokens(texts["base"]), 500), ("intent", 400, 625)]

    reclaimed = allocate(1, requests, protected=("base", "intent"), measure=_token_measure(texts))

    assert reclaimed == allocate(1, requests, protected=("base", "intent")) == {
        "base": 1,
        "intent": 0,
    }


def test_allocate_reclaim_clamps_a_measure_outside_its_grant():
    requests = [("base", 400, 100), ("intent", 400, 100)]

    # Clamped to the grant, so an over-reporting hook cannot drive the pool
    # negative and starve everything below it.
    assert allocate(500, requests, measure=lambda kind, grant: grant * 10) == allocate(500, requests)
    # Clamped at zero, so an under-reporting hook cannot mint budget beyond the
    # pool: every kind is still capped at what it wanted.
    assert allocate(500, requests, measure=lambda kind, grant: -50) == {"base": 400, "intent": 400}


def test_allocate_reclaim_is_deterministic_and_matches_the_blind_pass_when_exact():
    requests = [("base", 300, 500), ("intent", 400, 625), ("domain", 200, 500)]

    exact = allocate(1800, requests, measure=lambda kind, grant: grant)

    assert exact == allocate(1800, requests)
    assert exact == allocate(1800, requests, measure=lambda kind, grant: grant)


def test_allocate_reclaim_never_regresses_a_grant_property():
    """Slack only ever flows downhill, so no kind can lose budget by it."""

    rng = random.Random(20260727)
    kinds = ("base", "intent", "domain", "project_summary")
    for _ in range(300):
        requests = [(kind, rng.randint(1, 900), rng.randint(1, 700)) for kind in kinds]
        slack = {kind: rng.randint(0, 30) for kind in kinds}
        pool = rng.randint(0, 2500)

        blind = allocate(
            pool, requests, protected=("base", "intent"), drop_below_reserve=("domain",)
        )
        reclaimed = allocate(
            pool,
            requests,
            protected=("base", "intent"),
            drop_below_reserve=("domain",),
            measure=lambda kind, grant: max(0, grant - slack[kind]),
        )

        granted = [reclaimed[kind] for kind in kinds]
        dropped = [index for index, grant in enumerate(granted) if grant == 0]
        for kind, wanted, _reserve in requests:
            assert reclaimed[kind] >= blind[kind], (pool, requests, blind, reclaimed)
            assert reclaimed[kind] <= wanted
        if dropped:
            assert granted[dropped[0] :] == [0] * (len(kinds) - dropped[0])
        assert sum(max(0, grant - slack[kind]) for kind, grant in reclaimed.items()) <= pool


def test_allocate_reclaim_holds_protected_floors_against_the_real_skill_corpus():
    from plugins.study_os.domain_packs import domain_pack_for
    from plugins.study_os.tools import _INTENT_SKILL, _skill_path

    reserves = resolve_reserves(DEFAULT_PROMPT_POLICY)
    summary = "复习进度：" + "考研数学线性代数概率论" * 200
    for intent, intent_skill in _INTENT_SKILL.items():
        for pack_id in ("kaoyan.v1", "engineering.v1", "research.v1", "general.v1"):
            texts = {}
            for kind, skill in (("base", "study-os"), ("intent", intent_skill)):
                texts[kind], _warning = extract_prompt_fragment(
                    _skill_path(skill).read_text(encoding="utf-8")
                )
            domain_skill = domain_pack_for(pack_id).prompt_skill
            if domain_skill:
                texts["domain"], _warning = extract_prompt_fragment(
                    _skill_path(domain_skill).read_text(encoding="utf-8")
                )
            texts["project_summary"] = summary
            requests = [
                (kind, estimate_tokens(text), reserves[kind]) for kind, text in texts.items()
            ]
            measure = _token_measure(texts)

            for pool in (1800, 900, 700, 600, 450, 300, 200, 100, 20, 1):
                label = f"{intent}/{pack_id}/{pool}"
                blind = allocate(
                    pool,
                    requests,
                    protected=("base", "intent"),
                    drop_below_reserve=("domain",),
                )
                result = allocate(
                    pool,
                    requests,
                    protected=("base", "intent"),
                    drop_below_reserve=("domain",),
                    measure=measure,
                )

                spent = sum(measure(kind, grant) for kind, grant in result.items())
                assert spent <= pool, label
                for kind in texts:
                    assert result[kind] >= blind[kind], label
                granted = [result[kind] for kind in texts]
                dropped = [index for index, grant in enumerate(granted) if grant == 0]
                if dropped:
                    assert granted[dropped[0] :] == [0] * (len(texts) - dropped[0]), label
                if pool >= sum(min(estimate_tokens(texts[kind]), reserves[kind]) for kind in ("base", "intent")):
                    for kind in ("base", "intent"):
                        assert result[kind] >= min(estimate_tokens(texts[kind]), reserves[kind]), label


# ---------------------------------------------------------------------------
# legacy char -> token reserve conversion
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("chars", "expected"),
    [(2000, 500), (2500, 625), (1200, 300), (1, 1), (0, 0), (5, 2)],
)
def test_chars_to_reserve_tokens_exact_values(chars: int, expected: int):
    assert chars_to_reserve_tokens(chars) == expected


def test_chars_to_reserve_tokens_matches_estimator_on_ascii():
    for length in range(0, 101):
        assert chars_to_reserve_tokens(length) == estimate_tokens("a" * length)


def test_resolve_reserves_derives_all_four_from_default_policy():
    reserves = resolve_reserves(DEFAULT_PROMPT_POLICY)

    assert reserves == {"base": 500, "intent": 625, "domain": 500, "project_summary": 300}
    assert sum(reserves.values()) == 1925


def test_resolve_reserves_override_takes_precedence():
    policy = {**DEFAULT_PROMPT_POLICY, "domain_reserve_tokens": 120}

    reserves = resolve_reserves(policy)

    assert reserves["domain"] == 120
    assert reserves["base"] == 500
    assert reserves["intent"] == 625
    assert reserves["project_summary"] == 300


def test_resolve_reserves_ignores_none_override():
    policy = {**DEFAULT_PROMPT_POLICY, "domain_reserve_tokens": None}

    assert resolve_reserves(policy)["domain"] == 500


def test_resolve_reserves_returns_all_four_kinds_always():
    policy = {
        "base_max_chars": 10,
        "intent_max_chars": 10,
        "domain_max_chars": 10,
        "project_summary_max_chars": 10,
    }

    assert set(resolve_reserves(policy)) == {
        "base",
        "intent",
        "domain",
        "project_summary",
    }


# ---------------------------------------------------------------------------
# extract_prompt_fragment
# ---------------------------------------------------------------------------


def test_extract_prompt_fragment_falls_back_to_whole_document():
    text = "---\nname: study-os\n---\n\n# Title\n\nbody\n"

    assert extract_prompt_fragment(text) == (text, None)


def test_extract_prompt_fragment_returns_only_marked_region():
    text = (
        "---\nname: study-os\n---\n\n"
        "Prose that must not reach the model.\n\n"
        f"{FRAGMENT_BEGIN_MARKER}\n## Route\nrouting table\n{FRAGMENT_END_MARKER}\n\n"
        "More free prose.\n"
    )

    fragment, warning = extract_prompt_fragment(text)

    assert fragment == "## Route\nrouting table"
    assert warning is None
    assert "Prose that must not reach" not in fragment
    assert "name: study-os" not in fragment


def test_extract_prompt_fragment_joins_multiple_regions_in_document_order():
    text = (
        f"a{FRAGMENT_BEGIN_MARKER}first{FRAGMENT_END_MARKER}"
        f"b{FRAGMENT_BEGIN_MARKER}second{FRAGMENT_END_MARKER}c"
    )

    assert extract_prompt_fragment(text) == ("first\n\nsecond", None)


def test_extract_prompt_fragment_warns_on_unterminated_marker():
    text = f"intro\n{FRAGMENT_BEGIN_MARKER}\ntail content\n"

    fragment, warning = extract_prompt_fragment(text, source="study-os/SKILL.md")

    assert fragment == "tail content"
    assert warning is not None
    assert "study-os/SKILL.md" in warning
    assert "unterminated" in warning


def test_extract_prompt_fragment_skips_empty_regions():
    text = f"x{FRAGMENT_BEGIN_MARKER}\n \n{FRAGMENT_END_MARKER}y"

    assert extract_prompt_fragment(text) == ("", None)


def test_extract_prompt_fragment_never_includes_frontmatter_when_marked():
    for path in SKILL_FILES:
        text = _skill_text(path)
        if FRAGMENT_BEGIN_MARKER not in text:
            continue
        fragment, warning = extract_prompt_fragment(text, source=path.parent.name)

        assert warning is None, path
        assert fragment, path
        assert text.startswith("---"), path
        assert text.index(FRAGMENT_BEGIN_MARKER) > text.index("\n---", 3), path
        assert "name: " not in fragment.splitlines()[0], path


def test_default_pool_covers_every_real_intent_domain_combination():
    from plugins.study_os.domain_packs import domain_pack_for
    from plugins.study_os.tools import _INTENT_SKILL, _skill_path

    pool = int(DEFAULT_PROMPT_POLICY["total_max_tokens"])
    reserves = resolve_reserves(DEFAULT_PROMPT_POLICY)

    for intent, intent_skill in _INTENT_SKILL.items():
        for pack_id in ("kaoyan.v1", "engineering.v1", "research.v1", "general.v1"):
            requests = []
            for kind, skill in (("base", "study-os"), ("intent", intent_skill)):
                fragment, _warning = extract_prompt_fragment(
                    _skill_path(skill).read_text(encoding="utf-8")
                )
                requests.append((kind, estimate_tokens(fragment), reserves[kind]))
            domain_skill = domain_pack_for(pack_id).prompt_skill
            if domain_skill:
                fragment, _warning = extract_prompt_fragment(
                    _skill_path(domain_skill).read_text(encoding="utf-8")
                )
                requests.append(("domain", estimate_tokens(fragment), reserves["domain"]))
            requests.append(("project_summary", 10_000, reserves["project_summary"]))

            granted = allocate(pool, requests)

            label = f"{intent}/{pack_id}"
            for kind, wanted, _reserve in requests[:-1]:
                assert granted[kind] == wanted, label
            assert granted["project_summary"] >= reserves["project_summary"], label
