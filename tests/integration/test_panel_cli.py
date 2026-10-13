"""The lazy ``rag-agent panel`` CLI entry and its deterministic port preflight."""

from __future__ import annotations

import socket
import sys
from collections.abc import Sequence

import pytest

from enterprise_rag.cli import PANEL_DEFAULT_PORT, PANEL_HOST, build_parser, main


def parse(argv: Sequence[str]) -> object:
    return build_parser().parse_args(list(argv))


def test_panel_command_defaults_to_loopback_8080() -> None:
    arguments = parse(["panel"])

    assert arguments.port == PANEL_DEFAULT_PORT == 8080
    assert arguments.no_open is False
    assert arguments.command == "panel"


def test_panel_command_accepts_port_and_no_open() -> None:
    arguments = parse(["panel", "--port", "8081", "--no-open"])

    assert arguments.port == 8081
    assert arguments.no_open is True


@pytest.mark.parametrize(
    "argv",
    [
        ["panel", "--port", "not-a-number"],
        ["panel", "--port", "0"],
        ["panel", "--port", "65536"],
        ["panel", "--port", "-1"],
    ],
)
def test_invalid_ports_return_parser_code_2(argv: list[str]) -> None:
    assert main(argv) == 2


def test_panel_runner_receives_loopback_host_and_requested_port() -> None:
    calls: list[dict[str, object]] = []

    def runner(**kwargs: object) -> int:
        calls.append(dict(kwargs))
        return 0

    assert main(["panel", "--port", "8081", "--no-open"], panel_runner=runner) == 0

    assert calls == [
        {
            "host": PANEL_HOST,
            "port": 8081,
            "open_browser": False,
            "native": False,
        }
    ]


def test_panel_runner_opens_the_browser_unless_no_open_is_given() -> None:
    calls: list[dict[str, object]] = []

    def runner(**kwargs: object) -> int:
        calls.append(dict(kwargs))
        return 0

    assert main(["panel"], panel_runner=runner) == 0
    assert calls[0]["open_browser"] is True
    assert calls[0]["native"] is False
    assert calls[0]["host"] == "127.0.0.1"


def test_an_occupied_port_fails_closed_without_choosing_another(
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[dict[str, object]] = []

    def runner(**kwargs: object) -> int:
        calls.append(dict(kwargs))
        return 0

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
        occupied.bind((PANEL_HOST, 0))
        occupied.listen(1)
        port = occupied.getsockname()[1]

        exit_code = main(["panel", "--port", str(port)], panel_runner=runner)

    captured = capsys.readouterr()
    assert exit_code == 1
    assert calls == []
    assert str(port) in captured.err
    assert "--port" in captured.err


def test_the_panel_extra_is_only_imported_for_the_panel_command() -> None:
    import enterprise_rag.cli as cli

    for module in ("enterprise_rag.panel.app", "nicegui"):
        sys.modules.pop(module, None)

    assert cli.main(["doctor"]) in {0, 1}

    assert "enterprise_rag.panel.app" not in sys.modules
    assert "nicegui" not in sys.modules


def test_legacy_commands_still_parse_after_adding_panel() -> None:
    assert parse(["doctor"]).command == "doctor"
    assert parse(["kb", "build", "docs"]).command == "kb"
    assert parse(["rag", "ask", "q", "--generator", "local"]).command == "rag"
    assert parse(["eval", "run", "--layer", "all"]).command == "eval"


@pytest.mark.parametrize(
    "host",
    ["0.0.0.0", "192.168.1.10", "localhost", "example.invalid", "::1"],
)
def test_run_panel_rejects_every_non_loopback_host(host: str) -> None:
    from enterprise_rag.errors import ConfigurationError
    from enterprise_rag.panel.app import run_panel

    with pytest.raises(ConfigurationError, match="loopback"):
        run_panel(host=host, port=8080, open_browser=False, native=False)


def test_run_panel_rejects_native_mode_and_invalid_ports() -> None:
    from enterprise_rag.errors import ConfigurationError
    from enterprise_rag.panel.app import run_panel

    with pytest.raises(ConfigurationError, match="browser mode"):
        run_panel(host=PANEL_HOST, port=8080, open_browser=False, native=True)
    for port in (0, -1, 65536):
        with pytest.raises(ValueError, match="port"):
            run_panel(
                host=PANEL_HOST,
                port=port,
                open_browser=False,
                native=False,
            )
