from __future__ import annotations

import json
from pathlib import Path

import pytest

from enterprise_rag.application import (
    AgentRunRequest,
    CommandOutcome,
    RagAskRequest,
)
from enterprise_rag.cli import main
from enterprise_rag.errors import ProviderAuthenticationError
from enterprise_rag.execution_trace import RunTrace, TraceEvent
from enterprise_rag.models import (
    AgentResult,
    EvidenceClaim,
    RagResult,
    Source,
)


def sample_trace() -> RunTrace:
    return RunTrace(
        "run-cli",
        "succeeded",
        (
            TraceEvent(
                "run-cli",
                "run-cli:0001",
                None,
                "retrieval",
                "succeeded",
                0.0,
                12.5,
                {"backend_kind": "fake"},
                None,
            ),
        ),
    )


def answered_rag() -> RagResult:
    return RagResult.answered(
        "The hotel cap is CNY 600.",
        (Source("policy.md", "policy.md::0000", 0.91),),
        sample_trace(),
    )


def answered_agent() -> AgentResult:
    claim = EvidenceClaim(
        "The hotel cap is CNY 600.",
        ("policy.md::0000",),
        ("The hotel cap is CNY 600.",),
    )
    return AgentResult.answered(
        "The hotel cap is CNY 600.",
        claims=(claim,),
        sources=(Source("policy.md", "policy.md::0000", 0.91),),
        trace=sample_trace(),
    )


class FakeApplication:
    def __init__(self) -> None:
        self.rag_result: RagResult = answered_rag()
        self.agent_result: AgentResult = answered_agent()
        self.rag_requests: list[RagAskRequest] = []
        self.agent_requests: list[AgentRunRequest] = []
        self.built_paths: list[Path] = []
        self.eval_layers: list[str] = []
        self.doctor_calls = 0

    async def build_knowledge(self, path: Path) -> CommandOutcome:
        self.built_paths.append(path)
        return CommandOutcome.succeeded({"row_count": 8})

    async def ask_rag(self, request: RagAskRequest) -> RagResult:
        self.rag_requests.append(request)
        return self.rag_result

    async def run_agent(self, request: AgentRunRequest) -> AgentResult:
        self.agent_requests.append(request)
        return self.agent_result

    async def run_evaluation(self, layer: str) -> CommandOutcome:
        self.eval_layers.append(layer)
        return CommandOutcome.succeeded({"layer": layer, "cases": 0})

    async def run_diagnostics(self) -> CommandOutcome:
        self.doctor_calls += 1
        return CommandOutcome.succeeded({"python": "pass"})


class FailingApplication(FakeApplication):
    async def run_diagnostics(self) -> CommandOutcome:
        raise ProviderAuthenticationError(
            "secret-key-value at https://user:password@example.invalid"
        )


def test_rag_ask_json_contains_result_and_trace(capsys) -> None:
    application = FakeApplication()

    exit_code = main(
        [
            "rag",
            "ask",
            "What is the P4 hotel cap?",
            "--generator",
            "local",
            "--format",
            "json",
            "--trace",
        ],
        application=application,
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["status"] == "answered"
    assert payload["sources"][0]["chunk_id"] == "policy.md::0000"
    assert payload["trace"][0]["stage"] == "retrieval"
    assert application.rag_requests[0].fallback_local is None


def test_refusal_is_normal_exit_and_never_renders_sources(capsys) -> None:
    application = FakeApplication()
    application.rag_result = RagResult.refused(
        "insufficient_evidence",
        RunTrace("run-refused", "refused", ()),
    )

    exit_code = main(
        [
            "rag",
            "ask",
            "unknown",
            "--generator",
            "local",
            "--format",
            "json",
        ],
        application=application,
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["status"] == "refused"
    assert payload["answer"] is None
    assert payload["sources"] == []


def test_failed_result_uses_operational_exit_code_one(capsys) -> None:
    application = FakeApplication()
    application.rag_result = RagResult.failed(
        "retrieval_unavailable",
        RunTrace("run-failed", "failed", ()),
    )

    exit_code = main(
        [
            "rag",
            "ask",
            "question",
            "--generator",
            "local",
            "--format",
            "json",
        ],
        application=application,
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["status"] == "failed"
    assert payload["error_category"] == "retrieval_unavailable"


def test_online_fallback_absence_and_presence_remain_distinguishable(capsys) -> None:
    application = FakeApplication()
    base = [
        "rag",
        "ask",
        "question",
        "--generator",
        "online",
        "--provider",
        "relay-demo",
        "--format",
        "json",
    ]

    assert main(base, application=application) == 0
    capsys.readouterr()
    assert main([*base, "--fallback-local"], application=application) == 0
    capsys.readouterr()

    assert [request.fallback_local for request in application.rag_requests] == [
        None,
        True,
    ]


def test_local_generator_rejects_provider_and_fallback(capsys) -> None:
    application = FakeApplication()

    provider_exit = main(
        [
            "rag",
            "ask",
            "question",
            "--generator",
            "local",
            "--provider",
            "relay-demo",
        ],
        application=application,
    )
    fallback_exit = main(
        [
            "rag",
            "ask",
            "question",
            "--generator",
            "local",
            "--fallback-local",
        ],
        application=application,
    )
    capsys.readouterr()

    assert provider_exit == 2
    assert fallback_exit == 2
    assert application.rag_requests == []


def test_agent_run_dispatches_typed_request(capsys) -> None:
    application = FakeApplication()

    exit_code = main(
        [
            "agent",
            "run",
            "Explain CLAIM-1024",
            "--generator",
            "local",
            "--format",
            "json",
            "--trace",
        ],
        application=application,
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["status"] == "answered"
    assert application.agent_requests[0].task == "Explain CLAIM-1024"
    assert payload["trace"][0]["stage"] == "retrieval"


def test_kb_eval_and_doctor_commands_dispatch(capsys, tmp_path: Path) -> None:
    application = FakeApplication()

    assert main(["kb", "build", str(tmp_path)], application=application) == 0
    kb_payload = json.loads(capsys.readouterr().out)
    assert kb_payload["status"] == "succeeded"
    assert application.built_paths == [tmp_path]

    assert (
        main(
            ["eval", "run", "--layer", "rag"],
            application=application,
        )
        == 0
    )
    capsys.readouterr()
    assert application.eval_layers == ["rag"]

    assert main(["doctor"], application=application) == 0
    capsys.readouterr()
    assert application.doctor_calls == 1


def test_invalid_arguments_exit_two_without_dispatch() -> None:
    application = FakeApplication()

    assert main(["eval", "run", "--layer", "unknown"], application=application) == 2
    assert main(["--unknown-option"], application=application) == 2
    assert application.eval_layers == []


def test_cli_composes_real_rag_only_for_rag_commands(
    capsys,
    monkeypatch,
) -> None:
    composition_calls: list[dict[str, object]] = []

    def build(**kwargs: object) -> FakeApplication:
        composition_calls.append(dict(kwargs))
        return FakeApplication()

    monkeypatch.setattr("enterprise_rag.cli.build_application", build)

    assert main(["doctor"], application=None) == 0
    capsys.readouterr()

    assert (
        main(
            [
                "rag",
                "ask",
                "question",
                "--generator",
                "local",
                "--format",
                "json",
            ],
            application=None,
        )
        == 0
    )
    rag_payload = json.loads(capsys.readouterr().out)

    assert composition_calls == [
        {"compose_rag": False, "compose_diagnostics": True},
        {
            "compose_rag": True,
            "compose_agent": False,
            "generator_choice": "local",
            "provider_name": None,
            "fallback_local": False,
        },
    ]
    assert rag_payload["status"] == "answered"


def test_typed_operational_error_does_not_leak_exception_message(capsys) -> None:
    exit_code = main(["doctor"], application=FailingApplication())
    error_output = capsys.readouterr().err

    assert exit_code == 1
    assert "authentication" in error_output
    assert "secret-key-value" not in error_output
    assert "password" not in error_output


def test_text_rendering_hides_detailed_trace_without_flag(capsys) -> None:
    application = FakeApplication()

    exit_code = main(
        [
            "rag",
            "ask",
            "question",
            "--generator",
            "local",
            "--format",
            "text",
        ],
        application=application,
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "status: answered" in output
    assert "duration_ms: 12.500" in output
    assert "retrieval" not in output


def test_the_panel_branch_runs_before_any_application_is_composed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The panel must never drag the RAG core (or NiceGUI) into composition."""

    import enterprise_rag.cli as cli

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("the panel branch must not compose an application")

    monkeypatch.setattr(cli, "build_application", forbidden)
    calls: list[dict[str, object]] = []

    def runner(**kwargs: object) -> int:
        calls.append(dict(kwargs))
        return 0

    exit_code = main(["panel", "--no-open"], panel_runner=runner)

    assert exit_code == 0
    assert calls == [
        {
            "host": "127.0.0.1",
            "port": 8080,
            "open_browser": False,
            "native": False,
        }
    ]


def test_legacy_dispatch_is_unchanged_by_the_panel_command() -> None:
    application = FakeApplication()

    exit_code = main(
        ["doctor"],
        application=application,
    )

    assert exit_code == 0
