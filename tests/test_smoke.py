import sys

import pytest

from enterprise_rag import __version__
from enterprise_rag.cli import console_main, main


def test_package_version_and_cli_help(capsys) -> None:
    assert __version__ == "0.1.0"
    exit_code = main(["--help"])
    assert exit_code == 0
    assert "rag-agent" in capsys.readouterr().out


def test_cli_invalid_argument_returns_parser_exit_code() -> None:
    assert main(["--unknown-option"]) == 2


def test_console_main_preserves_process_exit(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["rag-agent"])
    with pytest.raises(SystemExit) as exc_info:
        console_main()
    assert exc_info.value.code == 0


def test_main_preserves_non_integer_system_exit(monkeypatch) -> None:
    class Parser:
        def parse_args(self, _argv) -> None:
            raise SystemExit("unexpected")

    monkeypatch.setattr("enterprise_rag.cli.build_parser", Parser)

    with pytest.raises(SystemExit) as exc_info:
        main([])
    assert exc_info.value.code == "unexpected"
