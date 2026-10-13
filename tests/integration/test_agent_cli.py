from __future__ import annotations

import json

from enterprise_rag.cli import main
from tests.integration.test_cli import FakeApplication


def test_agent_cli_json_exposes_grounded_result_contract(capsys) -> None:
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
    assert payload["claims"][0]["support_ids"] == ["policy.md::0000"]
    assert payload["sources"][0]["chunk_id"] == "policy.md::0000"


def test_agent_command_requests_agent_composition(capsys, monkeypatch) -> None:
    compose_calls: list[dict[str, object]] = []

    def build(**kwargs: object) -> FakeApplication:
        compose_calls.append(dict(kwargs))
        return FakeApplication()

    monkeypatch.setattr("enterprise_rag.cli.build_application", build)

    exit_code = main(
        [
            "agent",
            "run",
            "Explain CLAIM-1024",
            "--generator",
            "local",
            "--format",
            "json",
        ]
    )
    capsys.readouterr()

    assert exit_code == 0
    assert compose_calls == [
        {
            "compose_rag": True,
            "compose_agent": True,
            "generator_choice": "local",
            "provider_name": None,
            "fallback_local": False,
        }
    ]
