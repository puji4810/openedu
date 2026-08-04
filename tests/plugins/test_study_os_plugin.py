from __future__ import annotations

import json
from pathlib import Path

import pytest

from plugins.study_os.prompt_budget import FRAGMENT_BEGIN_MARKER, FRAGMENT_END_MARKER


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / "OS" / "Box").mkdir(parents=True)
    (root / "OS" / "examples").mkdir(parents=True)
    (root / "Math" / "Box" / "题型").mkdir(parents=True)
    (root / "OS" / "Box" / "进程创建.md").write_text(
        """---
type: concept
aliases:
  - 作业接纳
tags:
  - OS
  - 进程与线程
---
# 进程创建

进程创建会建立 [[进程控制块]]，并进入就绪队列。
""",
        encoding="utf-8",
    )
    (root / "OS" / "examples" / "OS-0043.md").write_text(
        """---
type: example
id: OS-0043
difficulty: 2
review_level: 2
status: 可复习
tags:
  - OS
patterns:
  - "[[题型：高级调度与进程接纳辨析]]"
concepts:
  - "[[处理机调度层次]]"
  - "[[进程创建]]"
---
# OS-0043 进程从创建态转为就绪态

## 题型特征
核心是区分高级调度和低级调度。
""",
        encoding="utf-8",
    )
    (root / "Math" / "Box" / "题型" / "题型：泰勒展开.md").write_text(
        """---
type: pattern
tags: [数学, 极限]
concepts: ["[[泰勒展开]]"]
---
# 题型：泰勒展开

看到无穷小阶数匹配时考虑 [[泰勒公式]]。
""",
        encoding="utf-8",
    )
    return root


def _loads(result: str) -> dict:
    return json.loads(result)


def test_due_reviews_discovers_examples_in_subject_folders(vault: Path):
    from plugins.study_os.tools import handle_study_due_reviews

    math_example = vault / "Math" / "examples" / "limit.md"
    math_example.parent.mkdir(parents=True)
    math_example.write_text(
        "---\ntype: example\nreview_level: 0\n---\n# 跨课程优先项\n",
        encoding="utf-8",
    )
    result = _loads(handle_study_due_reviews({"vault_path": str(vault), "limit": 1}))

    assert result["ok"] is True
    assert result["data"]["count"] == 1
    assert result["data"]["due"][0]["path"] == "Math/examples/limit.md"
    assert result["data"]["due"][0]["subject"] == "Math"
    assert result["data"]["subjects"] == ["Math", "OS"]


def test_due_reviews_supports_explicit_and_composable_review_selectors(vault: Path):
    from plugins.study_os.tools import handle_study_due_reviews

    selected = vault / "Math" / "examples" / "limit.md"
    selected.parent.mkdir(parents=True)
    selected.write_text(
        "---\n"
        "type: example\n"
        "difficulty: hard\n"
        "review_level: 4\n"
        "review_count: 3\n"
        "next_review_at: 2099-01-01\n"
        "tags: [math, calculus]\n"
        "concepts: [Taylor expansion]\n"
        "---\n# Taylor drill\n",
        encoding="utf-8",
    )
    result = _loads(
        handle_study_due_reviews(
            {
                "vault_path": str(vault),
                "notes": ["Math/examples/limit.md"],
                "tags": ["math", "calculus"],
                "concepts": ["taylor"],
                "difficulties": ["hard"],
                "min_review_level": 3,
                "review_state": "all",
                "match": "all",
                "sort": "title",
            }
        )
    )

    assert result["ok"] is True
    assert [item["path"] for item in result["data"]["due"]] == ["Math/examples/limit.md"]
    assert result["data"]["selection"] == {
        "review_state": "all",
        "sort": "title",
        "match": "all",
        "limit": 30,
        "notes": ["math/examples/limit.md"],
        "tags": ["calculus", "math"],
        "concepts": ["taylor"],
        "difficulties": ["hard"],
        "min_review_level": 3,
    }


def test_due_reviews_default_scope_remains_due_only(vault: Path):
    from plugins.study_os.tools import handle_study_due_reviews

    future = vault / "OS" / "examples" / "future.md"
    future.write_text(
        "---\ntype: example\nreview_level: 0\nnext_review_at: 2099-01-01\n---\n# Not due\n",
        encoding="utf-8",
    )

    default_result = _loads(handle_study_due_reviews({"vault_path": str(vault)}))
    all_result = _loads(handle_study_due_reviews({"vault_path": str(vault), "review_state": "all"}))

    assert "OS/examples/future.md" not in {item["path"] for item in default_result["data"]["due"]}
    assert "OS/examples/future.md" in {item["path"] for item in all_result["data"]["due"]}


def test_due_reviews_excludes_hidden_tool_directories(vault: Path):
    from plugins.study_os.tools import handle_study_due_reviews

    accidental = vault / ".opencode" / "skills" / "plugin" / "examples" / "README.md"
    accidental.parent.mkdir(parents=True)
    accidental.write_text("# Tool documentation\n", encoding="utf-8")

    result = _loads(
        handle_study_due_reviews(
            {"vault_path": str(vault), "review_state": "all", "limit": 500}
        )
    )

    assert result["ok"] is True
    assert not any(item["path"].startswith(".opencode/") for item in result["data"]["due"])


def test_due_reviews_filters_yaml_tags_limits_and_excluded_paths(vault: Path):
    from plugins.study_os.tools import handle_study_due_reviews

    examples = {
        "Archive/examples/old.md": ("A archived", ["math", "calculus"]),
        "Math/examples/keep-a.md": ("B keep", ["math", "calculus"]),
        "Math/examples/keep-b.md": ("C keep", ["calculus"]),
        "Math/examples/other.md": ("D other", ["linear-algebra"]),
    }
    for relative, (title, tags) in examples.items():
        path = vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "---\n"
            "type: example\n"
            f"title: {title}\n"
            f"tags: [{', '.join(tags)}]\n"
            "review_level: 0\n"
            "---\n# Review item\n",
            encoding="utf-8",
        )

    result = _loads(
        handle_study_due_reviews(
            {
                "vault_path": str(vault),
                "tags": ["calculus"],
                "exclude_paths": ["Archive"],
                "review_state": "all",
                "sort": "title",
                "limit": 1,
            }
        )
    )

    assert result["ok"] is True
    assert result["data"]["count"] == 1
    assert result["data"]["available_count"] == 2
    assert [item["path"] for item in result["data"]["due"]] == ["Math/examples/keep-a.md"]
    assert result["data"]["selection"] == {
        "review_state": "all",
        "sort": "title",
        "match": "any",
        "tags": ["calculus"],
        "exclude_paths": ["archive"],
        "limit": 1,
    }

    shortfall = _loads(
        handle_study_due_reviews(
            {
                "vault_path": str(vault),
                "tags": ["calculus"],
                "exclude_paths": ["Archive"],
                "review_state": "all",
                "sort": "title",
                "limit": 3,
            }
        )
    )
    assert shortfall["data"]["count"] == 2
    assert shortfall["data"]["available_count"] == 2
    assert [item["path"] for item in shortfall["data"]["due"]] == [
        "Math/examples/keep-a.md",
        "Math/examples/keep-b.md",
    ]


def test_review_submit_records_one_atomic_attempt_and_spacing_update(vault: Path):
    from plugins.study_os.learning import handle_study_activity
    from plugins.study_os.tools import handle_study_project

    init = _loads(handle_study_project({"vault_path": str(vault), "action": "init"}))
    submitted = _loads(
        handle_study_activity(
            {
                "resource": "review",
                "action": "submit",
                "vault_path": str(vault),
                "project_id": init["data"]["project"]["project_id"],
                "data": {
                    "note": "OS/examples/OS-0043.md",
                    "response": "高级调度决定作业接纳，低级调度负责进程切换。",
                    "result": "correct",
                    "duration_seconds": 30,
                },
            }
        )
    )

    assert submitted["ok"] is True
    assert submitted["data"]["attempt"]["item_id"] == "OS/examples/OS-0043.md"
    assert submitted["data"]["review"]["review_count"] == {"old": 0, "new": 1}
    assert list((vault / ".StudyOS" / "projects" / "general-learning" / "activity").glob("attempts-*.jsonl"))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("path", "OS/examples/OS-0043.md"),
        ("item_id", "OS/examples/OS-0043.md"),
        ("notes", ["OS/examples/OS-0043.md"]),
    ],
)
def test_review_submit_accepts_one_unambiguous_note_alias(
    vault: Path,
    field: str,
    value: str | list[str],
):
    from plugins.study_os.learning import handle_study_activity
    from plugins.study_os.tools import handle_study_project

    init = _loads(handle_study_project({"vault_path": str(vault), "action": "init"}))
    submitted = _loads(
        handle_study_activity(
            {
                "resource": "review",
                "action": "submit",
                "vault_path": str(vault),
                "project_id": init["data"]["project"]["project_id"],
                "data": {
                    field: value,
                    "response": "插入和删除的操作位置限制不同。",
                    "result": "correct",
                    "duration_seconds": 1,
                },
            }
        )
    )

    assert submitted["ok"] is True
    assert submitted["data"]["attempt"]["item_id"] == "OS/examples/OS-0043.md"


def test_note_list_and_read_serialize_yaml_dates_as_iso_strings(vault: Path):
    from plugins.study_os.learning import handle_study_activity

    dated = vault / "OS" / "examples" / "dated.md"
    dated.write_text(
        "---\n"
        "type: example\n"
        "last_reviewed_at: 2026-07-28\n"
        "nested:\n"
        "  due: 2026-07-29\n"
        "---\n"
        "# Dated review\n",
        encoding="utf-8",
    )
    common = {
        "resource": "note",
        "vault_path": str(vault),
    }

    listed = _loads(
        handle_study_activity(
            {
                **common,
                "action": "list",
                "data": {"folder": "OS/examples", "file_glob": "dated.md"},
            }
        )
    )
    read = _loads(
        handle_study_activity(
            {
                **common,
                "action": "read",
                "data": {"note": "OS/examples/dated.md"},
            }
        )
    )

    assert listed["ok"] is True
    assert read["ok"] is True
    for note in (listed["data"]["notes"][0], read["data"]["note"]):
        assert note["frontmatter"]["last_reviewed_at"] == "2026-07-28"
        assert note["frontmatter"]["nested"]["due"] == "2026-07-29"


def _valid_study_project() -> dict:
    return {
        "schema_version": "study_project.v1",
        "project_id": "kaoyan-2027",
        "title": "2027 考研学习计划",
        "domain": "kaoyan",
        "exam_type": "考研",
        "exam_date": "2027-12-20",
        "timezone": "Asia/Shanghai",
        "phase": "foundation",
        "domain_pack": "kaoyan.v1",
        "subjects": [
            {"id": "math", "label": "数学", "target_score": 120},
            {"id": "english", "label": "英语一", "target_score": 75},
            {"id": "politics", "label": "政治", "target_score": 75},
        ],
        "prompt_policy": {
            "base_max_chars": 2000,
            "intent_max_chars": 2500,
            "domain_max_chars": 2000,
            "project_summary_max_chars": 1200,
            "total_max_chars": 6000,
            "updates_apply": "next_session",
        },
        "created_at": "2026-06-28T00:00:00+08:00",
        "updated_at": "2026-06-28T00:00:00+08:00",
    }


def _valid_learning_project_v2() -> dict:
    return {
        "schema_version": "study_project.v2",
        "project_id": "research-agents",
        "title": "Agent Systems Research",
        "domain": "research",
        "timezone": "Asia/Shanghai",
        "phase": "replication",
        "domain_pack": "research.v1",
        "workspace_type": "hybrid",
        "artifact_policy": "lightweight",
        "deadline": "2026-12-01",
        "tracks": [{"id": "methods", "label": "Methods"}],
        "objectives": [
            {
                "objective_id": "reproduce-routing-result",
                "capability": "Reproduce and explain one routing result from source material.",
                "success_criteria": [
                    "The reproduction command and environment are recorded.",
                    "The learner explains one limitation without assistance.",
                ],
                "evidence_targets": ["execution", "explanation", "near_transfer"],
                "source_anchors": [
                    {"kind": "paper", "ref": "doi:10.0000/example", "locator": "section 4"}
                ],
            }
        ],
        "prompt_policy": {
            "base_max_chars": 2000,
            "intent_max_chars": 2500,
            "domain_max_chars": 2000,
            "project_summary_max_chars": 1200,
            "total_max_chars": 6000,
            "updates_apply": "next_session",
        },
        "created_at": "2026-07-13T09:00:00+08:00",
        "updated_at": "2026-07-13T09:00:00+08:00",
    }


def _valid_engineering_project_v2() -> dict:
    return {
        "schema_version": "study_project.v2",
        "project_id": "engine-runtime",
        "title": "Runtime Engineering",
        "domain": "engineering",
        "timezone": "Asia/Shanghai",
        "phase": "implementation",
        "domain_pack": "engineering.v1",
        "workspace_type": "engineering-repo",
        "artifact_policy": "source-and-command",
        "tracks": [{"id": "runtime", "label": "Runtime"}],
        "objectives": [
            {
                "objective_id": "trace-request-lifecycle",
                "capability": "Trace and verify one request lifecycle through the runtime.",
                "success_criteria": [
                    "The call path names concrete files and symbols.",
                    "A command or test verifies the claimed behavior.",
                ],
                "evidence_targets": ["execution", "explanation", "near_transfer"],
                "source_anchors": [
                    {"kind": "file", "ref": "run_agent.py", "locator": "AIAgent.run_conversation"}
                ],
            }
        ],
        "prompt_policy": {
            "base_max_chars": 2000,
            "intent_max_chars": 2500,
            "domain_max_chars": 2000,
            "project_summary_max_chars": 1200,
            "total_max_chars": 6000,
            "updates_apply": "next_session",
        },
        "created_at": "2026-07-13T09:00:00+08:00",
        "updated_at": "2026-07-13T09:00:00+08:00",
    }


def _valid_study_schedule() -> dict:
    return {
        "schema_version": "study_schedule.v1",
        "schedule_id": "kaoyan-2027-master-plan",
        "project_id": "kaoyan-2027",
        "title": "2027 考研数学基础阶段计划",
        "timezone": "Asia/Shanghai",
        "range": {"start": "2026-07-01", "end": "2026-07-31"},
        "phases": [
            {
                "id": "foundation",
                "title": "基础阶段",
                "start": "2026-07-01",
                "end": "2026-09-30",
                "goal": "完成核心考点覆盖",
            }
        ],
        "events": [
            {
                "id": "evt-20260701-math-derivative",
                "title": "数学：导数定义整理",
                "subject_id": "math",
                "type": "learning",
                "start": "2026-07-01T19:00:00+08:00",
                "end": "2026-07-01T21:00:00+08:00",
                "duration_minutes": 120,
                "goals": ["整理导数定义例题"],
                "source_curriculum": "一元函数微分学",
                "status": "planned",
            }
        ],
    }


def test_study_project_schema_accepts_kaoyan_manifest():
    from plugins.study_os.schemas import validate_study_project

    project = _valid_study_project()
    project["unknown_future_field"] = {"kept": True}

    ok, data_or_errors = validate_study_project(project)

    assert ok is True
    assert data_or_errors is project
    assert data_or_errors["unknown_future_field"] == {"kept": True}


def test_study_project_v2_is_domain_neutral_and_requires_observable_objectives():
    from plugins.study_os.schemas import validate_study_project

    project = _valid_learning_project_v2()
    ok, validated = validate_study_project(project)

    assert ok is True
    assert validated is project
    assert "exam_type" not in validated
    assert "exam_date" not in validated

    project["objectives"][0]["success_criteria"] = []
    ok, errors = validate_study_project(project)

    assert ok is False
    assert "objectives[0].success_criteria must be a non-empty string array" in errors


def test_learning_contract_names_mode_assistance_and_evidence_target():
    from plugins.study_os.schemas import validate_learning_contract

    contract = {
        "schema_version": "learning_contract.v1",
        "contract_id": "contract-routing-001",
        "project_id": "research-agents",
        "mode": "research",
        "objective": "Reproduce and explain the routing result.",
        "objective_ids": ["reproduce-routing-result"],
        "time_budget_minutes": 45,
        "assistance_level": "guided",
        "evidence_targets": ["execution", "explanation"],
        "created_at": "2026-07-13T09:10:00+08:00",
    }

    ok, validated = validate_learning_contract(contract, project=_valid_learning_project_v2())

    assert ok is True
    assert validated is contract

    contract["assistance_level"] = "do-it-for-me"
    ok, errors = validate_learning_contract(contract, project=_valid_learning_project_v2())

    assert ok is False
    assert any("assistance_level" in error for error in errors)

    contract["assistance_level"] = "guided"
    contract["evidence_targets"] = ["far_transfer"]
    ok, errors = validate_learning_contract(contract, project=_valid_learning_project_v2())

    assert ok is False
    assert any("referenced objectives" in error for error in errors)


@pytest.mark.parametrize("project_id", ["../bad", "Kaoyan", "xy"])
def test_study_project_schema_rejects_invalid_project_ids(project_id: str):
    from plugins.study_os.schemas import validate_study_project

    project = _valid_study_project()
    project["project_id"] = project_id

    ok, errors = validate_study_project(project)

    assert ok is False
    assert any("project_id must match" in error for error in errors)


def test_study_schedule_schema_accepts_kaoyan_schedule():
    from plugins.study_os.schemas import validate_study_schedule

    schedule = _valid_study_schedule()
    schedule["unknown_future_field"] = "kept"

    ok, data_or_errors = validate_study_schedule(schedule)

    assert ok is True
    assert data_or_errors is schedule
    assert data_or_errors["unknown_future_field"] == "kept"


def test_study_schedule_phase_supports_aggregate_effort_and_details():
    from plugins.study_os.schemas import validate_study_schedule

    schedule = _valid_study_schedule()
    phase = schedule["phases"][0]
    phase.update(
        {
            "effort_minutes": 3600,
            "goals": ["上午完成专题", "下午完成概率"],
            "source_curricula": ["空间解析几何", "概率"],
            "status": "planned",
        }
    )

    ok, validated = validate_study_schedule(schedule)

    assert ok is True
    assert validated is schedule

    phase["effort_minutes"] = 0
    ok, errors = validate_study_schedule(schedule)

    assert ok is False
    assert "phases[0].effort_minutes must be a positive integer" in errors


def test_study_schedule_schema_rejects_datetime_without_timezone():
    from plugins.study_os.schemas import validate_study_schedule

    schedule = _valid_study_schedule()
    schedule["events"][0]["start"] = "2026-07-01T19:00:00"

    ok, errors = validate_study_schedule(schedule)

    assert ok is False
    assert "events[0].start must include timezone offset" in errors


def test_study_schedule_schema_rejects_mismatched_cross_midnight_duration():
    from plugins.study_os.schemas import validate_study_schedule

    schedule = _valid_study_schedule()
    schedule["events"][0]["start"] = "2026-07-01T23:30:00+08:00"
    schedule["events"][0]["end"] = "2026-07-02T00:15:00+08:00"
    schedule["events"][0]["duration_minutes"] = 120

    ok, errors = validate_study_schedule(schedule)

    assert ok is False
    assert "events[0].duration_minutes does not match start/end" in errors


def test_study_schedule_schema_guides_long_ranges_to_phases():
    from plugins.study_os.schemas import validate_study_schedule

    schedule = _valid_study_schedule()
    schedule["events"][0].update(
        {
            "start": "2026-07-16T08:00:00+08:00",
            "end": "2026-07-21T20:00:00+08:00",
            "duration_minutes": 3600,
        }
    )

    ok, errors = validate_study_schedule(schedule)

    assert ok is False
    assert (
        "events[0] spans more than 720 minutes; use phases for long-term ranges "
        "and events only for concrete study sessions"
    ) in errors


def test_study_schedule_schema_rejects_unknown_project_subject():
    from plugins.study_os.application import StudyOSApplication
    from plugins.study_os.schemas import validate_study_schedule

    schedule = _valid_study_schedule()
    schedule["events"][0]["subject_id"] = "physics"

    ok, validated = validate_study_schedule(schedule)
    assert ok is True
    assert isinstance(validated, dict)
    errors = StudyOSApplication.validate_schedule_relationships(
        _valid_study_project(), validated
    )

    assert "events[0].subject_id must exist in project subjects" in errors


def test_study_project_init_and_schedule_save(vault: Path):
    from plugins.study_os.tools import handle_study_project, handle_study_schedule

    init = _loads(handle_study_project({"vault_path": str(vault), "action": "init"}))
    status = _loads(handle_study_project({"vault_path": str(vault), "action": "status"}))
    schedule = _valid_study_schedule()
    schedule["project_id"] = "general-learning"
    schedule["schedule_id"] = "general-learning-master-plan"
    schedule["events"] = []
    save = _loads(handle_study_schedule({"vault_path": str(vault), "action": "save", "data": schedule}))
    read = _loads(
        handle_study_schedule(
            {
                "vault_path": str(vault),
                "action": "read",
                "project_id": "general-learning",
                "schedule_id": "general-learning-master-plan",
            }
        )
    )

    assert init["ok"] is True
    assert init["data"]["path"] == ".StudyOS/projects/general-learning/manifest.json"
    assert init["data"]["active_path"] == ".StudyOS/projects/active.json"
    assert init["data"]["project"]["domain_pack"] == "general.v1"
    assert status["ok"] is True
    assert status["data"]["project"]["project_id"] == "general-learning"
    assert save["ok"] is True
    assert save["data"]["path"] == ".StudyOS/projects/general-learning/schedules/general-learning-master-plan.json"
    assert read["ok"] is True
    assert read["data"]["schedule"]["events"] == []
    assert (vault / ".StudyOS" / "projects" / "general-learning" / "manifest.json").exists()
    assert (vault / ".StudyOS" / "projects" / "active.json").exists()
    assert (vault / ".StudyOS" / "projects" / "general-learning" / "schedules" / "general-learning-master-plan.json").exists()


def test_study_schedule_list_reports_invalid_long_term_events(vault: Path):
    from plugins.study_os.tools import handle_study_project, handle_study_schedule

    initialized = _loads(
        handle_study_project(
            {
                "vault_path": str(vault),
                "action": "init",
                "domain_pack": "kaoyan.v1",
            }
        )
    )
    assert initialized["ok"] is True
    schedule = _valid_study_schedule()
    schedule["events"][0].update(
        {
            "start": "2026-07-16T08:00:00+08:00",
            "end": "2026-07-21T20:00:00+08:00",
            "duration_minutes": 3600,
        }
    )
    path = (
        vault
        / ".StudyOS"
        / "projects"
        / "kaoyan-2027"
        / "schedules"
        / "kaoyan-2027-master-plan.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(schedule, ensure_ascii=False), encoding="utf-8")

    listed = _loads(
        handle_study_schedule(
            {
                "vault_path": str(vault),
                "action": "list",
                "project_id": "kaoyan-2027",
            }
        )
    )

    assert listed["ok"] is True
    assert listed["data"]["schedules"] == []
    assert listed["data"]["invalid_schedules"][0]["schedule_id"] == (
        "kaoyan-2027-master-plan"
    )
    assert listed["data"]["invalid_schedules"][0]["errors"] == [
        "events[0].duration_minutes must be an integer from 1 to 720",
        "events[0] spans more than 720 minutes; use phases for long-term ranges "
        "and events only for concrete study sessions",
    ]


def test_study_activity_saves_schedule_without_double_data_wrapper(vault: Path):
    from plugins.study_os.learning import handle_study_activity

    initialized = _loads(
        handle_study_activity(
            {
                "resource": "project",
                "action": "init",
                "vault_path": str(vault),
                "data": {"domain_pack": "kaoyan.v1"},
            }
        )
    )
    schedule = _valid_study_schedule()

    validated = _loads(
        handle_study_activity(
            {
                "resource": "schedule",
                "action": "validate",
                "vault_path": str(vault),
                "project_id": schedule["project_id"],
                "data": schedule,
            }
        )
    )
    saved = _loads(
        handle_study_activity(
            {
                "resource": "schedule",
                "action": "save",
                "vault_path": str(vault),
                "project_id": schedule["project_id"],
                "data": schedule,
            }
        )
    )

    assert initialized["ok"] is True
    assert validated["ok"] is True, validated
    assert saved["ok"] is True, saved
    assert saved["data"]["schedule"] == schedule
    assert saved["data"]["registered"] is True
    assert saved["data"]["panel_discovery"] == "automatic_on_next_refresh"
    assert saved["data"]["path"] == (
        ".StudyOS/projects/kaoyan-2027/schedules/kaoyan-2027-master-plan.json"
    )


@pytest.mark.parametrize("wrapper", ["data", "schedule"])
def test_study_activity_tolerates_wrapped_schedule_payloads(vault: Path, wrapper: str):
    from plugins.study_os.learning import handle_study_activity

    initialized = _loads(
        handle_study_activity(
            {
                "resource": "project",
                "action": "init",
                "vault_path": str(vault),
                "data": {"domain_pack": "kaoyan.v1"},
            }
        )
    )
    schedule = _valid_study_schedule()
    validated = _loads(
        handle_study_activity(
            {
                "resource": "schedule",
                "action": "validate",
                "vault_path": str(vault),
                "project_id": schedule["project_id"],
                "data": {wrapper: schedule},
            }
        )
    )

    assert initialized["ok"] is True
    assert validated["ok"] is True, validated
    assert validated["data"]["schedule"] == schedule


def test_study_project_init_keeps_kaoyan_explicit(vault: Path):
    from plugins.study_os.tools import handle_study_project, handle_study_schedule

    init = _loads(handle_study_project({"vault_path": str(vault), "action": "init", "domain_pack": "kaoyan.v1"}))
    template = _loads(handle_study_schedule({"vault_path": str(vault), "action": "template", "project_id": "kaoyan-2027"}))

    assert init["ok"] is True
    assert init["data"]["project"]["project_id"] == "kaoyan-2027"
    assert init["data"]["project"]["domain_pack"] == "kaoyan.v1"
    assert init["data"]["project"]["workspace_type"] == "exam-vault"
    assert template["ok"] is True
    assert template["data"]["schedule"]["events"][0]["subject_id"] == "math"


def test_study_project_init_can_create_domain_neutral_v2(vault: Path):
    from plugins.study_os.tools import handle_study_project

    expected = _valid_learning_project_v2()
    init = _loads(
        handle_study_project(
            {
                "vault_path": str(vault),
                "action": "init",
                **{
                    key: value
                    for key, value in expected.items()
                    if key not in {"prompt_policy", "created_at", "updated_at"}
                },
            }
        )
    )

    assert init["ok"] is True
    project = init["data"]["project"]
    assert project["schema_version"] == "study_project.v2"
    assert project["objectives"][0]["objective_id"] == "reproduce-routing-result"
    assert project["tracks"] == [{"id": "methods", "label": "Methods"}]
    assert "exam_date" not in project
    assert "exam_type" not in project


def test_study_project_engineering_prompt_context(vault: Path):
    from plugins.study_os.tools import handle_study_project, handle_study_prompt_context, handle_study_schedule

    init = _loads(
        handle_study_project(
            {
                "vault_path": str(vault),
                "action": "init",
                "project_id": "ai-infra",
                "title": "AI Infra Learning",
                "domain": "ai-infra",
                "domain_pack": "engineering.v1",
                "workspace_type": "hybrid",
                "subjects": [{"id": "ai-infra", "label": "AI Infra"}],
            }
        )
    )
    context = _loads(
        handle_study_prompt_context(
            {
                "vault_path": str(vault),
                "intent": "planning",
                "project_id": "ai-infra",
            }
        )
    )
    template = _loads(handle_study_schedule({"vault_path": str(vault), "action": "template", "project_id": "ai-infra"}))

    assert init["ok"] is True
    assert init["data"]["project"]["domain_pack"] == "engineering.v1"
    assert init["data"]["project"]["workspace_type"] == "hybrid"
    assert context["ok"] is True
    fragments = {fragment["kind"]: fragment["content"] for fragment in context["data"]["fragments"]}
    # Marked fragments carry body text only, never the YAML frontmatter.
    assert "engineering-repo" in fragments["domain"]
    assert "hybrid" in fragments["domain"]
    assert "description:" not in fragments["domain"]
    assert template["ok"] is True
    assert template["data"]["schedule"]["events"][0]["subject_id"] == "ai-infra"
    assert "Scout one concept" in template["data"]["schedule"]["events"][0]["title"]


def test_study_decision_creates_learning_decision_record(vault: Path):
    from plugins.study_os.tools import handle_study_decision, handle_study_project

    init = _loads(
        handle_study_project(
            {
                "vault_path": str(vault),
                "action": "init",
                "project_id": "ai-infra",
                "domain_pack": "engineering.v1",
                "subjects": [{"id": "ai-infra", "label": "AI Infra"}],
            }
        )
    )
    created = _loads(
        handle_study_decision(
            {
                "vault_path": str(vault),
                "project_id": "ai-infra",
                "action": "create",
                "title": "Use hybrid AI Infra workspace",
                "decision": "Keep source exploration in infra-learning and reusable concepts in AIInfra.",
                "context": "The user wants StudyOS to support engineering learning without copying exam-vault behavior.",
                "options_considered": ["all notes in repo", "heavy StudyOS vault", "hybrid workspace"],
                "linked_concepts": ["KV Cache", "PagedAttention"],
                "linked_sources": ["/home/puji/infra-learning"],
                "linked_sessions": ["grill:2026-07-01"],
            }
        )
    )
    listed = _loads(handle_study_decision({"vault_path": str(vault), "project_id": "ai-infra", "action": "list"}))
    read = _loads(
        handle_study_decision(
            {
                "vault_path": str(vault),
                "project_id": "ai-infra",
                "action": "read",
                "decision_id": created["data"]["decision"]["decision_id"],
            }
        )
    )

    assert init["ok"] is True
    assert created["ok"] is True
    assert created["data"]["path"].startswith(".StudyOS/projects/ai-infra/decisions/")
    assert (vault / created["data"]["path"]).exists()
    assert listed["data"]["decisions"][0]["title"] == "Use hybrid AI Infra workspace"
    assert read["ok"] is True
    assert "schema_version: learning_decision_record.v1" in read["data"]["content"]
    assert "KV Cache" in read["data"]["content"]


def test_study_learning_record_creates_learning_record(vault: Path):
    from plugins.study_os.tools import handle_study_learning_record, handle_study_project, handle_study_prompt_context

    init = _loads(
        handle_study_project(
            {
                "vault_path": str(vault),
                "action": "init",
                "project_id": "ai-infra",
                "domain_pack": "engineering.v1",
                "subjects": [{"id": "ai-infra", "label": "AI Infra"}],
            }
        )
    )
    context = _loads(
        handle_study_prompt_context(
            {
                "vault_path": str(vault),
                "project_id": "ai-infra",
                "intent": "teaching",
            }
        )
    )
    created = _loads(
        handle_study_learning_record(
            {
                "vault_path": str(vault),
                "project_id": "ai-infra",
                "action": "create",
                "title": "Understands prefill versus decode",
                "summary": "The user can explain why LLM serving separates prompt prefill from token decode.",
                "evidence": "They correctly compared long-prompt startup cost with per-token decode cost.",
                "implications": "Future lessons can discuss continuous batching without re-teaching this split.",
                "linked_concepts": ["Prefill", "Decode"],
                "linked_sources": ["/home/puji/infra-learning/vllm"],
            }
        )
    )
    listed = _loads(handle_study_learning_record({"vault_path": str(vault), "project_id": "ai-infra", "action": "list"}))
    read = _loads(
        handle_study_learning_record(
            {
                "vault_path": str(vault),
                "project_id": "ai-infra",
                "action": "read",
                "record_id": created["data"]["record"]["record_id"],
            }
        )
    )

    assert init["ok"] is True
    assert context["ok"] is True
    assert any("study-teach" in fragment["source"] for fragment in context["data"]["fragments"])
    assert created["ok"] is True
    assert created["data"]["path"].startswith(".StudyOS/projects/ai-infra/learning-records/")
    assert listed["data"]["records"][0]["title"] == "Understands prefill versus decode"
    assert read["ok"] is True
    assert "schema_version: learning_record.v1" in read["data"]["content"]
    assert "continuous batching" in read["data"]["content"]


def test_study_learning_record_requires_evidence(vault: Path):
    from plugins.study_os.tools import handle_study_learning_record, handle_study_project

    init = _loads(handle_study_project({"vault_path": str(vault), "action": "init"}))
    created = _loads(
        handle_study_learning_record(
            {
                "vault_path": str(vault),
                "action": "create",
                "title": "Covered prefill",
                "summary": "The session mentioned prefill.",
            }
        )
    )

    assert init["ok"] is True
    assert created["ok"] is False
    assert created["error"]["code"] == "VALIDATION_FAILED"
    assert not (vault / ".StudyOS" / "projects" / "general-learning" / "learning-records").exists()


def test_study_lesson_creates_visual_lesson_artifact(vault: Path):
    from plugins.study_os.tools import handle_study_lesson, handle_study_project

    init = _loads(handle_study_project({"vault_path": str(vault), "action": "init"}))
    html = "<!doctype html><html><head><title>Prefill vs Decode</title></head><body><h1>Prefill vs Decode</h1></body></html>"
    created = _loads(
        handle_study_lesson(
            {
                "vault_path": str(vault),
                "action": "create",
                "title": "Prefill vs Decode",
                "rationale": "The split is easiest to understand as a request timeline.",
                "html": html,
                "linked_concepts": ["Prefill", "Decode"],
                "linked_sources": ["/home/puji/infra-learning/vllm"],
            }
        )
    )
    listed = _loads(handle_study_lesson({"vault_path": str(vault), "action": "list"}))
    read = _loads(
        handle_study_lesson(
            {
                "vault_path": str(vault),
                "action": "read",
                "lesson_id": created["data"]["lesson"]["lesson_id"],
            }
        )
    )

    assert init["ok"] is True
    assert created["ok"] is True
    assert created["data"]["path"].startswith(".StudyOS/projects/general-learning/lessons/")
    assert created["data"]["metadata_path"].endswith(".json")
    assert (vault / created["data"]["path"]).exists()
    assert listed["data"]["lessons"][0]["title"] == "Prefill vs Decode"
    assert read["ok"] is True
    assert read["data"]["metadata"]["schema_version"] == "visual_lesson.v1"
    assert "Prefill vs Decode" in read["data"]["html"]


def test_study_lesson_requires_complete_html(vault: Path):
    from plugins.study_os.tools import handle_study_lesson, handle_study_project

    init = _loads(handle_study_project({"vault_path": str(vault), "action": "init"}))
    created = _loads(
        handle_study_lesson(
            {
                "vault_path": str(vault),
                "action": "create",
                "title": "Incomplete",
                "rationale": "Visual layout needed.",
                "html": "<section>not complete</section>",
            }
        )
    )

    assert init["ok"] is True
    assert created["ok"] is False
    assert created["error"]["code"] == "VALIDATION_FAILED"


def test_study_schedule_read_returns_not_found(vault: Path):
    from plugins.study_os.tools import handle_study_project, handle_study_schedule

    init = _loads(handle_study_project({"vault_path": str(vault), "action": "init", "domain_pack": "kaoyan.v1"}))
    missing = _loads(
        handle_study_schedule(
            {
                "vault_path": str(vault),
                "action": "read",
                "project_id": "kaoyan-2027",
                "schedule_id": "kaoyan-2027-missing",
            }
        )
    )

    assert init["ok"] is True
    assert missing["ok"] is False
    assert missing["error"]["code"] == "SCHEDULE_NOT_FOUND"


def test_study_project_and_prompt_context_reject_invalid_inputs(vault: Path):
    from plugins.study_os.tools import handle_study_project, handle_study_prompt_context

    traversal = _loads(handle_study_project({"vault_path": str(vault), "action": "init", "project_id": "../escape"}))
    invalid_intent = _loads(
        handle_study_prompt_context(
            {
                "vault_path": str(vault),
                "intent": "motivation-hype",
                "project_id": "kaoyan-2027",
            }
        )
    )

    assert traversal["ok"] is False
    assert traversal["error"]["code"] == "VALIDATION_FAILED"
    assert invalid_intent["ok"] is False
    assert invalid_intent["error"]["code"] == "INVALID_INTENT"
    assert not (vault.parent / "escape").exists()


def test_study_prompt_context_reserves_do_not_cap_project_summary(vault: Path):
    from plugins.study_os.tools import handle_study_project, handle_study_prompt_context

    init = _loads(
        handle_study_project(
            {
                "vault_path": str(vault),
                "action": "init",
                "project_id": "general-2027",
                "domain": "general",
                "domain_pack": "general.v1",
            }
        )
    )
    summary = _loads(
        handle_study_project(
            {
                "vault_path": str(vault),
                "action": "update_prompt_summary",
                "project_id": "general-2027",
                "summary": "x" * 2000,
            }
        )
    )
    context = _loads(
        handle_study_prompt_context(
            {
                "vault_path": str(vault),
                "intent": "reviewing",
                "project_id": "general-2027",
            }
        )
    )

    assert init["ok"] is True
    assert summary["ok"] is True
    # project_summary_max_chars is a reserve on both paths now: 2000 chars is
    # well past the 1200-char reserve, so the write path stores it untouched and
    # prompt_context.load delivers it whole while the pool has room for it.
    assert summary["warnings"] == []
    assert summary["data"]["char_count"] == 2000
    assert (
        vault / ".StudyOS" / "projects" / "general-2027" / "prompt_summary.md"
    ).read_text(encoding="utf-8") == "x" * 2000
    assert context["ok"] is True
    fragments = {fragment["kind"]: fragment for fragment in context["data"]["fragments"]}
    assert not [warning for warning in context["warnings"] if "project_summary" in warning]
    assert 0 < fragments["project_summary"]["char_count"] <= 2000
    budget = context["data"]["budget"]
    assert fragments["project_summary"]["token_count"] <= budget["granted_tokens"]["project_summary"]
    assert context["data"]["total_token_count"] <= budget["pool_tokens"]


def _tighten_prompt_policy(vault: Path, project_id: str, **overrides) -> None:
    manifest_path = vault / ".StudyOS" / "projects" / project_id / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["prompt_policy"] = {**manifest["prompt_policy"], **overrides}
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")


def _update_prompt_summary(vault: Path, project_id: str, summary: str) -> dict:
    from plugins.study_os.tools import handle_study_project

    return _loads(
        handle_study_project(
            {
                "vault_path": str(vault),
                "action": "update_prompt_summary",
                "project_id": project_id,
                "summary": summary,
            }
        )
    )


def test_study_project_stores_summaries_larger_than_the_project_summary_reserve(vault: Path):
    """The sanctioned writer must reach the headroom the reader will deliver.

    project_summary_max_chars (1200) is a floor for prompt_context.load, not a
    ceiling; enforcing it here made ~3000 characters of reachable budget
    unwritable through the only tool that exists for the job.
    """

    assert _init_project(vault, "general-2027", "general.v1")["ok"] is True

    written = _update_prompt_summary(vault, "general-2027", "x" * 3000)
    context = _load_prompt_context(vault, "general-2027")

    assert written["ok"] is True
    assert written["warnings"] == []
    assert written["data"]["char_count"] == 3000
    summary_path = vault / ".StudyOS" / "projects" / "general-2027" / "prompt_summary.md"
    assert len(summary_path.read_text(encoding="utf-8")) == 3000
    assert context["ok"] is True
    fragments = {fragment["kind"]: fragment for fragment in context["data"]["fragments"]}
    assert fragments["project_summary"]["char_count"] > 1200


def test_study_project_stores_an_oversized_summary_whole_and_says_what_is_reachable(
    vault: Path,
):
    """Storage is project memory; the prompt budget describes it, never cuts it.

    A boundary-preferring cut at a storage ceiling refused text the reader would
    have delivered -- this summary's only section break below 6000 characters
    sits at 3600, so 449 characters of reachable budget were unwritable -- and
    the warning blamed total_max_chars when the constraint that actually cut was
    prompt_budget's unexposed boundary retention floor.
    """

    assert _init_project(vault, "general-2027", "general.v1")["ok"] is True
    oversized = "x" * 3600 + "\n## Later section" + "y" * 20000

    written = _update_prompt_summary(vault, "general-2027", oversized)
    context = _load_prompt_context(vault, "general-2027")

    assert written["ok"] is True
    assert written["data"]["char_count"] == len(oversized)
    stored = (vault / ".StudyOS" / "projects" / "general-2027" / "prompt_summary.md").read_text(
        encoding="utf-8"
    )
    assert stored == oversized
    assert [warning.split(";")[0] for warning in written["warnings"]] == [
        "summary is 5905 tokens",
        "summary is 23617 characters",
    ]
    for warning in written["warnings"]:
        assert "will not reach the model" in warning
    assert "total_max_tokens" in written["warnings"][0]
    assert "total_max_chars" in written["warnings"][1]
    fragments = {fragment["kind"]: fragment for fragment in context["data"]["fragments"]}
    assert fragments["project_summary"]["char_count"] > 3600


def test_study_project_storage_does_not_shrink_when_the_prompt_budget_does(vault: Path):
    """Lowering the injected prompt must not destroy stored project memory."""

    assert _init_project(vault, "general-2027", "general.v1")["ok"] is True
    _tighten_prompt_policy(vault, "general-2027", total_max_chars=1500)

    written = _update_prompt_summary(vault, "general-2027", "x" * 3000)

    assert written["ok"] is True
    assert written["data"]["char_count"] == 3000
    assert (
        vault / ".StudyOS" / "projects" / "general-2027" / "prompt_summary.md"
    ).read_text(encoding="utf-8") == "x" * 3000
    assert written["warnings"] == [
        "summary is 3000 characters; prompt_context.load shares a 1500 character "
        "ceiling (total_max_chars) across every fragment, so its tail will not "
        "reach the model"
    ]


def test_study_project_warns_when_a_cjk_summary_outruns_the_token_pool(vault: Path):
    """The reader binds in tokens, so a character-only ceiling saw nothing wrong.

    StudyOS is 考研-facing: 5056 Chinese characters fit any character ceiling in
    the policy and still cost ~4x their length in tokens, so this write reported
    ok with no warnings at all while three quarters of it was unreachable.
    """

    assert _init_project(vault, "general-2027", "general.v1")["ok"] is True
    cjk = "复习进度记录，重点在数学和英语。" * 316

    written = _update_prompt_summary(vault, "general-2027", cjk)
    context = _load_prompt_context(vault, "general-2027")

    assert written["data"]["char_count"] == len(cjk) == 5056
    assert len(written["warnings"]) == 1
    assert written["warnings"][0].startswith("summary is 5056 tokens")
    assert "1800 token pool (total_max_tokens)" in written["warnings"][0]
    fragments = {fragment["kind"]: fragment for fragment in context["data"]["fragments"]}
    assert fragments["project_summary"]["char_count"] < len(cjk)


def test_study_prompt_context_bounds_the_work_a_huge_summary_can_cost(vault: Path):
    """prompt_summary.md is user-editable and the reader pays for it every turn.

    Obsidian sync, a script or a bad merge can put anything in this file. A
    1.2 MB one cost 1.5 s of estimator and prefix-count work per load (10 MB:
    13 s and +70 MB RSS) to deliver the same 4050 characters. No grant drawn
    from an n-token pool can reach past 4n characters, so the reader stops
    there -- and what it delivers is *identical* to reading the whole file.
    """

    from plugins.study_os.prompt_budget import truncate_to_chars, truncate_to_tokens
    from plugins.study_os.tools import _read_summary_text

    assert _init_project(vault, "general-2027", "general.v1")["ok"] is True
    body = "# Summary\n\n" + "progress note here. " * 61440
    assert len(body) > 1_200_000
    path = vault / ".StudyOS" / "projects" / "general-2027" / "prompt_summary.md"
    path.write_text(body, encoding="utf-8")

    context = _load_prompt_context(vault, "general-2027")

    assert len(_read_summary_text(path, 1800)) == 4 * 1800 + 4
    budget = context["data"]["budget"]
    fragments = {fragment["kind"]: fragment for fragment in context["data"]["fragments"]}
    delivered = fragments["project_summary"]["content"]
    whole_file, _truncated = truncate_to_tokens(body, budget["granted_tokens"]["project_summary"])
    assert delivered == truncate_to_chars(
        whole_file, budget["granted_chars"]["project_summary"]
    )[0]
    assert 0 < len(delivered) <= budget["total_max_chars"]


def test_study_prompt_context_truncates_project_summary_to_total_budget(vault: Path):
    from plugins.study_os.tools import handle_study_project, handle_study_prompt_context

    initialized = _loads(
        handle_study_project(
            {
                "vault_path": str(vault),
                "action": "init",
                "project_id": "kaoyan-2027",
                "domain_pack": "kaoyan.v1",
            }
        )
    )
    summary_path = vault / ".StudyOS" / "projects" / "kaoyan-2027" / "prompt_summary.md"
    summary_path.write_text("z" * 1200, encoding="utf-8")

    def _load() -> dict:
        return _loads(
            handle_study_prompt_context(
                {
                    "vault_path": str(vault),
                    "intent": "reviewing",
                    "project_id": "kaoyan-2027",
                }
            )
        )

    generous = _load()
    fixed_chars = sum(
        fragment["char_count"]
        for fragment in generous["data"]["fragments"]
        if fragment["kind"] != "project_summary"
    )
    fixed_tokens = sum(
        fragment["token_count"]
        for fragment in generous["data"]["fragments"]
        if fragment["kind"] != "project_summary"
    )

    # Step 1 of the degradation ladder: the token pool binds first.
    _tighten_prompt_policy(vault, "kaoyan-2027", total_max_tokens=fixed_tokens + 40)
    token_bound = _load()
    # Step 1 again, this time via the secondary character ceiling.
    _tighten_prompt_policy(
        vault,
        "kaoyan-2027",
        total_max_tokens=1800,
        total_max_chars=fixed_chars + 200,
    )
    char_bound = _load()

    assert initialized["ok"] is True
    for context in (generous, token_bound, char_bound):
        assert context["ok"] is True
        fragments = {fragment["kind"]: fragment for fragment in context["data"]["fragments"]}
        assert set(fragments) == {"base", "intent", "domain", "project_summary"}
        assert 0 < fragments["project_summary"]["char_count"] <= 1200
        budget = context["data"]["budget"]
        assert context["data"]["total_token_count"] <= budget["pool_tokens"]
        assert context["data"]["total_char_count"] <= budget["total_max_chars"]
    assert generous["warnings"] == []
    for context in (token_bound, char_bound):
        assert any(
            "project_summary" in warning and "truncat" in warning
            for warning in context["warnings"]
        ), context["warnings"]
        assert not [
            warning
            for warning in context["warnings"]
            if warning.startswith(("base ", "intent ", "domain "))
        ]
    assert token_bound["data"]["fragments"][-1]["token_count"] <= 40
    assert char_bound["data"]["fragments"][-1]["char_count"] <= 200


@pytest.fixture
def skills_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect prompt fragment lookups at a writable copy of the real skills.

    Tests may overwrite or delete a ``SKILL.md`` under the returned root to
    exercise marker parsing and the fail-soft ladder without editing the
    shipped documents.
    """

    from plugins.study_os import tools as study_tools

    root = tmp_path / "skills"
    # Anchored at the repo, not the cwd: a cwd-relative glob matches nothing
    # from a foreign working directory and the fixture would then silently
    # publish an empty skills tree.
    real_root = Path(__file__).resolve().parents[2] / "plugins" / "study_os" / "skills"
    assert real_root.is_dir(), real_root
    for source in sorted(real_root.glob("*/SKILL.md")):
        target = root / source.parent.name / "SKILL.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(study_tools, "_skill_path", lambda name: root / name / "SKILL.md")
    return root


def _init_project(vault: Path, project_id: str, domain_pack: str) -> dict:
    from plugins.study_os.tools import handle_study_project

    return _loads(
        handle_study_project(
            {
                "vault_path": str(vault),
                "action": "init",
                "project_id": project_id,
                "domain_pack": domain_pack,
            }
        )
    )


def _load_prompt_context(vault: Path, project_id: str, intent: str = "reviewing") -> dict:
    from plugins.study_os.tools import handle_study_prompt_context

    return _loads(
        handle_study_prompt_context(
            {"vault_path": str(vault), "intent": intent, "project_id": project_id}
        )
    )


def test_study_prompt_context_uses_only_the_marked_region(vault: Path, skills_root: Path):
    (skills_root / "study-os" / "SKILL.md").write_text(
        "---\n"
        "name: study-os\n"
        "description: Route StudyOS learning workflows.\n"
        "---\n\n"
        "PREAMBLE_OUTSIDE_MARKERS\n\n"
        "<!-- prompt-context:begin -->\n"
        "FIRST_MARKED_REGION\n"
        "<!-- prompt-context:end -->\n\n"
        "REFERENCE_OUTSIDE_MARKERS\n\n"
        "<!-- prompt-context:begin -->\n"
        "SECOND_MARKED_REGION\n"
        "<!-- prompt-context:end -->\n\n"
        "TRAILER_OUTSIDE_MARKERS\n",
        encoding="utf-8",
    )

    initialized = _init_project(vault, "general-2027", "general.v1")
    context = _load_prompt_context(vault, "general-2027")

    assert initialized["ok"] is True
    assert context["ok"] is True
    fragments = {fragment["kind"]: fragment for fragment in context["data"]["fragments"]}
    # Every marked region, in document order, joined by a blank line; nothing
    # outside the markers (frontmatter included) reaches the prompt.
    assert fragments["base"]["content"] == "FIRST_MARKED_REGION\n\nSECOND_MARKED_REGION"
    for excluded in (
        "PREAMBLE_OUTSIDE_MARKERS",
        "REFERENCE_OUTSIDE_MARKERS",
        "TRAILER_OUTSIDE_MARKERS",
        "description:",
    ):
        assert excluded not in fragments["base"]["content"]
    assert context["warnings"] == []


def test_study_prompt_context_falls_back_to_whole_unmarked_skill(vault: Path, skills_root: Path):
    body = (
        "---\n"
        "name: study-review\n"
        "description: Run flexible StudyOS spaced-repetition reviews.\n"
        "---\n\n"
        "# Legacy Unmarked Skill\n\n"
        "This document carries no prompt-context markers at all.\n"
    )
    (skills_root / "study-review" / "SKILL.md").write_text(body, encoding="utf-8")

    initialized = _init_project(vault, "general-2027", "general.v1")
    context = _load_prompt_context(vault, "general-2027")

    assert initialized["ok"] is True
    assert context["ok"] is True
    fragments = {fragment["kind"]: fragment for fragment in context["data"]["fragments"]}
    assert fragments["intent"]["content"] == body
    assert context["warnings"] == []


def test_study_prompt_context_warns_once_on_unterminated_marker(vault: Path, skills_root: Path):
    (skills_root / "study-review" / "SKILL.md").write_text(
        "---\nname: study-review\ndescription: Run reviews.\n---\n\n"
        "DROPPED_PREAMBLE\n\n"
        "<!-- prompt-context:begin -->\n"
        "TAIL_OF_DOCUMENT\n",
        encoding="utf-8",
    )

    initialized = _init_project(vault, "general-2027", "general.v1")
    context = _load_prompt_context(vault, "general-2027")

    assert initialized["ok"] is True
    assert context["ok"] is True
    fragments = {fragment["kind"]: fragment for fragment in context["data"]["fragments"]}
    assert fragments["intent"]["content"] == "TAIL_OF_DOCUMENT"
    assert "DROPPED_PREAMBLE" not in fragments["intent"]["content"]
    assert [warning for warning in context["warnings"] if "unterminated" in warning]


def test_study_prompt_context_skips_missing_domain_skill_with_warning(vault: Path, skills_root: Path):
    (skills_root / "study-kaoyan" / "SKILL.md").unlink()

    initialized = _init_project(vault, "kaoyan-2027", "kaoyan.v1")
    context = _load_prompt_context(vault, "kaoyan-2027")

    assert initialized["ok"] is True
    # A domain pack whose skill file is gone degrades to base + intent routing
    # instead of failing closed the way the old fixed-cap loader did.
    assert context["ok"] is True
    assert {fragment["kind"] for fragment in context["data"]["fragments"]} == {"base", "intent"}
    assert [
        warning
        for warning in context["warnings"]
        if "domain" in warning and "skipped" in warning
    ], context["warnings"]


@pytest.mark.parametrize("kind, skill", [("base", "study-os"), ("intent", "study-review")])
def test_study_prompt_context_still_fails_closed_without_base_or_intent(
    vault: Path, skills_root: Path, kind: str, skill: str
):
    (skills_root / skill / "SKILL.md").unlink()

    initialized = _init_project(vault, "kaoyan-2027", "kaoyan.v1")
    context = _load_prompt_context(vault, "kaoyan-2027")

    assert initialized["ok"] is True
    assert context["ok"] is False
    assert context["error"]["code"] == "PROMPT_CONTEXT_SOURCE_MISSING"
    assert kind in context["error"]["message"]


def test_study_prompt_context_degrades_oversized_project_summary(vault: Path):
    initialized = _init_project(vault, "kaoyan-2027", "kaoyan.v1")
    summary_path = vault / ".StudyOS" / "projects" / "kaoyan-2027" / "prompt_summary.md"
    summary_path.write_text("q" * 200_000, encoding="utf-8")

    context = _load_prompt_context(vault, "kaoyan-2027")

    assert initialized["ok"] is True
    # A summary far larger than the whole pool degrades; it never produces
    # PROMPT_CONTEXT_TOO_LARGE and never suppresses the routing fragments.
    assert context["ok"] is True
    fragments = {fragment["kind"]: fragment for fragment in context["data"]["fragments"]}
    assert set(fragments) == {"base", "intent", "domain", "project_summary"}
    # Ladder step 1 (truncate) fires before step 2 (drop), so the summary is
    # still delivered, just clipped and flagged.
    assert [
        warning
        for warning in context["warnings"]
        if warning.startswith("project_summary fragment truncated")
    ], context["warnings"]
    assert fragments["project_summary"]["content"].endswith("…")
    assert fragments["project_summary"]["char_count"] < 200_000
    budget = context["data"]["budget"]
    assert context["data"]["total_token_count"] <= budget["pool_tokens"]
    assert context["data"]["total_char_count"] <= budget["total_max_chars"]


def test_study_prompt_context_never_drops_base_or_intent(vault: Path):
    initialized = _init_project(vault, "kaoyan-2027", "kaoyan.v1")
    summary_path = vault / ".StudyOS" / "projects" / "kaoyan-2027" / "prompt_summary.md"
    summary_path.write_text("w" * 4000, encoding="utf-8")

    generous = _load_prompt_context(vault, "kaoyan-2027")
    routing_tokens = sum(
        fragment["token_count"]
        for fragment in generous["data"]["fragments"]
        if fragment["kind"] in ("base", "intent")
    )
    # Squeeze the pool down to less than base + intent alone need: the ladder
    # must drop the optional fragments and truncate the routing ones instead of
    # returning nothing.
    _tighten_prompt_policy(vault, "kaoyan-2027", total_max_tokens=routing_tokens - 20)
    squeezed = _load_prompt_context(vault, "kaoyan-2027")

    assert initialized["ok"] is True
    assert squeezed["ok"] is True
    fragments = {fragment["kind"]: fragment for fragment in squeezed["data"]["fragments"]}
    assert set(fragments) == {"base", "intent"}
    assert fragments["base"]["content"]
    assert fragments["intent"]["content"]
    for dropped in ("domain", "project_summary"):
        assert [
            warning
            for warning in squeezed["warnings"]
            if warning.startswith(f"{dropped} fragment dropped")
        ], squeezed["warnings"]
    assert squeezed["data"]["total_token_count"] <= routing_tokens - 20


@pytest.mark.parametrize("pool", [4, 20, 100, 232, 233, 400, 546, 547, 800])
def test_study_prompt_context_returns_routing_fragments_at_any_usable_pool(
    vault: Path, pool: int
):
    _init_project(vault, "kaoyan-2027", "kaoyan.v1")
    _tighten_prompt_policy(vault, "kaoyan-2027", total_max_tokens=pool)

    context = _load_prompt_context(vault, "kaoyan-2027")

    # base used to be funded to its full want before intent got anything, so
    # every pool under ~234 produced PROMPT_CONTEXT_TOO_LARGE and ZERO
    # fragments -- "generic Hermes with no StudyOS rules" by another door.
    assert context["ok"] is True, context
    fragments = {fragment["kind"]: fragment for fragment in context["data"]["fragments"]}
    assert {"base", "intent"} <= set(fragments), (pool, fragments)
    for kind in ("base", "intent"):
        assert fragments[kind]["content"], (pool, kind)
    assert context["data"]["total_token_count"] <= pool


def test_study_prompt_context_sacrifices_the_summary_before_the_routing_pair(
    vault: Path, skills_root: Path
):
    def _inflate(name: str, size: int) -> None:
        path = skills_root / name / "SKILL.md"
        text = path.read_text(encoding="utf-8")
        begin = text.index(FRAGMENT_BEGIN_MARKER) + len(FRAGMENT_BEGIN_MARKER)
        end = text.index(FRAGMENT_END_MARKER)
        filler = "\n- an extra operating rule that grows the marked region.\n" * 200
        path.write_text(text[:begin] + (text[begin:end] + filler)[:size] + text[end:], "utf-8")

    _inflate("study-os", 4000)
    _inflate("study-review", 3000)
    _init_project(vault, "kaoyan-2027", "kaoyan.v1")
    summary_path = vault / ".StudyOS" / "projects" / "kaoyan-2027" / "prompt_summary.md"
    summary_path.write_text("s" * 1200, encoding="utf-8")
    _tighten_prompt_policy(vault, "kaoyan-2027", total_max_chars=100_000)

    context = _load_prompt_context(vault, "kaoyan-2027")

    assert context["ok"] is True
    fragments = {fragment["kind"]: fragment for fragment in context["data"]["fragments"]}
    # The ladder retires project_summary and domain; it never clips the routing
    # contract to make room for a project summary.
    assert set(fragments) == {"base", "intent"}
    for kind in ("base", "intent"):
        assert not fragments[kind]["content"].endswith("…"), kind
    for dropped in ("domain", "project_summary"):
        assert [
            warning
            for warning in context["warnings"]
            if warning.startswith(f"{dropped} fragment dropped")
        ], context["warnings"]


def test_study_prompt_context_drops_domain_rather_than_stubbing_it(vault: Path):
    _init_project(vault, "kaoyan-2027", "kaoyan.v1")
    generous = _load_prompt_context(vault, "kaoyan-2027")
    routing_tokens = sum(
        fragment["token_count"]
        for fragment in generous["data"]["fragments"]
        if fragment["kind"] in ("base", "intent")
    )

    contexts = {}
    for spare in (200, 100, 13):
        _tighten_prompt_policy(vault, "kaoyan-2027", total_max_tokens=routing_tokens + spare)
        contexts[spare] = _load_prompt_context(vault, "kaoyan-2027")

    for spare, context in contexts.items():
        kinds = {fragment["kind"] for fragment in context["data"]["fragments"]}
        # A domain fragment below its reserve is a truncated heading and half a
        # sentence presented as the domain's operating rules; drop it instead.
        assert kinds == {"base", "intent"}, (spare, kinds)
        assert [
            warning
            for warning in context["warnings"]
            if warning.startswith("domain fragment dropped") and "reserve" in warning
        ], (spare, context["warnings"])


def test_study_prompt_context_char_ceiling_follows_the_same_ladder(vault: Path):
    _init_project(vault, "kaoyan-2027", "kaoyan.v1")
    generous = _load_prompt_context(vault, "kaoyan-2027")
    sizes = {fragment["kind"]: fragment["char_count"] for fragment in generous["data"]["fragments"]}
    _tighten_prompt_policy(
        vault, "kaoyan-2027", total_max_chars=sizes["base"] + sizes["intent"] + 100
    )

    context = _load_prompt_context(vault, "kaoyan-2027")

    assert context["ok"] is True
    fragments = {fragment["kind"]: fragment for fragment in context["data"]["fragments"]}
    # total_max_chars used to be spent greedily per fragment, which let the
    # lowest-priority fragment survive on characters base had left unused.
    assert set(fragments) == {"base", "intent"}
    assert fragments["base"]["char_count"] == sizes["base"]
    assert fragments["intent"]["char_count"] == sizes["intent"]
    assert context["data"]["total_char_count"] <= sizes["base"] + sizes["intent"] + 100


@pytest.mark.parametrize(
    "body",
    [
        f"---\nname: study-kaoyan\ndescription: Guide.\n---\n\n"
        f"prose\n\n{FRAGMENT_BEGIN_MARKER}\n{FRAGMENT_END_MARKER}\n",
        f"---\nname: study-kaoyan\ndescription: Guide.\n---\n\nprose\n\n{FRAGMENT_BEGIN_MARKER}\n",
        f"---\nname: study-kaoyan\ndescription: Guide.\n---\n\n"
        f"{FRAGMENT_END_MARKER}\nprose\n{FRAGMENT_BEGIN_MARKER}\n",
    ],
    ids=["empty-region", "unterminated-at-eof", "reversed-markers"],
)
def test_study_prompt_context_warns_when_a_domain_region_is_empty(
    vault: Path, skills_root: Path, body: str
):
    (skills_root / "study-kaoyan" / "SKILL.md").write_text(body, encoding="utf-8")

    _init_project(vault, "kaoyan-2027", "kaoyan.v1")
    context = _load_prompt_context(vault, "kaoyan-2027")

    assert context["ok"] is True
    assert {fragment["kind"] for fragment in context["data"]["fragments"]} == {"base", "intent"}
    # A marker mistake must never lose the 考研 rules silently: the skip is
    # reported, and so is the marker warning that explains it.
    assert [
        warning for warning in context["warnings"] if "domain" in warning and "skipped" in warning
    ], context["warnings"]


def test_study_activity_loads_all_workflow_contexts_within_budget(vault: Path):
    from plugins.study_os.learning import handle_study_activity

    initialized = _loads(
        handle_study_activity(
            {
                "resource": "project",
                "action": "init",
                "vault_path": str(vault),
                "data": {"project_id": "general-2027"},
            }
        )
    )
    assert initialized["ok"] is True

    for intent in ("planning", "schedule_adjustment", "organizing", "reviewing", "teaching", "assessment", "error_analysis"):
        context = _loads(
            handle_study_activity(
                {
                    "resource": "prompt_context",
                    "action": "load",
                    "vault_path": str(vault),
                    "project_id": "general-2027",
                    "data": {"intent": intent},
                }
            )
        )
        assert context["ok"] is True, intent
        assert {fragment["kind"] for fragment in context["data"]["fragments"]} == {"base", "intent"}
        assert context["data"]["total_char_count"] <= 6000

    for project_id, domain_pack in (
        ("kaoyan-2027", "kaoyan.v1"),
        ("ai-infra", "engineering.v1"),
        ("research-agents", "research.v1"),
    ):
        project = _loads(
            handle_study_activity(
                {
                    "resource": "project",
                    "action": "init",
                    "vault_path": str(vault),
                    "data": {"project_id": project_id, "domain_pack": domain_pack},
                }
            )
        )
        context = _loads(
            handle_study_activity(
                {
                    "resource": "prompt_context",
                    "action": "load",
                    "vault_path": str(vault),
                    "project_id": project_id,
                    "data": {"intent": "reviewing"},
                }
            )
        )

        assert project["ok"] is True
        assert context["ok"] is True
        assert {fragment["kind"] for fragment in context["data"]["fragments"]} == {"base", "intent", "domain"}
        assert context["data"]["total_char_count"] <= 6000


def test_study_os_registers_modular_skills(monkeypatch):
    from hermes_cli import plugins as plugins_mod
    from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest
    from plugins import study_os
    from tools.registry import registry

    manager = PluginManager()
    monkeypatch.setattr(plugins_mod, "_plugin_manager", manager)
    manifest = PluginManifest(name="study_os", version="0.1.0", description="study", source="bundled")
    ctx = PluginContext(manifest, manager)

    try:
        study_os.register(ctx)
        for name in (
            "study-os",
            "study-plan",
            "study-organize",
            "study-review",
            "study-teach",
            "study-lesson",
            "study-assessment",
            "study-kaoyan",
            "study-engineering",
            "study-research",
            "study-grill",
        ):
            assert manager.find_plugin_skill(f"study_os:{name}") is not None
    finally:
        for name in ("study_activity", "study_coach"):
            registry.deregister(name)


def test_study_os_skill_descriptions_and_budgets(monkeypatch):
    from hermes_cli import plugins as plugins_mod
    from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest
    from plugins import study_os
    from tools.registry import registry

    manager = PluginManager()
    monkeypatch.setattr(plugins_mod, "_plugin_manager", manager)
    manifest = PluginManifest(name="study_os", version="0.1.0", description="study", source="bundled")
    ctx = PluginContext(manifest, manager)

    try:
        study_os.register(ctx)
        expected = {
            "study-os": ("Route StudyOS learning workflows.", 6000),
            "study-plan": ("Create, revise, and persist StudyOS learning schedules.", 9000),
            "study-organize": ("Organize problems into StudyOS notes.", 9000),
            "study-review": ("Run flexible StudyOS spaced-repetition reviews.", 9000),
            "study-teach": ("Teach through StudyOS learning records.", 9000),
            "study-lesson": ("Create visual StudyOS lesson artifacts.", 9000),
            "study-assessment": ("Analyze StudyOS exams and mistakes.", 9000),
            "study-kaoyan": ("Guide 考研 learning with StudyOS.", 9000),
            "study-engineering": ("Guide engineering and skill learning with StudyOS.", 9000),
            "study-research": ("Guide research and replication learning with StudyOS.", 9000),
            "study-grill": ("Bridge grilling sessions into StudyOS decisions.", 9000),
        }
        all_text = ""
        for name, (description, max_chars) in expected.items():
            entry = manager._plugin_skills[f"study_os:{name}"]
            assert entry["description"] == description
            assert len(description) <= 60
            assert description.endswith(".")
            assert description.count(".") == 1
            body = Path(entry["path"]).read_text(encoding="utf-8")
            all_text += body
            assert len(body) <= max_chars, name
            assert "study_activity" in body
            assert "mutate system prompts" in body
            if name == "study-plan":
                assert "Long-term roadmaps belong in `phases`" in body
                assert "`events` may be empty" in body
                assert "effort_minutes" in body
                assert ".StudyOS/plans/" in body
                assert "Continue in the same turn" in body
                assert "study_activity` is" in body
        for term in ("艾宾浩斯", "整理", "错题", "weekly", "curriculum", "考研", "engineering", "research", "LearningDecisionRecord", "LearningRecord", "VisualLesson"):
            assert term in all_text
    finally:
        for name in ("study_activity", "study_coach"):
            registry.deregister(name)


def _register_study_os_against(skills_root: Path, monkeypatch):
    """Run ``study_os.register`` against a writable copy of the shipped skills."""

    from hermes_cli import plugins as plugins_mod
    from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest
    from plugins import study_os
    from tools.registry import registry

    real_root = Path(__file__).resolve().parents[2] / "plugins" / "study_os" / "skills"
    for source in sorted(real_root.glob("*/SKILL.md")):
        target = skills_root / source.parent.name / "SKILL.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    manager = PluginManager()
    monkeypatch.setattr(plugins_mod, "_plugin_manager", manager)
    monkeypatch.setattr(study_os, "_SKILLS_ROOT", skills_root)
    manifest = PluginManifest(name="study_os", version="0.1.0", description="study", source="bundled")

    def _run():
        try:
            study_os.register(PluginContext(manifest, manager))
        finally:
            for name in ("study_activity", "study_coach"):
                registry.deregister(name)

    return manager, _run


def _rewrite_frontmatter(path: Path, **fields: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    for index, line in enumerate(lines):
        key = line.split(":", 1)[0]
        if key in fields:
            lines[index] = f"{key}: {fields[key]}\n"
    path.write_text("".join(lines), encoding="utf-8")


def test_study_os_registration_is_all_or_nothing(tmp_path: Path, monkeypatch):
    """Half-registered is the worst state: live tools and hooks, dead routing.

    study-review is the fourth of eleven registrations, so validating inside the
    registration loop left three skills routable, both pre_llm_call hooks
    appended and study_activity/study_coach in the global registry -- while the
    plugin manager caught the exception and recorded study_os as failed with no
    tools at all.
    """

    from tools.registry import registry

    skills_root = tmp_path / "skills"
    manager, run = _register_study_os_against(skills_root, monkeypatch)
    _rewrite_frontmatter(skills_root / "study-review" / "SKILL.md", name="study-reviews")

    with pytest.raises(ValueError) as excinfo:
        run()

    assert "'study-review'" in str(excinfo.value)
    assert manager._plugin_skills == {}
    assert manager._hooks.get("pre_llm_call", []) == []
    assert registry.get_entry("study_activity") is None
    assert registry.get_entry("study_coach") is None


def test_study_os_registers_a_description_longer_than_the_routing_index_clips(
    tmp_path: Path, monkeypatch
):
    """The 60-character clip belongs to a code path plugin skills never take.

    ``agent.skill_utils.extract_skill_description`` clips at 60, but its only
    caller indexes the flat skills tree plus the configured external dirs, and
    plugin skills enter neither. Rejecting a long description here traded a
    truncation that cannot happen for a plugin that refuses to load.
    """

    skills_root = tmp_path / "skills"
    manager, run = _register_study_os_against(skills_root, monkeypatch)
    long_description = "Guide 考研 learning with StudyOS across every 科目 and every stage."
    assert len(long_description) > 60
    _rewrite_frontmatter(skills_root / "study-kaoyan" / "SKILL.md", description=long_description)

    run()

    assert len(manager._plugin_skills) == 11
    assert manager._plugin_skills["study_os:study-kaoyan"]["description"] == long_description


def test_study_os_registration_rejects_a_frontmatter_name_that_disagrees(
    tmp_path: Path, monkeypatch
):
    skills_root = tmp_path / "skills"
    _manager, run = _register_study_os_against(skills_root, monkeypatch)
    _rewrite_frontmatter(skills_root / "study-teach" / "SKILL.md", name="study-teaching")

    with pytest.raises(ValueError) as excinfo:
        run()

    assert "'study-teach'" in str(excinfo.value)


def test_study_os_registration_still_rejects_a_bad_domain_pack_skill(
    tmp_path: Path, monkeypatch
):
    skills_root = tmp_path / "skills"
    _manager, run = _register_study_os_against(skills_root, monkeypatch)
    _rewrite_frontmatter(skills_root / "study-kaoyan" / "SKILL.md", description="")

    with pytest.raises(ValueError) as excinfo:
        run()

    assert "DomainPack kaoyan.v1 prompt skill" in str(excinfo.value)


def test_study_os_routing_descriptions_come_from_the_skill_frontmatter(
    tmp_path: Path, monkeypatch
):
    """No second copy of the description: editing the file moves the routing."""

    skills_root = tmp_path / "skills"
    manager, run = _register_study_os_against(skills_root, monkeypatch)
    _rewrite_frontmatter(
        skills_root / "study-organize" / "SKILL.md",
        description="Sort StudyOS problems into notes.",
    )

    run()

    assert (
        manager._plugin_skills["study_os:study-organize"]["description"]
        == "Sort StudyOS problems into notes."
    )


def test_study_os_teaching_guide_discloses_the_model_lifecycle_contract(vault: Path):
    skills_root = Path("plugins/study_os/skills")
    teaching = (skills_root / "study-teach" / "SKILL.md").read_text(encoding="utf-8")
    assert _init_project(vault, "general-2027", "general.v1")["ok"] is True
    context = _load_prompt_context(vault, "general-2027", intent="teaching")
    guide = json.dumps(context["data"]["operation_guide"], ensure_ascii=False)

    for field in (
        "session_id",
        "mode",
        "objective",
        "time_budget_minutes",
        "assistance_level",
        "evidence_targets",
        "response",
        "result",
        "evaluator",
    ):
        assert field in guide
    for action in ("start", "advance", "snapshot|finish"):
        assert action in guide
    assert "study_coach.advance" in teaching
    assert "active Session" in teaching


def test_study_review_skill_documents_bounded_yaml_tag_selection():
    review = Path("plugins/study_os/skills/study-review/SKILL.md").read_text(encoding="utf-8")

    for term in ("YAML tag", "limit", "exclude_paths", "available_count"):
        assert term in review
    assert "never broaden" in review.casefold()
    # No whole-file size guard here on purpose: prose below the end marker never
    # reaches the model, so bounding the document would recreate the very cliff
    # the marked-region loader removed. The shared 9000-char guard in
    # test_study_os_skill_descriptions_and_budgets still covers this file.


def test_study_review_skill_documents_automatic_review_levels():
    review = Path("plugins/study_os/skills/study-review/SKILL.md").read_text(encoding="utf-8")

    assert "Do not ask for confidence or a review level" in review
    assert "incorrect → Lv.1" in review
    assert "partial → Lv.2" in review
    assert "correct → at least Lv.3" in review


def test_study_review_guide_exposes_atomic_submission_fields(vault: Path):
    assert _init_project(vault, "general-2027", "general.v1")["ok"] is True
    context = _load_prompt_context(vault, "general-2027", intent="reviewing")
    submit = next(
        operation
        for operation in context["data"]["operation_guide"]
        if operation.get("operation") == "review.submit"
    )

    assert submit["data_fields"][:4] == [
        "note",
        "response",
        "result",
        "duration_seconds",
    ]
    assert {"hints_used?", "evaluator?", "assistance?", "diagnoses?"} <= set(
        submit["data_fields"]
    )


def test_study_review_skill_keeps_multistep_answers_open_until_completion():
    review = Path("plugins/study_os/skills/study-review/SKILL.md").read_text(encoding="utf-8")
    contract = " ".join(review.split())

    assert "one coherent retrieval task at a time" in contract
    assert "learner determine when their response is complete" in contract
    assert "Completion, correctness, and verification strength" in contract
    assert "Evaluate the accumulated response against the learning objective" in contract
    assert "call `review.submit` once" in contract
    assert "completion action, never a checkpoint action" in contract


def test_study_review_stopping_preserves_accumulated_evidence():
    review = Path("plugins/study_os/skills/study-review/SKILL.md").read_text(encoding="utf-8")
    contract = " ".join(review.split())

    assert "Stopping closes future work, not prior evidence" in contract
    assert "not the percentage of requested steps completed" in contract
    assert "no evaluable response exists" in contract
    assert "learner explicitly discards it" in contract


def test_study_review_skill_submits_clear_complete_answers_without_confirmation():
    review = Path("plugins/study_os/skills/study-review/SKILL.md").read_text(encoding="utf-8")
    contract = " ".join(review.split())

    assert "Do not grade before completion" in contract
    assert "demand another confirmation afterwards" in contract
    assert "Offer the next item as an option, not an obligation" in contract


def test_study_review_and_teaching_preserve_learner_control():
    skills_root = Path("plugins/study_os/skills")
    review = (skills_root / "study-review" / "SKILL.md").read_text(encoding="utf-8")
    teaching = (skills_root / "study-teach" / "SKILL.md").read_text(encoding="utf-8")
    router = (skills_root / "study-os" / "SKILL.md").read_text(encoding="utf-8")

    for term in ("learner determine", "independent judgments", "Stopping closes future work"):
        assert term in review
    for term in ("controls depth, pace, assistance, and stopping", "fixed dialogue pattern", "ready_to_finish"):
        assert term in teaching
    assert "never interrupt review or teaching" in router
    assert "interaction completion" in router.casefold()
    assert "never continue solely to strengthen" in router


def test_study_prompt_fragments_state_principles_without_modality_examples():
    from plugins.study_os.prompt_budget import extract_prompt_fragment

    skills_root = Path("plugins/study_os/skills")
    fragments = []
    for skill_name in ("study-os", "study-review", "study-teach"):
        text = (skills_root / skill_name / "SKILL.md").read_text(encoding="utf-8")
        fragment, warning = extract_prompt_fragment(text, source=skill_name)
        assert warning is None
        fragments.append(fragment.casefold())

    prompt = "\n".join(fragments)
    for overfit_example in ("paper", "photo", "self-report", "纸上", "自拍"):
        assert overfit_example not in prompt
    assert "interaction completion" in prompt
    assert "evidence verification" in prompt


def test_study_organize_skill_uses_three_layers_and_atomic_save():
    organize = Path("plugins/study_os/skills/study-organize/SKILL.md").read_text(
        encoding="utf-8"
    )
    contract = " ".join(organize.split())

    for layer in ("**Capture**", "**Synthesize**", "**Curate**"):
        assert layer in organize
    assert "requested outcome, scope, and reversibility" in organize
    assert "request for a persisted note authorizes that scoped write" in contract.casefold()
    assert "`note.save` validates links and saves atomically" in contract
    assert "reserve `note.validate` for previews or higher-risk batches" in contract


def test_atomic_review_submission_does_not_share_learning_session_evidence_ownership():
    from plugins.study_os.learning import STUDY_ACTIVITY_SCHEMA

    router = Path("plugins/study_os/skills/study-os/SKILL.md").read_text(encoding="utf-8")
    review = Path("plugins/study_os/skills/study-review/SKILL.md").read_text(encoding="utf-8")
    activity_contract = str(STUDY_ACTIVITY_SCHEMA["description"])

    assert "Use one evidence owner" in router
    assert "review.submit owns both evidence and spacing" in activity_contract
    assert "start or advance a Learning\nSession" in review


def test_study_toolset_is_opt_in():
    from hermes_cli.tools_config import _DEFAULT_OFF_TOOLSETS
    from toolsets import TOOLSETS, _HERMES_CORE_TOOLS

    expected_tools = ["study_activity", "study_coach"]

    assert TOOLSETS["study"]["tools"] == expected_tools
    assert "study" in _DEFAULT_OFF_TOOLSETS
    for tool in expected_tools:
        assert tool not in _HERMES_CORE_TOOLS


def test_study_model_interface_keeps_action_payloads_behind_prompt_disclosure():
    from plugins.study_os.learning import STUDY_ACTIVITY_SCHEMA, STUDY_COACH_SCHEMA

    for schema in (STUDY_ACTIVITY_SCHEMA, STUDY_COACH_SCHEMA):
        data_schema = schema["parameters"]["properties"]["data"]
        assert set(data_schema) == {"type", "description"}
        assert data_schema["type"] == "object"
    assert "operation guide" in STUDY_ACTIVITY_SCHEMA["description"]
    assert "workflow guide" in STUDY_COACH_SCHEMA["description"]


def test_study_operation_guides_disclose_diagnosis_fields(vault: Path):
    assert _init_project(vault, "general-2027", "general.v1")["ok"] is True
    assessment = _load_prompt_context(vault, "general-2027", intent="assessment")
    teaching = _load_prompt_context(vault, "general-2027", intent="teaching")

    guide = json.dumps(
        assessment["data"]["operation_guide"] + teaching["data"]["operation_guide"],
        ensure_ascii=False,
    )
    assert "diagnoses?" in guide
    assert "observation{response,result,evaluator" in guide


def test_study_review_guide_exposes_queue_selectors(vault: Path):
    assert _init_project(vault, "general-2027", "general.v1")["ok"] is True
    context = _load_prompt_context(vault, "general-2027", intent="reviewing")
    due = next(
        operation
        for operation in context["data"]["operation_guide"]
        if operation.get("operation") == "review.due"
    )

    assert {
        "notes?",
        "subjects?",
        "tags?",
        "concepts?",
        "difficulties?",
        "review_levels?",
        "review_state?",
        "match?",
        "sort?",
        "limit?",
        "exclude_paths?",
    } == set(due["data_fields"])


def test_study_organizing_guide_and_backend_own_closed_wikilink_saves(vault: Path):
    from plugins.study_os.learning import STUDY_ACTIVITY_SCHEMA

    description = STUDY_ACTIVITY_SCHEMA["description"]
    router = Path("plugins/study_os/skills/study-os/SKILL.md").read_text(
        encoding="utf-8"
    )
    organize = Path(
        "plugins/study_os/skills/study-organize/SKILL.md"
    ).read_text(encoding="utf-8")
    assert _init_project(vault, "general-2027", "general.v1")["ok"] is True
    context = _load_prompt_context(vault, "general-2027", intent="organizing")
    guide = json.dumps(context["data"]["operation_guide"], ensure_ascii=False)

    assert "Canonical save actions validate before writing" in description
    assert "note.save|validate" in guide
    assert "notes[{path,content,overwrite?}]" in guide
    assert "never use generic writes" in router.casefold()
    assert "Never use a generic file-writing tool" in organize


def test_study_coach_backend_validates_lifecycle_payload_after_disclosure(vault: Path):
    from plugins.study_os.learning import handle_study_coach

    assert _init_project(vault, "general-2027", "general.v1")["ok"] is True
    rejected = _loads(
        handle_study_coach(
            {
                "action": "start",
                "vault_path": str(vault),
                "project_id": "general-2027",
                "data": {
                    "session_id": "invalid-contract-001",
                    "contract": {
                        "objective": "Translate distance conditions into surface equations.",
                    },
                },
            }
        )
    )

    assert rejected["ok"] is False
    assert rejected["error"]["code"] == "VALIDATION_FAILED"
    for field in ("mode", "time_budget_minutes", "assistance_level", "evidence_targets"):
        assert field in rejected["error"]["message"]


def test_study_activity_diagnosis_error_explains_how_to_retry(vault: Path):
    from plugins.study_os.learning import handle_study_activity

    initialized = _loads(
        handle_study_activity(
            {
                "resource": "project",
                "action": "init",
                "vault_path": str(vault),
                "data": {"project_id": "calculus-2027"},
            }
        )
    )
    invalid_data = {
        "item_id": "derivative-sign-01",
        "response": "Divided without checking the sign.",
        "result": "incorrect",
        "diagnoses": ["condition_missed"],
    }
    rejected = _loads(
        handle_study_activity(
            {
                "resource": "attempt",
                "action": "record",
                "vault_path": str(vault),
                "project_id": "calculus-2027",
                "data": invalid_data,
            }
        )
    )

    assert initialized["ok"] is True
    assert rejected["error"]["code"] == "VALIDATION_FAILED"
    assert "kind" in rejected["error"]["message"]
    assert "evidence" in rejected["error"]["message"]

    corrected = _loads(
        handle_study_activity(
            {
                "resource": "attempt",
                "action": "record",
                "vault_path": str(vault),
                "project_id": "calculus-2027",
                "data": {
                    **invalid_data,
                    "diagnoses": [
                        {
                            "kind": "condition_missed",
                            "evidence": "The divisor sign was not established.",
                        }
                    ],
                },
            }
        )
    )

    assert corrected["ok"] is True


def test_study_activity_records_and_queries_immutable_attempts(vault: Path):
    from plugins.study_os.learning import handle_study_activity

    initialized = _loads(
        handle_study_activity(
            {
                "resource": "project",
                "action": "init",
                "vault_path": str(vault),
                "data": {"project_id": "calculus-2027"},
            }
        )
    )
    recorded = _loads(
        handle_study_activity(
            {
                "resource": "attempt",
                "action": "record",
                "vault_path": str(vault),
                "project_id": "calculus-2027",
                "data": {
                    "attempt_id": "att-derivative-001",
                    "item_id": "derivative-sign-01",
                    "occurred_at": "2026-07-12T10:00:00+08:00",
                    "response": "Divided by an expression without checking its sign.",
                    "result": "incorrect",
                    "score": 0.2,
                    "transfer_level": "execution",
                    "concepts": ["函数单调性"],
                    "patterns": ["含参导数符号判断"],
                    "diagnoses": [
                        {
                            "kind": "condition_missed",
                            "concept": "函数单调性",
                            "evidence": "The sign of the divisor was never established.",
                        }
                    ],
                },
            }
        )
    )
    listed = _loads(
        handle_study_activity(
            {
                "resource": "attempt",
                "action": "list",
                "vault_path": str(vault),
                "project_id": "calculus-2027",
                "data": {"concept": "函数单调性"},
            }
        )
    )
    duplicate = _loads(
        handle_study_activity(
            {
                "resource": "attempt",
                "action": "record",
                "vault_path": str(vault),
                "project_id": "calculus-2027",
                "data": recorded["data"]["attempt"],
            }
        )
    )

    assert initialized["ok"] is True
    assert recorded["ok"] is True
    assert recorded["data"]["path"].endswith("activity/attempts-2026-07.jsonl")
    assert listed["data"]["count"] == 1
    assert listed["data"]["attempts"][0]["response"].startswith("Divided")
    assert duplicate["ok"] is False
    assert duplicate["error"]["code"] == "ATTEMPT_EXISTS"


def test_attempt_evidence_preserves_evaluator_provenance(vault: Path):
    from plugins.study_os.learning import handle_study_activity

    initialized = _loads(
        handle_study_activity(
            {
                "resource": "project",
                "action": "init",
                "vault_path": str(vault),
                "data": {"project_id": "calculus-2027"},
            }
        )
    )
    assert initialized["ok"] is True

    recorded = _loads(
        handle_study_activity(
            {
                "resource": "attempt",
                "action": "record",
                "vault_path": str(vault),
                "project_id": "calculus-2027",
                "data": {
                    "attempt_id": "att-self-grade-001",
                    "item_id": "derivative-001",
                    "occurred_at": "2026-07-13T10:00:00+08:00",
                    "response": "Use the definition of the derivative.",
                    "result": "correct",
                    "score": 1.0,
                    "transfer_level": "execution",
                    "concepts": ["导数"],
                    "evaluator": {"kind": "self", "confidence": 0.45},
                    "assistance": {"level": "independent", "hints_used": 0},
                    "objective_ids": ["derive-from-definition"],
                },
            }
        )
    )

    assert recorded["ok"] is True
    assert recorded["data"]["attempt"]["evaluator"] == {"kind": "self", "confidence": 0.45}
    assert recorded["data"]["attempt"]["assistance"]["level"] == "independent"
    assert recorded["data"]["attempt"]["objective_ids"] == ["derive-from-definition"]


def test_study_coach_uses_evidence_for_summary_recommendation_and_pattern_proposal(vault: Path):
    from plugins.study_os.learning import handle_study_activity, handle_study_coach

    init = _loads(
        handle_study_activity(
            {"resource": "project", "action": "init", "vault_path": str(vault), "data": {"project_id": "calculus-2027"}}
        )
    )
    assert init["ok"] is True
    for index in range(1, 3):
        result = _loads(
            handle_study_activity(
                {
                    "resource": "attempt",
                    "action": "record",
                    "vault_path": str(vault),
                    "project_id": "calculus-2027",
                    "data": {
                        "attempt_id": f"att-condition-{index:03d}",
                        "item_id": f"item-{index}",
                        "occurred_at": f"2026-07-1{index}T10:00:00+08:00",
                        "response": "Applied the routine before checking the condition.",
                        "result": "incorrect",
                        "score": 0.0,
                        "transfer_level": "execution",
                        "concepts": ["函数单调性"],
                        "patterns": ["含参导数符号判断"],
                        "diagnoses": [
                            {
                                "kind": "condition_missed",
                                "concept": "函数单调性",
                                "evidence": "The required sign condition was omitted.",
                            }
                        ],
                    },
                }
            )
        )
        assert result["ok"] is True

    summary = _loads(
        handle_study_coach(
            {"action": "summarize", "scope": "project", "vault_path": str(vault), "project_id": "calculus-2027"}
        )
    )
    recommendation = _loads(
        handle_study_coach(
            {"action": "recommend", "vault_path": str(vault), "project_id": "calculus-2027"}
        )
    )
    proposed = _loads(
        handle_study_coach(
            {"action": "propose_pattern", "vault_path": str(vault), "project_id": "calculus-2027"}
        )
    )
    probe = _loads(
        handle_study_coach(
            {"action": "generate_probe", "vault_path": str(vault), "project_id": "calculus-2027"}
        )
    )

    assert summary["data"]["summary"]["attempt_count"] == 2
    assert summary["data"]["summary"]["evidence_attempt_ids"] == ["att-condition-001", "att-condition-002"]
    assert "transfer" in summary["data"]["summary"]["unverified"]
    assert summary["data"]["summary"]["attempt_count"] == 2
    assert recommendation["data"]["diagnosis"]["evidence_dimensions"]["execution"]["attempt_count"] == 2
    interventions = {item["intervention"] for item in recommendation["data"]["recommendations"]}
    assert {"misconception_probe", "near_transfer_probe"} <= interventions
    proposal = proposed["data"]["proposals"][0]
    assert proposal["status"] == "candidate"
    assert proposal["evidence_attempt_ids"] == ["att-condition-001", "att-condition-002"]
    assert probe["data"]["probe_blueprint"]["target_concept"] == "函数单调性"
    assert probe["data"]["probe_blueprint"]["response_policy"].startswith("ask for")
    assert not (vault / ".StudyOS" / "projects" / "calculus-2027" / "pattern-proposals").exists()

    saved = _loads(
        handle_study_activity(
            {
                "resource": "pattern_proposal",
                "action": "save",
                "vault_path": str(vault),
                "project_id": "calculus-2027",
                "data": {"proposal": proposal},
            }
        )
    )
    assert saved["ok"] is True
    assert saved["data"]["path"].endswith(f"pattern-proposals/{proposal['proposal_id']}.json")


def test_learning_runtime_runs_one_evidence_backed_session(vault: Path):
    from plugins.study_os.learning import handle_study_activity, handle_study_coach

    project = _valid_learning_project_v2()
    initialized = _loads(
        handle_study_activity(
            {
                "resource": "project",
                "action": "init",
                "vault_path": str(vault),
                "data": {
                    key: value
                    for key, value in project.items()
                    if key not in {"prompt_policy", "created_at", "updated_at"}
                },
            }
        )
    )
    assert initialized["ok"] is True

    started = _loads(
        handle_study_coach(
            {
                "action": "start",
                "vault_path": str(vault),
                "project_id": "research-agents",
                "data": {
                    "session_id": "learn-routing-001",
                    "contract": {
                        "contract_id": "contract-routing-001",
                        "mode": "research",
                        "objective": "Reproduce and explain the routing result.",
                        "objective_ids": ["reproduce-routing-result"],
                        "time_budget_minutes": 45,
                        "assistance_level": "guided",
                        "evidence_targets": ["execution", "explanation"],
                    },
                },
            },
            session_id="hermes-conversation-001",
        )
    )

    assert started["ok"] is True
    assert started["data"]["session"]["status"] == "active"
    assert started["data"]["session"]["conversation_session_id"] == "hermes-conversation-001"
    assert started["data"]["session"]["evidence_ids"] == []
    assert started["data"]["next_activity"]["evidence_target"] == "execution"
    assert started["data"]["next_activity"]["kind"] == "research_replication"
    assert started["data"]["next_activity"]["activity_adapter"] == "research.v1"
    assert started["data"]["competency_snapshot"]["evidence_count"] == 0
    assert (vault / ".StudyOS" / "projects" / "research-agents" / "sessions" / "learn-routing-001.json").exists()

    advanced = _loads(
        handle_study_coach(
            {
                "action": "advance",
                "vault_path": str(vault),
                "project_id": "research-agents",
                "data": {
                    "session_id": "learn-routing-001",
                    "observation": {
                        "attempt_id": "att-routing-001",
                        "response": "I reproduced the command but could not explain why the router changed.",
                        "result": "partial",
                        "score": 0.5,
                        "duration_seconds": 480,
                        "concepts": ["routing"],
                        "artifact_refs": ["command:python reproduce_routing.py", "result:routing-run-001.json"],
                        "diagnoses": [
                            {
                                "kind": "explanation_gap",
                                "concept": "routing",
                                "evidence": "The mechanism was not explained.",
                            }
                        ],
                        "evaluator": {"kind": "agent", "id": "primary", "confidence": 0.8},
                    },
                },
            }
        )
    )

    assert advanced["ok"] is True
    assert advanced["data"]["evidence"]["attempt_id"] == "att-routing-001"
    assert advanced["data"]["session"]["evidence_ids"] == ["att-routing-001"]
    assert advanced["data"]["competency_snapshot"]["dimensions"]["execution"]["attempt_count"] == 1
    assert advanced["data"]["competency_snapshot"]["dimensions"]["execution"]["verification_status"] == "developing"
    assert advanced["data"]["competency_snapshot"]["evaluator_provenance"] == {"agent": 1}
    assert advanced["data"]["evidence"]["artifact_refs"][0].startswith("command:")
    assert advanced["data"]["next_activity"]["kind"] == "research_mechanism_explanation"
    assert advanced["data"]["next_activity"]["reason"]

    snapshot = _loads(
        handle_study_coach(
            {
                "action": "snapshot",
                "vault_path": str(vault),
                "project_id": "research-agents",
                "data": {"session_id": "learn-routing-001"},
            }
        )
    )
    finished = _loads(
        handle_study_coach(
            {
                "action": "finish",
                "vault_path": str(vault),
                "project_id": "research-agents",
                "data": {"session_id": "learn-routing-001"},
            }
        )
    )

    assert snapshot["data"]["competency_snapshot"]["evidence_count"] == 1
    assert finished["ok"] is True
    assert finished["data"]["session"]["status"] == "completed"
    assert finished["data"]["outcome"]["evidence_count"] == 1
    assert "explanation" in finished["data"]["outcome"]["unverified_dimensions"]


def test_learning_runtime_stops_adding_activities_at_the_time_budget(vault: Path):
    from plugins.study_os.learning import handle_study_activity, handle_study_coach

    initialized = _loads(
        handle_study_activity(
            {
                "resource": "project",
                "action": "init",
                "vault_path": str(vault),
                "data": {"project_id": "calculus-2027"},
            }
        )
    )
    started = _loads(
        handle_study_coach(
            {
                "action": "start",
                "vault_path": str(vault),
                "project_id": "calculus-2027",
                "data": {
                    "session_id": "learn-budget-001",
                    "contract": {
                        "mode": "learn",
                        "objective": "Explain and apply the derivative definition.",
                        "time_budget_minutes": 1,
                        "assistance_level": "guided",
                        "evidence_targets": ["explanation", "near_transfer"],
                    },
                },
            }
        )
    )
    advanced = _loads(
        handle_study_coach(
            {
                "action": "advance",
                "vault_path": str(vault),
                "project_id": "calculus-2027",
                "data": {
                    "session_id": "learn-budget-001",
                    "observation": {
                        "response": "The derivative is the limit of the difference quotient.",
                        "result": "correct",
                        "duration_seconds": 60,
                        "evaluator": {"kind": "agent", "confidence": 0.8},
                    },
                },
            }
        )
    )

    assert initialized["ok"] is True
    assert started["ok"] is True
    assert advanced["ok"] is True
    assert advanced["data"]["next_activity"] is None
    assert advanced["data"]["continuation"] == {
        "state": "ready_to_finish",
        "reason": "time_budget_reached",
        "observed_evidence_targets": ["explanation"],
        "pending_evidence_targets": ["near_transfer"],
        "elapsed_activity_seconds": 60,
        "time_budget_seconds": 60,
        "learner_controls_follow_up": True,
    }


def test_learning_runtime_rejects_unprovenanced_or_post_finish_evidence(vault: Path):
    from plugins.study_os.learning import handle_study_activity, handle_study_coach

    initialized = _loads(
        handle_study_activity(
            {
                "resource": "project",
                "action": "init",
                "vault_path": str(vault),
                "data": {"project_id": "calculus-2027"},
            }
        )
    )
    assert initialized["ok"] is True
    started = _loads(
        handle_study_coach(
            {
                "action": "start",
                "vault_path": str(vault),
                "project_id": "calculus-2027",
                "data": {
                    "session_id": "learn-calculus-001",
                    "contract": {
                        "mode": "learn",
                        "objective": "Explain the derivative from its definition.",
                        "objective_ids": [],
                        "time_budget_minutes": 20,
                        "assistance_level": "hints_only",
                        "evidence_targets": ["explanation"],
                    },
                },
            }
        )
    )
    assert started["ok"] is True

    missing_evaluator = _loads(
        handle_study_coach(
            {
                "action": "advance",
                "vault_path": str(vault),
                "project_id": "calculus-2027",
                "data": {
                    "session_id": "learn-calculus-001",
                    "observation": {
                        "response": "A derivative is a limit.",
                        "result": "partial",
                        "score": 0.5,
                    },
                },
            }
        )
    )
    assert missing_evaluator["ok"] is False
    assert missing_evaluator["error"]["code"] == "EVALUATOR_REQUIRED"

    finished = _loads(
        handle_study_coach(
            {
                "action": "finish",
                "vault_path": str(vault),
                "project_id": "calculus-2027",
                "data": {"session_id": "learn-calculus-001"},
            }
        )
    )
    assert finished["ok"] is True

    after_finish = _loads(
        handle_study_coach(
            {
                "action": "advance",
                "vault_path": str(vault),
                "project_id": "calculus-2027",
                "data": {
                    "session_id": "learn-calculus-001",
                    "observation": {
                        "response": "A derivative is a limit.",
                        "result": "correct",
                        "score": 1.0,
                        "evaluator": {"kind": "agent", "confidence": 0.8},
                    },
                },
            }
        )
    )
    assert after_finish["ok"] is False
    assert after_finish["error"]["code"] == "SESSION_NOT_ACTIVE"


def test_engineering_adapter_requires_reproducible_artifacts_for_execution(vault: Path):
    from plugins.study_os.learning import handle_study_activity, handle_study_coach

    project = _valid_engineering_project_v2()
    initialized = _loads(
        handle_study_activity(
            {
                "resource": "project",
                "action": "init",
                "vault_path": str(vault),
                "data": {
                    key: value
                    for key, value in project.items()
                    if key not in {"prompt_policy", "created_at", "updated_at"}
                },
            }
        )
    )
    started = _loads(
        handle_study_coach(
            {
                "action": "start",
                "vault_path": str(vault),
                "project_id": "engine-runtime",
                "data": {
                    "session_id": "learn-runtime-001",
                    "contract": {
                        "mode": "execute",
                        "objective": "Trace and verify one request lifecycle through the runtime.",
                        "objective_ids": ["trace-request-lifecycle"],
                        "time_budget_minutes": 40,
                        "assistance_level": "guided",
                        "evidence_targets": ["execution"],
                    },
                },
            }
        )
    )

    assert initialized["ok"] is True
    assert started["ok"] is True
    activity = started["data"]["next_activity"]
    assert activity["activity_adapter"] == "engineering.v1"
    assert activity["kind"] == "engineering_execution"
    assert activity["source_anchors"][0]["ref"] == "run_agent.py"
    assert "artifact_refs" in activity["evidence_requirements"]

    missing_artifact = _loads(
        handle_study_coach(
            {
                "action": "advance",
                "vault_path": str(vault),
                "project_id": "engine-runtime",
                "data": {
                    "session_id": "learn-runtime-001",
                    "observation": {
                        "response": "The request passes through run_conversation into the provider loop.",
                        "result": "partial",
                        "score": 0.6,
                        "evaluator": {"kind": "agent", "confidence": 0.75},
                    },
                },
            }
        )
    )
    assert missing_artifact["ok"] is False
    assert missing_artifact["error"]["code"] == "ARTIFACT_REFERENCE_REQUIRED"

    advanced = _loads(
        handle_study_coach(
            {
                "action": "advance",
                "vault_path": str(vault),
                "project_id": "engine-runtime",
                "data": {
                    "session_id": "learn-runtime-001",
                    "observation": {
                        "attempt_id": "att-runtime-trace-001",
                        "response": "The request passes through run_conversation into the provider loop.",
                        "result": "correct",
                        "score": 0.9,
                        "concepts": ["request lifecycle"],
                        "artifact_refs": ["command:pytest tests/agent/test_turn_context.py", "file:run_agent.py"],
                        "evaluator": {"kind": "program", "id": "pytest", "confidence": 0.95},
                    },
                },
            }
        )
    )

    assert advanced["ok"] is True
    assert advanced["data"]["competency_snapshot"]["dimensions"]["execution"]["verification_status"] == "supported"
    assert advanced["data"]["evidence"]["artifact_refs"] == [
        "command:pytest tests/agent/test_turn_context.py",
        "file:run_agent.py",
    ]
    assert advanced["data"]["evidence"]["source_anchors"] == activity["source_anchors"]
    assert advanced["data"]["next_activity"] is None
    assert advanced["data"]["continuation"]["state"] == "ready_to_finish"
    assert advanced["data"]["continuation"]["reason"] == "contract_evidence_observed"
    assert advanced["data"]["recommendations"]


def test_research_adapter_requires_a_source_anchor_for_claim_evidence(vault: Path):
    from plugins.study_os.learning import handle_study_activity, handle_study_coach

    project = _valid_learning_project_v2()
    project["project_id"] = "research-unanchored"
    project["objectives"][0]["objective_id"] = "explain-routing-claim"
    project["objectives"][0].pop("source_anchors")
    initialized = _loads(
        handle_study_activity(
            {
                "resource": "project",
                "action": "init",
                "vault_path": str(vault),
                "data": {
                    key: value
                    for key, value in project.items()
                    if key not in {"prompt_policy", "created_at", "updated_at"}
                },
            }
        )
    )
    started = _loads(
        handle_study_coach(
            {
                "action": "start",
                "vault_path": str(vault),
                "project_id": "research-unanchored",
                "data": {
                    "session_id": "learn-claim-001",
                    "contract": {
                        "mode": "research",
                        "objective": "Explain the routing claim and its limitation.",
                        "objective_ids": ["explain-routing-claim"],
                        "time_budget_minutes": 30,
                        "assistance_level": "independent",
                        "evidence_targets": ["explanation"],
                    },
                },
            }
        )
    )

    assert initialized["ok"] is True
    assert started["data"]["next_activity"]["kind"] == "research_mechanism_explanation"
    rejected = _loads(
        handle_study_coach(
            {
                "action": "advance",
                "vault_path": str(vault),
                "project_id": "research-unanchored",
                "data": {
                    "session_id": "learn-claim-001",
                    "observation": {
                        "response": "The router changes because load changes.",
                        "result": "partial",
                        "score": 0.5,
                        "evaluator": {"kind": "agent", "confidence": 0.6},
                    },
                },
            }
        )
    )
    assert rejected["ok"] is False
    assert rejected["error"]["code"] == "SOURCE_ANCHOR_REQUIRED"

    accepted = _loads(
        handle_study_coach(
            {
                "action": "advance",
                "vault_path": str(vault),
                "project_id": "research-unanchored",
                "data": {
                    "session_id": "learn-claim-001",
                    "observation": {
                        "response": "The paper attributes the change to load, but section 4 does not isolate latency.",
                        "result": "correct",
                        "source_anchors": [
                            {"kind": "paper", "ref": "doi:10.0000/example", "locator": "section 4"}
                        ],
                        "evaluator": {"kind": "human", "id": "supervisor", "confidence": 0.9},
                    },
                },
            }
        )
    )
    assert accepted["ok"] is True
    assert accepted["data"]["evidence"]["score"] == 1.0
    assert accepted["data"]["evidence"]["source_anchors"][0]["kind"] == "paper"
    # One anchored, unaided success is supporting evidence, not demonstrated
    # independence -- that needs a second showing.
    assert accepted["data"]["competency_snapshot"]["dimensions"]["explanation"]["verification_status"] == "supported"
    assert accepted["data"]["next_activity"] is None
    assert accepted["data"]["continuation"]["reason"] == "contract_evidence_observed"

    first_finished = _loads(
        handle_study_coach(
            {
                "action": "finish",
                "vault_path": str(vault),
                "project_id": "research-unanchored",
                "data": {"session_id": "learn-claim-001"},
            }
        )
    )
    second_started = _loads(
        handle_study_coach(
            {
                "action": "start",
                "vault_path": str(vault),
                "project_id": "research-unanchored",
                "data": {
                    "session_id": "learn-claim-002",
                    "contract": {
                        "mode": "research",
                        "objective": "Re-check the routing claim after spacing.",
                        "objective_ids": ["explain-routing-claim"],
                        "time_budget_minutes": 30,
                        "assistance_level": "independent",
                        "evidence_targets": ["explanation"],
                    },
                },
            }
        )
    )

    confirmed = _loads(
        handle_study_coach(
            {
                "action": "advance",
                "vault_path": str(vault),
                "project_id": "research-unanchored",
                "data": {
                    "session_id": "learn-claim-002",
                    "observation": {
                        "response": "Section 5's ablation isolates latency and reproduces the effect without load.",
                        "result": "correct",
                        "source_anchors": [
                            {"kind": "paper", "ref": "doi:10.0000/example", "locator": "section 5"}
                        ],
                        "evaluator": {"kind": "human", "id": "supervisor", "confidence": 0.9},
                    },
                },
            }
        )
    )
    assert first_finished["ok"] is True
    assert second_started["ok"] is True
    assert confirmed["ok"] is True
    assert confirmed["data"]["competency_snapshot"]["dimensions"]["explanation"]["verification_status"] == "independent"
    assert confirmed["data"]["next_activity"] is None

    finished = _loads(
        handle_study_coach(
            {
                "action": "finish",
                "vault_path": str(vault),
                "project_id": "research-unanchored",
                "data": {"session_id": "learn-claim-002"},
            }
        )
    )
    assert finished["data"]["outcome"]["verified_dimensions"] == ["explanation"]
    assert finished["data"]["outcome"]["unverified_dimensions"] == []


def test_active_learning_context_is_turn_local_bounded_and_removed_on_finish(vault: Path):
    from plugins.study_os.context import MAX_ACTIVE_CONTEXT_CHARS, active_learning_context
    from plugins.study_os.learning import handle_study_activity, handle_study_coach

    initialized = _loads(
        handle_study_activity(
            {
                "resource": "project",
                "action": "init",
                "vault_path": str(vault),
                "data": {"project_id": "calculus-2027"},
            }
        )
    )
    assert initialized["ok"] is True
    assert active_learning_context(session_id="hermes-learning-context-001") is None

    started = _loads(
        handle_study_coach(
            {
                "action": "start",
                "vault_path": str(vault),
                "project_id": "calculus-2027",
                "data": {
                    "session_id": "learn-context-001",
                    "conversation_session_id": "hermes-learning-context-001",
                    "contract": {
                        "mode": "learn",
                        "objective": "Explain the derivative from its definition.",
                        "objective_ids": [],
                        "time_budget_minutes": 20,
                        "assistance_level": "hints_only",
                        "evidence_targets": ["explanation"],
                    },
                },
            }
        )
    )
    active = active_learning_context(session_id="hermes-learning-context-001")

    assert started["ok"] is True
    assert active is not None
    assert set(active) == {"context"}
    assert "Explain the derivative from its definition." in active["context"]
    assert '"assistance_level":"hints_only"' in active["context"]
    assert "not proof of mastery" in active["context"]
    assert "Interaction completion and evidence verification are separate" in active["context"]
    assert "without erasing supported observations already produced" in active["context"]
    assert "paper" not in active["context"].casefold()
    assert len(active["context"]) <= MAX_ACTIVE_CONTEXT_CHARS

    finished = _loads(
        handle_study_coach(
            {
                "action": "finish",
                "vault_path": str(vault),
                "project_id": "calculus-2027",
                "data": {"session_id": "learn-context-001"},
            }
        )
    )
    assert finished["ok"] is True
    assert active_learning_context(session_id="hermes-learning-context-001") is None


def test_study_planning_context_requires_canonical_tool_persistence(
    vault: Path,
    monkeypatch,
):
    from plugins.study_os.context import study_planning_context
    from plugins.study_os.learning import handle_study_activity

    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(vault))
    initialized = _loads(
        handle_study_activity(
            {
                "resource": "project",
                "action": "init",
                "vault_path": str(vault),
                "data": {"project_id": "kaoyan-2027", "domain_pack": "kaoyan.v1"},
            }
        )
    )

    context = study_planning_context(
        user_message="请完成线性代数学习规划，并登记到 StudyOS 日历。"
    )

    assert initialized["ok"] is True
    assert context is not None
    prompt = context["context"]
    for required in (
        "project.status",
        "prompt_context.load",
        "schedule.validate",
        "schedule.save",
        "Markdown",
        "future tool call",
    ):
        assert required in prompt
    assert study_planning_context(user_message="帮我规划一次普通旅行。") is None
    assert study_planning_context(user_message="Explain this study result.") is None


def test_study_os_startup_injects_schedule_phases_active_on_current_date(
    vault: Path,
    monkeypatch,
):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from plugins.study_os.context import study_planning_context
    from plugins.study_os.learning import handle_study_activity

    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(vault))
    initialized = _loads(
        handle_study_activity(
            {
                "resource": "project",
                "action": "init",
                "vault_path": str(vault),
                "data": {"project_id": "kaoyan-2027"},
            }
        )
    )
    schedule = _valid_study_schedule()
    schedule["events"] = []
    saved = _loads(
        handle_study_activity(
            {
                "resource": "schedule",
                "action": "save",
                "vault_path": str(vault),
                "project_id": "kaoyan-2027",
                "data": schedule,
            }
        )
    )

    context = study_planning_context(
        user_message="/study-os",
        as_of=datetime(2026, 7, 18, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert initialized["ok"] is True
    assert saved["ok"] is True
    assert context is not None
    prompt = context["context"]
    for required in (
        "kaoyan-2027-master-plan",
        "2027 考研数学基础阶段计划",
        "基础阶段",
        "完成核心考点覆盖",
        "2026-07-01",
        "2026-09-30",
    ):
        assert required in prompt
    loaded_skill_context = study_planning_context(
        user_message=(
            '[IMPORTANT: The user has invoked the "study-os" skill, indicating they want '
            "you to follow its instructions.]"
        ),
        as_of=datetime(2026, 7, 18, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    assert loaded_skill_context is not None
    assert "基础阶段" in loaded_skill_context["context"]
    assert (
        study_planning_context(
            user_message="/study-os",
            as_of=datetime(2026, 6, 18, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        )
        is None
    )


def test_registered_learning_runtime_binds_and_unbinds_real_pre_llm_hook(vault: Path, monkeypatch):
    from hermes_cli import plugins as plugins_mod
    from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest
    from plugins import study_os
    from tools.registry import registry

    manager = PluginManager()
    monkeypatch.setattr(plugins_mod, "_plugin_manager", manager)
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(vault))
    manifest = PluginManifest(name="study_os", version="0.1.0", description="study", source="bundled")
    ctx = PluginContext(manifest, manager)

    try:
        study_os.register(ctx)
        initialized = _loads(
            registry.dispatch(
                "study_activity",
                {
                    "resource": "project",
                    "action": "init",
                    "vault_path": str(vault),
                    "data": {"project_id": "calculus-2027"},
                },
                session_id="hermes-e2e-learning-001",
            )
        )
        started = _loads(
            registry.dispatch(
                "study_coach",
                {
                    "action": "start",
                    "vault_path": str(vault),
                    "project_id": "calculus-2027",
                    "data": {
                        "session_id": "learn-e2e-001",
                        "contract": {
                            "mode": "learn",
                            "objective": "Explain the derivative from its definition.",
                            "objective_ids": [],
                            "time_budget_minutes": 20,
                            "assistance_level": "guided",
                            "evidence_targets": ["explanation"],
                        },
                    },
                },
                session_id="hermes-e2e-learning-001",
            )
        )
        hook_results = manager.invoke_hook(
            "pre_llm_call",
            session_id="hermes-e2e-learning-001",
            user_message="continue",
            conversation_history=[],
            is_first_turn=False,
            model="test",
        )

        assert initialized["ok"] is True
        assert started["ok"] is True
        assert len(hook_results) == 1
        assert "Explain the derivative from its definition." in hook_results[0]["context"]

        finished = _loads(
            registry.dispatch(
                "study_coach",
                {
                    "action": "finish",
                    "vault_path": str(vault),
                    "project_id": "calculus-2027",
                    "data": {"session_id": "learn-e2e-001"},
                },
                session_id="hermes-e2e-learning-001",
            )
        )
        assert finished["ok"] is True
        assert manager.invoke_hook("pre_llm_call", session_id="hermes-e2e-learning-001") == []
        planning_results = manager.invoke_hook(
            "pre_llm_call",
            session_id="hermes-e2e-learning-001",
            user_message="请把线性代数规划登记到 StudyOS 日历。",
        )
        assert len(planning_results) == 1
        assert "schedule.save" in planning_results[0]["context"]
    finally:
        for name in ("study_activity", "study_coach"):
            registry.deregister(name)


def test_review_runner_reads_hidden_answer_and_submits_one_compound_result(vault: Path):
    from plugins.study_os.learning import handle_study_review_detail, handle_study_review_submission
    from plugins.study_os.tools import handle_study_project

    initialized = _loads(handle_study_project({"vault_path": str(vault), "action": "init", "project_id": "calculus-2027"}))
    assert initialized["ok"] is True
    note = vault / "math" / "examples" / "derivative.md"
    note.parent.mkdir(parents=True)
    note.write_text(
        "---\n"
        "title: 导数符号判断\n"
        "type: example\n"
        "review_level: 2\n"
        "review_count: 1\n"
        "concepts: [函数单调性]\n"
        "patterns: [含参导数符号判断]\n"
        "---\n\n"
        "# 导数符号判断\n\n求函数的单调区间。\n\n## 解析\n\n先判断参数符号。\n",
        encoding="utf-8",
    )

    detail = _loads(handle_study_review_detail({"vault_path": str(vault), "note": "math/examples/derivative.md"}))
    submitted = _loads(
        handle_study_review_submission(
            {
                "vault_path": str(vault),
                "project_id": "calculus-2027",
                "note": "math/examples/derivative.md",
                "attempt_id": "att-review-runner-001",
                "occurred_at": "2026-07-12T10:00:00+08:00",
                "response": "先求导，再按参数符号分类讨论。",
                "result": "correct",
                "duration_seconds": 93,
                "transfer_level": "execution",
                "diagnoses": [],
            }
        )
    )

    assert detail["ok"] is True
    assert detail["data"]["prompt_markdown"].endswith("求函数的单调区间。")
    assert "先判断参数符号" not in detail["data"]["prompt_markdown"]
    assert detail["data"]["answer_markdown"].startswith("## 解析")
    assert submitted["ok"] is True
    assert submitted["data"]["attempt"]["item_id"] == "math/examples/derivative.md"
    assert "self_confidence" not in submitted["data"]["attempt"]
    assert submitted["data"]["review"]["review_level"] == {"old": 2, "new": 3}
    assert submitted["data"]["completed_today_increment"] == 1
    assert submitted["data"]["completed_today"] == 1
    updated = note.read_text(encoding="utf-8")
    assert "review_level: 3" in updated
    assert "review_count: 2" in updated


def test_review_submission_advances_review_level_automatically(vault: Path):
    from plugins.study_os.learning import handle_study_review_submission
    from plugins.study_os.tools import handle_study_project

    initialized = _loads(handle_study_project({"vault_path": str(vault), "action": "init", "project_id": "calculus-2027"}))
    assert initialized["ok"] is True
    note = vault / "math" / "examples" / "semantic-level.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(
        "---\n"
        "title: Semantic review level\n"
        "type: example\n"
        "review_level: 1\n"
        "---\n\n"
        "# Question\n",
        encoding="utf-8",
    )
    submission = {
        "vault_path": str(vault),
        "project_id": "calculus-2027",
        "note": "math/examples/semantic-level.md",
        "response": "A completely correct first attempt.",
        "result": "correct",
        "duration_seconds": 30,
    }

    submitted = _loads(handle_study_review_submission(submission))

    assert submitted["ok"] is True
    assert "self_confidence" not in submitted["data"]["attempt"]
    assert submitted["data"]["review"]["review_level"] == {"old": 1, "new": 3}
    assert "review_level: 3" in note.read_text(encoding="utf-8")

    second = _loads(
        handle_study_review_submission(
            {
                **submission,
                "attempt_id": "att-second-correct",
                "occurred_at": "2026-07-13T10:00:00+08:00",
            }
        )
    )
    third = _loads(
        handle_study_review_submission(
            {
                **submission,
                "attempt_id": "att-third-correct",
                "occurred_at": "2026-07-14T10:00:00+08:00",
            }
        )
    )

    assert second["data"]["review"]["review_level"] == {"old": 3, "new": 4}
    assert third["data"]["review"]["review_level"] == {"old": 4, "new": 5}


def test_review_submission_maps_answer_understanding_to_level_two(vault: Path):
    from plugins.study_os.learning import handle_study_review_submission
    from plugins.study_os.tools import handle_study_project

    initialized = _loads(handle_study_project({"vault_path": str(vault), "action": "init", "project_id": "calculus-2027"}))
    assert initialized["ok"] is True
    note = vault / "math" / "examples" / "understood-answer.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(
        "---\n"
        "title: Understood answer\n"
        "type: example\n"
        "review_level: 5\n"
        "---\n\n"
        "# Question\n",
        encoding="utf-8",
    )

    submitted = _loads(
        handle_study_review_submission(
            {
                "vault_path": str(vault),
                "project_id": "calculus-2027",
                "note": "math/examples/understood-answer.md",
                "response": "I understand the reference answer.",
                "result": "partial",
                "duration_seconds": 30,
            }
        )
    )

    assert submitted["ok"] is True
    assert submitted["data"]["review"]["review_level"] == {"old": 5, "new": 2}


def test_diagnosis_ignores_legacy_self_confidence(vault: Path):
    from plugins.study_os.learning import _all_attempts, _diagnosis

    activity = vault / ".StudyOS" / "projects" / "legacy-project" / "activity"
    activity.mkdir(parents=True)
    (activity / "attempts-2026-07.jsonl").write_text(
        json.dumps(
            {
                "attempt_id": "legacy-high-confidence-error",
                "result": "incorrect",
                "score": 0.0,
                "self_confidence": 5,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    attempts = _all_attempts(vault, "legacy-project")
    diagnosis = _diagnosis(attempts)

    assert attempts[0]["self_confidence"] == 5
    assert "calibration" not in diagnosis


def test_review_runner_rolls_back_attempt_when_review_update_fails(vault: Path, monkeypatch):
    from plugins.study_os import learning
    from plugins.study_os.tools import handle_study_project

    initialized = _loads(handle_study_project({"vault_path": str(vault), "action": "init", "project_id": "calculus-2027"}))
    assert initialized["ok"] is True
    note = vault / "examples" / "rollback.md"
    note.parent.mkdir(parents=True)
    original = "---\ntitle: Rollback\ntype: example\nreview_level: 0\n---\n\n# Rollback\n"
    note.write_text(original, encoding="utf-8")
    monkeypatch.setattr(
        learning.legacy,
        "handle_study_record_review",
        lambda _args: json.dumps({"ok": False, "error": {"message": "disk write failed"}, "warnings": []}),
    )

    result = _loads(
        learning.handle_study_review_submission(
            {
                "vault_path": str(vault),
                "project_id": "calculus-2027",
                "note": "examples/rollback.md",
                "attempt_id": "att-rollback-001",
                "response": "answer",
                "result": "incorrect",
                "duration_seconds": 12,
            }
        )
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "REVIEW_SUBMISSION_FAILED"
    assert note.read_text(encoding="utf-8") == original
    activity = vault / ".StudyOS" / "projects" / "calculus-2027" / "activity"
    assert not list(activity.glob("attempts-*.jsonl"))


def test_list_notes_reads_obsidian_frontmatter(vault: Path):
    from plugins.study_os.tools import handle_study_list_notes

    result = _loads(
        handle_study_list_notes(
            {
                "vault_path": str(vault),
                "folder": "OS",
                "tag": "OS",
                "limit": 10,
            }
        )
    )

    assert result["ok"] is True
    assert result["data"]["count"] == 2
    paths = {note["path"] for note in result["data"]["notes"]}
    assert "OS/Box/进程创建.md" in paths
    assert "OS/examples/OS-0043.md" in paths


def test_read_note_resolves_title_alias_and_extracts_links(vault: Path):
    from plugins.study_os.tools import handle_study_read_note

    result = _loads(
        handle_study_read_note(
            {
                "vault_path": str(vault),
                "note": "作业接纳",
                "include_body": True,
            }
        )
    )

    assert result["ok"] is True
    note = result["data"]["note"]
    assert note["path"] == "OS/Box/进程创建.md"
    assert note["title"] == "进程创建"
    assert note["layer"] == "concept"
    assert "进程控制块" in note["wikilinks"]
    assert "body" in note


def test_note_save_rejects_recursive_dangling_wikilinks_atomically(vault: Path):
    from plugins.study_os.learning import handle_study_activity

    result = _loads(
        handle_study_activity(
            {
                "resource": "note",
                "action": "save",
                "data": {
                    "vault_path": str(vault),
                    "notes": [
                        {
                            "path": "OS/Box/调度.md",
                            "content": "# 调度\n\n依赖 [[进程]]。\n",
                        },
                        {
                            "path": "OS/Box/进程.md",
                            "content": "# 进程\n\n由 [[进程控制块]] 表示。\n",
                        },
                    ],
                },
            }
        )
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "BROKEN_WIKILINKS"
    assert result["error"]["details"]["missing"] == [
        {
            "source": "OS/Box/进程.md",
            "target": "进程控制块",
        }
    ]
    assert not (vault / "OS" / "Box" / "调度.md").exists()
    assert not (vault / "OS" / "Box" / "进程.md").exists()


def test_note_save_writes_recursively_closed_batch_and_invalidates_graph_cache(
    vault: Path,
):
    from plugins.study_os.learning import handle_study_activity

    graph_cache = vault / ".StudyOS" / "concept_graph.json"
    graph_cache.parent.mkdir()
    graph_cache.write_text('{"stale": true}', encoding="utf-8")
    result = _loads(
        handle_study_activity(
            {
                "resource": "note",
                "action": "save",
                "data": {
                    "vault_path": str(vault),
                    "notes": [
                        {
                            "path": "OS/Box/调度.md",
                            "content": "# 调度\n\n依赖 [[进程]]。\n",
                        },
                        {
                            "path": "OS/Box/进程.md",
                            "content": "# 进程\n\n由 [[进程控制块]] 表示。\n",
                        },
                        {
                            "path": "OS/Box/进程控制块.md",
                            "content": "# 进程控制块\n\n记录进程状态和上下文。\n",
                        },
                    ],
                },
            }
        )
    )

    assert result["ok"] is True
    assert result["data"]["saved"] is True
    assert result["data"]["broken_links"] == []
    assert result["data"]["graph"]["edge_count"] == 2
    assert {
        item["path"] for item in result["data"]["notes"]
    } == {
        "OS/Box/调度.md",
        "OS/Box/进程.md",
        "OS/Box/进程控制块.md",
    }
    assert (vault / "OS" / "Box" / "调度.md").is_file()
    assert (vault / "OS" / "Box" / "进程.md").is_file()
    assert (vault / "OS" / "Box" / "进程控制块.md").is_file()
    assert not graph_cache.exists()


def test_note_graph_reports_transitive_existing_dangling_links(vault: Path):
    from plugins.study_os.learning import handle_study_activity

    (vault / "OS" / "Box" / "线程.md").write_text(
        "# 线程\n\n依赖 [[线程控制块]]。\n",
        encoding="utf-8",
    )
    (vault / "OS" / "Box" / "并发.md").write_text(
        "# 并发\n\n参见 [[线程]]。\n",
        encoding="utf-8",
    )
    result = _loads(
        handle_study_activity(
            {
                "resource": "note",
                "action": "graph",
                "data": {
                    "vault_path": str(vault),
                    "roots": ["并发"],
                },
            }
        )
    )

    assert result["ok"] is True
    assert result["data"]["broken_link_count"] == 1
    assert result["data"]["broken_links"] == [
        {
            "source": "OS/Box/线程.md",
            "target": "线程控制块",
        }
    ]
    assert result["data"]["graph"]["visited_notes"] == [
        "OS/Box/并发.md",
        "OS/Box/线程.md",
    ]


def test_note_save_accepts_existing_alias_and_attachment_targets(vault: Path):
    from plugins.study_os.learning import handle_study_activity

    attachment = vault / "OS" / "assets" / "process.png"
    attachment.parent.mkdir(parents=True)
    attachment.write_bytes(b"png")
    result = _loads(
        handle_study_activity(
            {
                "resource": "note",
                "action": "save",
                "data": {
                    "vault_path": str(vault),
                    "notes": [
                        {
                            "path": "OS/Box/进程总览.md",
                            "content": (
                                "# 进程总览\n\n"
                                "参见 [[作业接纳]] 和 [[process.png]]。\n"
                            ),
                        },
                        {
                            "path": "OS/Box/进程控制块.md",
                            "content": "# 进程控制块\n\n记录进程状态和上下文。\n",
                        },
                    ],
                },
            }
        )
    )

    assert result["ok"] is True
    assert result["data"]["graph"]["edge_count"] == 3
    assert result["data"]["broken_links"] == []


def test_note_save_rolls_back_earlier_writes_when_batch_write_fails(
    vault: Path,
    monkeypatch,
):
    from plugins.study_os import notes as note_module

    existing = vault / "OS" / "Box" / "A.md"
    existing.write_text("# Original A\n", encoding="utf-8")
    original_write = note_module._write_text
    calls = 0

    def fail_second_write(path: Path, content: str):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("disk full")
        return original_write(path, content)

    monkeypatch.setattr(note_module, "_write_text", fail_second_write)
    with pytest.raises(OSError, match="disk full"):
        note_module.save_note_batch(
            vault,
            [
                {"path": "OS/Box/A.md", "content": "# A\n\n[[B]]\n"},
                {"path": "OS/Box/B.md", "content": "# B\n"},
            ],
            overwrite=True,
        )

    assert existing.read_text(encoding="utf-8") == "# Original A\n"
    assert not (vault / "OS" / "Box" / "B.md").exists()


def test_extract_concepts_uses_frontmatter_and_candidates(vault: Path):
    from plugins.study_os.tools import handle_study_extract_concepts

    result = _loads(
        handle_study_extract_concepts(
            {
                "vault_path": str(vault),
                "notes": ["OS/examples/OS-0043.md", "Math/Box/题型/题型：泰勒展开.md"],
            }
        )
    )

    assert result["ok"] is True
    concepts = dict(result["data"]["concepts"])
    patterns = dict(result["data"]["patterns"])
    assert concepts["进程创建"] == 1
    assert concepts["泰勒展开"] == 1
    assert patterns["题型：高级调度与进程接纳辨析"] == 1


def test_write_tools_only_create_studyos_files(vault: Path):
    from plugins.study_os.tools import (
        handle_study_create_review_task,
        handle_study_generate_weekly_report,
        handle_study_log_error,
    )

    err = _loads(
        handle_study_log_error(
            {
                "vault_path": str(vault),
                "title": "创建态到就绪态调度混淆",
                "source_note": "OS/examples/OS-0043.md",
                "subject": "OS",
                "concepts": ["进程创建"],
                "patterns": ["题型：高级调度与进程接纳辨析"],
                "cause": "concept_confusion",
                "severity": "high",
                "next_action": "二刷高级调度与低级调度区别",
                "detail": "把作业接纳误判为 CPU 分配。",
                "occurred_on": "2026-06-22",
            }
        )
    )
    task = _loads(
        handle_study_create_review_task(
            {
                "vault_path": str(vault),
                "title": "二刷进程调度层次",
                "source_note": "OS/examples/OS-0043.md",
                "due_date": "2026-06-23",
                "priority": "high",
                "concepts": ["进程创建"],
                "review_level": 2,
            }
        )
    )
    report = _loads(
        handle_study_generate_weekly_report(
            {
                "vault_path": str(vault),
                "start_date": "2026-06-22",
                "end_date": "2026-06-28",
            }
        )
    )

    assert err["ok"] is True
    assert task["ok"] is True
    assert report["ok"] is True
    assert err["data"]["path"].startswith(".StudyOS/errors/")
    assert task["data"]["path"] == ".StudyOS/review_tasks.md"
    assert report["data"]["path"] == ".StudyOS/reports/2026-W26.md"
    assert (vault / ".StudyOS" / "errors" / "2026-06.md").exists()
    assert (vault / ".StudyOS" / "review_tasks.md").exists()
    assert "concept_confusion: 1" in (vault / ".StudyOS" / "reports" / "2026-W26.md").read_text(encoding="utf-8")
    assert not (vault / "errors").exists()


def test_export_anki_candidates_writes_candidates_under_studyos(vault: Path):
    from plugins.study_os.tools import handle_study_export_anki_candidates

    result = _loads(
        handle_study_export_anki_candidates(
            {
                "vault_path": str(vault),
                "folder": "OS",
                "query": "进程",
                "limit": 2,
                "include_errors": False,
            }
        )
    )

    assert result["ok"] is True
    path = vault / result["data"]["path"]
    assert path.as_posix().endswith(".StudyOS/anki_candidates/" + path.name)
    text = path.read_text(encoding="utf-8")
    assert "START" in text
    assert "Tags: StudyOS Obsidian" in text


def test_resolve_vault_path_uses_obsidian_env(vault: Path, monkeypatch):
    from plugins.study_os.tools import handle_study_list_notes

    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(vault))
    result = _loads(handle_study_list_notes({"folder": "OS", "limit": 1}))

    assert result["ok"] is True
    assert result["data"]["vault_path"] == str(vault)


def test_plugin_registers_tools_and_skill(monkeypatch):
    from hermes_cli import plugins as plugins_mod
    from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest
    from plugins import study_os
    from tools.registry import registry

    manager = PluginManager()
    monkeypatch.setattr(plugins_mod, "_plugin_manager", manager)
    manifest = PluginManifest(name="study_os", version="0.1.0", description="study", source="bundled")
    ctx = PluginContext(manifest, manager)

    try:
        study_os.register(ctx)
        assert registry.get_toolset_for_tool("study_activity") == "study"
        assert registry.get_toolset_for_tool("study_coach") == "study"
        assert manager.find_plugin_skill("study_os:study-os") is not None
        assert manager.has_hook("pre_llm_call")
    finally:
        for name in ("study_activity", "study_coach"):
            registry.deregister(name)
