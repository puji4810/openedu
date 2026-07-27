"""Remote compaction via OpenAI's POST /v1/responses/compact.

Covers eligibility gating (only first-party OpenAI Responses surfaces), the
request/response mapping, replay of the returned compaction item, and the
fallback to the local summarizer on failure.
"""

from types import SimpleNamespace

import pytest

from agent.codex_responses_adapter import (
    _chat_messages_to_responses_input,
    _preflight_codex_input_items,
)
from agent.openai_remote_compaction import (
    COMPACTION_ITEM_TYPE,
    REMOTE_COMPACTION_METADATA_KEY,
    RESPONSES_REPLAY_ITEMS_KEY,
    compact_messages_via_openai,
    remote_compaction_eligible,
)

ISSUER = "other:https://api.openai.com/v1"


class FakeResponses:
    def __init__(self, result=None, error=None):
        self._result = result
        self._error = error
        self.calls = []

    def compact(self, **kwargs):
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return self._result


class FakeClient:
    def __init__(self, responses):
        self.responses = responses


def make_agent(client=None, **overrides):
    agent = SimpleNamespace(
        model="gpt-5.2",
        provider="openai",
        base_url="https://api.openai.com/v1",
        api_key="sk-test",
        api_mode="codex_responses",
        session_id="s1",
        openai_remote_compaction="auto",
        _cached_system_prompt="You are Hermes.",
        _codex_reasoning_replay_enabled=True,
        _session_db=None,
        context_compressor=None,
    )
    agent._is_direct_openai_url = lambda base_url=None: "api.openai.com" in (
        base_url or agent.base_url
    )
    agent._is_azure_openai_url = lambda base_url=None: "azure" in (
        base_url or agent.base_url
    )
    agent._resolved_api_call_timeout = lambda: 300.0
    agent._ensure_primary_openai_client = lambda *, reason: client
    for key, value in overrides.items():
        setattr(agent, key, value)
    return agent


def compacted_response(*, user_texts=("hi", "and then"), blob="ENCRYPTED"):
    output = [
        SimpleNamespace(
            type="message",
            role="user",
            content=[SimpleNamespace(type="input_text", text=text)],
        )
        for text in user_texts
    ]
    output.append(
        SimpleNamespace(
            type=COMPACTION_ITEM_TYPE,
            id="cmp_abc",
            encrypted_content=blob,
        )
    )
    return SimpleNamespace(
        id="resp_1",
        object="response.compaction",
        output=output,
        usage=None,
    )


def long_history():
    return [
        {"role": "system", "content": "You are Hermes."},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call_1", "function": {"name": "read_file", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "call_1", "content": "x" * 5000},
        {"role": "assistant", "content": "done"},
        {"role": "user", "content": "and then"},
    ]


# ── Eligibility ────────────────────────────────────────────────────────────

def test_eligible_on_official_openai_responses():
    assert remote_compaction_eligible(make_agent())[0] is True


def test_eligible_on_chatgpt_codex_oauth_responses():
    agent = make_agent(
        provider="openai-codex",
        base_url="https://chatgpt.com/backend-api/codex",
    )
    assert remote_compaction_eligible(agent)[0] is True


def test_chatgpt_codex_compaction_uses_oauth_endpoint_contract():
    responses = FakeResponses(result=compacted_response())
    agent = make_agent(
        FakeClient(responses),
        provider="openai-codex",
        base_url="https://chatgpt.com/backend-api/codex/",
        tools=[{
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a file",
                "parameters": {"type": "object", "properties": {}},
            },
        }],
    )

    compressed = compact_messages_via_openai(agent, long_history())

    call = responses.calls[0]
    assert call["extra_headers"] == {
        "session_id": "s1",
        "x-client-request-id": "s1",
    }
    assert call["extra_body"] == {
        "tools": [{
            "type": "function",
            "name": "read_file",
            "description": "Read a file",
            "strict": False,
            "parameters": {"type": "object", "properties": {}},
        }],
        "parallel_tool_calls": True,
    }
    carrier = compressed[-1]
    assert carrier[RESPONSES_REPLAY_ITEMS_KEY][0]["_issuer_kind"] == "codex_backend"
    replay = _chat_messages_to_responses_input(
        compressed,
        current_issuer_kind="codex_backend",
    )
    assert replay[-1] == {
        "type": COMPACTION_ITEM_TYPE,
        "encrypted_content": "ENCRYPTED",
    }


def test_chatgpt_codex_compaction_reaches_sdk_endpoint():
    """Exercise the real OpenAI SDK URL/body mapping without network access."""
    import json

    import httpx
    from openai import OpenAI

    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "id": "resp_oauth",
            "object": "response.compaction",
            "created_at": 0,
            "output": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "hi"}],
                },
                {
                    "type": COMPACTION_ITEM_TYPE,
                    "id": "cmp_oauth",
                    "encrypted_content": "OAUTH_BLOB",
                },
            ],
            "usage": None,
        })

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = OpenAI(
        api_key="oauth-test",
        base_url="https://chatgpt.com/backend-api/codex",
        http_client=http_client,
    )
    try:
        agent = make_agent(
            client,
            provider="openai-codex",
            base_url="https://chatgpt.com/backend-api/codex",
            tools=[{
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read a file",
                    "parameters": {"type": "object", "properties": {}},
                },
            }],
        )
        compressed = compact_messages_via_openai(agent, long_history())
    finally:
        client.close()

    assert seen["url"] == (
        "https://chatgpt.com/backend-api/codex/responses/compact"
    )
    assert seen["headers"]["session_id"] == "s1"
    assert seen["body"]["tools"][0]["name"] == "read_file"
    assert compressed[-1][RESPONSES_REPLAY_ITEMS_KEY][0] == {
        "type": COMPACTION_ITEM_TYPE,
        "encrypted_content": "OAUTH_BLOB",
        "_issuer_kind": "codex_backend",
        "id": "cmp_oauth",
    }


@pytest.mark.parametrize(
    "overrides",
    [
        {"api_mode": "chat_completions"},
        {"openai_remote_compaction": "off"},
        {"provider": "openai-codex", "base_url": "https://chatgpt.example/backend-api/codex"},
        {"provider": "xai", "base_url": "https://api.x.ai/v1"},
        {"provider": "", "base_url": "https://openrouter.ai/api/v1"},
        {"provider": "openai", "base_url": "https://my-res.openai.azure.com/openai/v1"},
        {"_codex_reasoning_replay_enabled": False},
        {"_openai_remote_compaction_unsupported": True},
    ],
)
def test_ineligible_runtimes(overrides):
    eligible, reason = remote_compaction_eligible(make_agent(**overrides))
    assert eligible is False
    assert reason


def test_provider_openai_without_base_url_is_eligible():
    agent = make_agent(provider="openai", base_url="")
    assert remote_compaction_eligible(agent)[0] is True


# ── Request / response mapping ─────────────────────────────────────────────

def test_compact_sends_responses_input_and_maps_output_back():
    responses = FakeResponses(result=compacted_response())
    agent = make_agent(FakeClient(responses))

    compressed = compact_messages_via_openai(agent, long_history())

    assert responses.calls, "expected one /responses/compact call"
    call = responses.calls[0]
    assert call["model"] == "gpt-5.2"
    assert call["instructions"] == "You are Hermes."
    assert call["timeout"] == 300.0
    # The system prompt travels as `instructions`, never as an input item.
    assert all(item.get("role") != "system" for item in call["input"])
    # Tool traffic is rendered in Responses shape for the compactor.
    assert {"function_call", "function_call_output"} <= {
        item.get("type") for item in call["input"] if item.get("type")
    }

    assert [m["role"] for m in compressed] == ["user", "user", "assistant"]
    assert [m["content"] for m in compressed[:2]] == ["hi", "and then"]
    carrier = compressed[-1]
    assert carrier[REMOTE_COMPACTION_METADATA_KEY] is True
    assert carrier[RESPONSES_REPLAY_ITEMS_KEY] == [{
        "type": COMPACTION_ITEM_TYPE,
        "encrypted_content": "ENCRYPTED",
        "_issuer_kind": ISSUER,
        "id": "cmp_abc",
    }]


def test_compacted_window_replays_as_a_compaction_input_item():
    responses = FakeResponses(result=compacted_response())
    agent = make_agent(FakeClient(responses))

    compressed = compact_messages_via_openai(agent, long_history())
    items = _preflight_codex_input_items(
        _chat_messages_to_responses_input(compressed, current_issuer_kind=ISSUER)
    )

    assert items == [
        {"role": "user", "content": "hi"},
        {"role": "user", "content": "and then"},
        # ``id`` is stripped: with store=False the API cannot resolve it.
        {"type": COMPACTION_ITEM_TYPE, "encrypted_content": "ENCRYPTED"},
    ]


def test_compaction_item_from_another_endpoint_is_not_replayed():
    """encrypted_content is sealed to its issuer — a foreign blob must drop."""
    messages = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": "",
            RESPONSES_REPLAY_ITEMS_KEY: [{
                "type": COMPACTION_ITEM_TYPE,
                "encrypted_content": "ENCRYPTED",
                "_issuer_kind": ISSUER,
            }],
        },
    ]

    items = _chat_messages_to_responses_input(
        messages, current_issuer_kind="xai_responses"
    )

    assert items == [{"role": "user", "content": "hi"}]


def test_multimodal_user_message_survives_compaction():
    response = SimpleNamespace(
        id="resp_2",
        output=[
            SimpleNamespace(
                type="message",
                role="user",
                content=[
                    SimpleNamespace(type="input_text", text="look"),
                    SimpleNamespace(
                        type="input_image",
                        image_url="data:image/png;base64,AAA",
                        detail="high",
                    ),
                ],
            ),
            SimpleNamespace(
                type=COMPACTION_ITEM_TYPE, id="cmp_1", encrypted_content="BLOB",
            ),
        ],
        usage=None,
    )
    agent = make_agent(FakeClient(FakeResponses(result=response)))

    compressed = compact_messages_via_openai(agent, long_history())

    assert compressed[0]["content"] == [
        {"type": "text", "text": "look"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA", "detail": "high"}},
    ]


# ── Failure handling ───────────────────────────────────────────────────────

def test_transient_failure_returns_none_without_latching_off():
    agent = make_agent(FakeClient(FakeResponses(error=RuntimeError("boom"))))

    assert compact_messages_via_openai(agent, long_history()) is None
    assert not getattr(agent, "_openai_remote_compaction_unsupported", False)


def test_unsupported_endpoint_latches_off_for_the_session():
    error = RuntimeError("no such endpoint")
    error.status_code = 404
    agent = make_agent(FakeClient(FakeResponses(error=error)))

    assert compact_messages_via_openai(agent, long_history()) is None
    assert agent._openai_remote_compaction_unsupported is True
    assert remote_compaction_eligible(agent)[0] is False


def test_plaintext_replacement_history_is_accepted_without_opaque_item():
    """Codex OAuth may return a replacement history without an opaque item."""
    response = SimpleNamespace(
        id="resp_3",
        output=[
            SimpleNamespace(
                type="message",
                role="user",
                content=[SimpleNamespace(
                    type="input_text",
                    text="[CONTEXT COMPACTION] Preserve the learner's current work.",
                )],
            ),
            SimpleNamespace(
                type="message",
                role="assistant",
                content=[SimpleNamespace(type="output_text", text="Ready.")],
            ),
        ],
        usage=None,
    )
    agent = make_agent(FakeClient(FakeResponses(result=response)))

    assert compact_messages_via_openai(agent, long_history()) == [
        {
            "role": "user",
            "content": "[CONTEXT COMPACTION] Preserve the learner's current work.",
        },
        {"role": "assistant", "content": "Ready."},
    ]


def test_output_without_replayable_history_is_treated_as_failure():
    response = SimpleNamespace(
        id="resp_ignored",
        output=[SimpleNamespace(type="reasoning", encrypted_content="IGNORED")],
        usage=None,
    )
    agent = make_agent(FakeClient(FakeResponses(result=response)))

    assert compact_messages_via_openai(agent, long_history()) is None


@pytest.mark.parametrize(
    ("item_type", "stored_type"),
    [
        ("compaction_summary", "compaction"),
        ("context_compaction", "context_compaction"),
    ],
)
def test_compaction_item_variants_are_replayed(item_type, stored_type):
    response = SimpleNamespace(
        id="resp_variant",
        output=[
            SimpleNamespace(
                type=item_type,
                id="cmp_variant",
                encrypted_content="VARIANT_BLOB",
            ),
        ],
        usage=None,
    )
    agent = make_agent(FakeClient(FakeResponses(result=response)))

    compressed = compact_messages_via_openai(agent, long_history())

    assert compressed[-1][RESPONSES_REPLAY_ITEMS_KEY][0] == {
        "type": stored_type,
        "encrypted_content": "VARIANT_BLOB",
        "_issuer_kind": ISSUER,
        "id": "cmp_variant",
    }
    replay = _preflight_codex_input_items(
        _chat_messages_to_responses_input(
            compressed,
            current_issuer_kind=ISSUER,
        )
    )
    assert replay[-1] == {
        "type": stored_type,
        "encrypted_content": "VARIANT_BLOB",
    }


def test_sdk_without_compact_support_latches_off():
    agent = make_agent(FakeClient(SimpleNamespace()))

    assert compact_messages_via_openai(agent, long_history()) is None
    assert agent._openai_remote_compaction_unsupported is True


def test_disabled_by_config_skips_the_endpoint():
    responses = FakeResponses(result=compacted_response())
    agent = make_agent(FakeClient(responses), openai_remote_compaction="off")

    assert compact_messages_via_openai(agent, long_history()) is None
    assert responses.calls == []


def test_short_history_is_left_to_the_local_compressor():
    responses = FakeResponses(result=compacted_response())
    agent = make_agent(FakeClient(responses))

    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
    ]

    assert compact_messages_via_openai(agent, messages) is None
    assert responses.calls == []


# ── compress_context() routing ─────────────────────────────────────────────

def _agent_on_openai(db, session_id, local_result):
    """A real AIAgent on the official OpenAI Responses surface."""
    from unittest.mock import MagicMock

    from run_agent import AIAgent

    agent = AIAgent(
        api_key="sk-test",
        base_url="https://api.openai.com/v1",
        provider="openai",
        model="gpt-5.2",
        quiet_mode=True,
        session_db=db,
        session_id=session_id,
        skip_context_files=True,
        skip_memory=True,
    )
    assert agent.api_mode == "codex_responses"

    compressor = MagicMock()
    compressor.compress.return_value = local_result
    compressor.compression_count = 0
    compressor.last_prompt_tokens = 0
    compressor.last_completion_tokens = 0
    compressor._last_summary_error = None
    compressor._last_compress_aborted = False
    compressor._last_aux_model_failure_model = None
    compressor._last_aux_model_failure_error = None
    compressor._last_compression_made_progress = True
    compressor._last_summary_fallback_used = False
    agent.context_compressor = compressor
    return agent


def _history(n=24):
    return [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"}
        for i in range(n)
    ]


def test_compress_context_prefers_the_remote_endpoint(tmp_path):
    from hermes_state import SessionDB

    db = SessionDB(db_path=tmp_path / "state.db")
    db.create_session("REMOTE_1", source="cli")
    local = [{"role": "user", "content": "local summary"}]
    agent = _agent_on_openai(db, "REMOTE_1", local)

    responses = FakeResponses(result=compacted_response(user_texts=("m0", "m22")))
    agent._ensure_primary_openai_client = lambda *, reason: FakeClient(responses)

    compressed, _prompt = agent._compress_context(_history(), "sys", approx_tokens=120_000)

    assert responses.calls, "remote compaction was not attempted"
    agent.context_compressor.compress.assert_not_called()
    assert compressed[-1][RESPONSES_REPLAY_ITEMS_KEY][0]["encrypted_content"] == "ENCRYPTED"
    # The boundary is scored like any completed compaction.
    assert agent.context_compressor.compression_count == 1
    assert agent.context_compressor._last_compression_made_progress is True
    assert agent.context_compressor._last_compress_aborted is False


def test_compress_context_falls_back_to_the_local_summarizer(tmp_path):
    from hermes_state import SessionDB

    db = SessionDB(db_path=tmp_path / "state.db")
    db.create_session("REMOTE_2", source="cli")
    local = [
        {"role": "user", "content": "[CONTEXT COMPACTION] summary"},
        {"role": "user", "content": "tail"},
    ]
    agent = _agent_on_openai(db, "REMOTE_2", local)

    responses = FakeResponses(error=RuntimeError("service unavailable"))
    agent._ensure_primary_openai_client = lambda *, reason: FakeClient(responses)

    compressed, _prompt = agent._compress_context(_history(), "sys", approx_tokens=120_000)

    assert responses.calls, "remote compaction was not attempted"
    agent.context_compressor.compress.assert_called_once()
    assert [m["content"] for m in compressed[:2]] == [
        "[CONTEXT COMPACTION] summary",
        "tail",
    ]


def test_memory_provider_context_is_carried_into_the_compact_request():
    responses = FakeResponses(result=compacted_response())
    agent = make_agent(FakeClient(responses))

    compact_messages_via_openai(
        agent,
        long_history(),
        memory_context="User prefers pytest over unittest.",
    )

    instructions = responses.calls[0]["instructions"]
    assert instructions.startswith("You are Hermes.")
    assert "MEMORY PROVIDER CONTEXT" in instructions
    assert "User prefers pytest over unittest." in instructions


def test_focus_topic_routes_to_the_local_compressor(tmp_path):
    """/compress <focus> has no equivalent on the endpoint — keep it local."""
    from hermes_state import SessionDB

    db = SessionDB(db_path=tmp_path / "state.db")
    db.create_session("REMOTE_3", source="cli")
    agent = _agent_on_openai(db, "REMOTE_3", [{"role": "user", "content": "focused"}])

    responses = FakeResponses(result=compacted_response())
    agent._ensure_primary_openai_client = lambda *, reason: FakeClient(responses)

    agent._compress_context(
        _history(), "sys", approx_tokens=120_000, focus_topic="authentication",
    )

    assert responses.calls == []
    agent.context_compressor.compress.assert_called_once()


def test_compaction_call_is_billed_to_the_session():
    response = compacted_response()
    response.usage = SimpleNamespace(
        input_tokens=120_000,
        output_tokens=800,
        input_tokens_details=SimpleNamespace(cached_tokens=100_000),
        output_tokens_details=SimpleNamespace(reasoning_tokens=600),
    )
    agent = make_agent(
        FakeClient(FakeResponses(result=response)),
        session_api_calls=0,
        session_prompt_tokens=0,
        session_completion_tokens=0,
        session_total_tokens=0,
        session_input_tokens=0,
        session_output_tokens=0,
        session_cache_read_tokens=0,
        session_reasoning_tokens=0,
        session_estimated_cost_usd=0.0,
    )

    compact_messages_via_openai(agent, long_history())

    assert agent.session_api_calls == 1
    assert agent.session_prompt_tokens == 120_000
    assert agent.session_input_tokens == 20_000
    assert agent.session_cache_read_tokens == 100_000
    assert agent.session_output_tokens == 800
    assert agent.session_reasoning_tokens == 600
