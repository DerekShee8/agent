from __future__ import annotations

import json

from enterprise_rag.cli import main
from tests.integration.test_cli import FakeApplication


def test_eval_cli_accepts_json_format_and_dispatches_layer(capsys) -> None:
    application = FakeApplication()

    exit_code = main(
        ["eval", "run", "--layer", "all", "--format", "json"],
        application=application,
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert application.eval_layers == ["all"]
    assert payload["data"]["layer"] == "all"


def test_eval_cli_requests_real_evaluation_composition(capsys, monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def build(**kwargs: object) -> FakeApplication:
        calls.append(dict(kwargs))
        return FakeApplication()

    monkeypatch.setattr("enterprise_rag.cli.build_application", build)

    exit_code = main(["eval", "run", "--layer", "reranker"])
    capsys.readouterr()

    assert exit_code == 0
    assert calls == [
        {"compose_evaluation": True, "evaluation_layer": "reranker"}
    ]

