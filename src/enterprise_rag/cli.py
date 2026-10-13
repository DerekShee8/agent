import argparse
import asyncio
import socket
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import cast

from .application import (
    AgentRunRequest,
    ApplicationPort,
    EvaluationLayer,
    GeneratorChoice,
    RagAskRequest,
    build_application,
)
from .cli_rendering import (
    render_agent_result,
    render_command_outcome,
    render_rag_result,
)
from .errors import ConfigurationError
from .models import AgentResult, RagResult

# The panel only ever binds the loopback interface, on exactly the requested port.
PANEL_HOST = "127.0.0.1"
PANEL_DEFAULT_PORT = 8080
PanelRunner = Callable[..., int]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rag-agent")
    commands = parser.add_subparsers(dest="command")

    kb_parser = commands.add_parser("kb")
    kb_commands = kb_parser.add_subparsers(dest="kb_command", required=True)
    kb_build = kb_commands.add_parser("build")
    kb_build.add_argument("path", type=Path)

    rag_parser = commands.add_parser("rag")
    rag_commands = rag_parser.add_subparsers(dest="rag_command", required=True)
    rag_ask = rag_commands.add_parser("ask")
    rag_ask.add_argument("question")
    _add_answer_options(rag_ask)

    agent_parser = commands.add_parser("agent")
    agent_commands = agent_parser.add_subparsers(
        dest="agent_command",
        required=True,
    )
    agent_run = agent_commands.add_parser("run")
    agent_run.add_argument("task")
    _add_answer_options(agent_run)

    eval_parser = commands.add_parser("eval")
    eval_commands = eval_parser.add_subparsers(dest="eval_command", required=True)
    eval_run = eval_commands.add_parser("run")
    eval_run.add_argument(
        "--layer",
        required=True,
        choices=("retriever", "reranker", "rag", "agent", "all"),
    )
    eval_run.add_argument("--format", choices=("json",), default="json")

    commands.add_parser("doctor")

    panel_parser = commands.add_parser("panel")
    panel_parser.add_argument("--port", type=int, default=PANEL_DEFAULT_PORT)
    panel_parser.add_argument("--no-open", action="store_true")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    application: ApplicationPort | None = None,
    panel_runner: PanelRunner | None = None,
) -> int:
    parser = build_parser()
    try:
        arguments = parser.parse_args(argv)
        _validate_generator_options(parser, arguments)
        _validate_panel_options(parser, arguments)
    except SystemExit as error:
        if not isinstance(error.code, int):
            raise
        return error.code

    if arguments.command is None:
        parser.print_help()
        return 0

    if arguments.command == "panel":
        try:
            _probe_panel_port(PANEL_HOST, arguments.port)
            runner = panel_runner or _load_panel_runner()
            return runner(
                host=PANEL_HOST,
                port=arguments.port,
                open_browser=not arguments.no_open,
                native=False,
            )
        except Exception as error:
            print(_public_error(error), file=sys.stderr)
            return 1

    try:
        if application is not None:
            active_application = application
        elif arguments.command in {"rag", "agent"}:
            active_application = build_application(
                compose_rag=True,
                compose_agent=arguments.command == "agent",
                generator_choice=cast(GeneratorChoice, arguments.generator),
                provider_name=arguments.provider,
                fallback_local=arguments.fallback_local is True,
            )
        elif arguments.command == "eval":
            active_application = build_application(
                compose_evaluation=True,
                evaluation_layer=cast(EvaluationLayer, arguments.layer),
            )
        elif arguments.command == "doctor":
            active_application = build_application(
                compose_rag=False,
                compose_diagnostics=True,
            )
        else:
            active_application = build_application(compose_rag=False)
        rendered, exit_code = asyncio.run(_dispatch(arguments, active_application))
    except Exception as error:
        print(_public_error(error), file=sys.stderr)
        return 1
    print(rendered)
    return exit_code


def console_main() -> None:
    raise SystemExit(main())


def _add_answer_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--generator",
        required=True,
        choices=("local", "online"),
    )
    parser.add_argument("--provider")
    parser.add_argument(
        "--fallback-local",
        action="store_const",
        const=True,
        default=None,
    )
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--trace", action="store_true")


def _validate_panel_options(
    parser: argparse.ArgumentParser,
    arguments: argparse.Namespace,
) -> None:
    if getattr(arguments, "command", None) != "panel":
        return
    port = arguments.port
    if port < 1 or port > 65535:
        parser.error("--port must be a port number between 1 and 65535")


def _probe_panel_port(host: str, port: int) -> None:
    """Fail fast on an occupied port without silently choosing another one.

    The probe socket binds exactly ``host:port`` and is closed immediately, so
    the panel either starts on the requested port or refuses to start at all.
    ``SO_REUSEADDR`` is deliberately not set: on this platform it would let the
    probe bind an address another process is already listening on.
    """

    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind((host, port))
    except OSError as error:
        raise ConfigurationError(
            f"port {port} on {host} is already in use; choose another one with --port"
        ) from error
    finally:
        probe.close()


def _load_panel_runner() -> PanelRunner:
    """The only place in this module that imports the optional panel extra."""

    from .panel.app import run_panel

    return run_panel


def _validate_generator_options(
    parser: argparse.ArgumentParser,
    arguments: argparse.Namespace,
) -> None:
    generator = getattr(arguments, "generator", None)
    if generator == "online" and getattr(arguments, "provider", None) is None:
        parser.error("--provider is required with --generator online")
    if generator != "local":
        return
    if getattr(arguments, "provider", None) is not None:
        parser.error("--provider is only valid with --generator online")
    if getattr(arguments, "fallback_local", None) is not None:
        parser.error("--fallback-local is only valid with --generator online")


async def _dispatch(
    arguments: argparse.Namespace,
    application: ApplicationPort,
) -> tuple[str, int]:
    if arguments.command == "kb":
        outcome = await application.build_knowledge(arguments.path)
        return render_command_outcome(outcome), _outcome_exit_code(outcome.status)
    if arguments.command == "rag":
        result = await application.ask_rag(
            RagAskRequest(
                question=arguments.question,
                generator=cast(GeneratorChoice, arguments.generator),
                provider=arguments.provider,
                fallback_local=arguments.fallback_local,
            )
        )
        return (
            render_rag_result(
                result,
                output_format=arguments.format,
                include_trace=arguments.trace,
            ),
            _result_exit_code(result),
        )
    if arguments.command == "agent":
        result = await application.run_agent(
            AgentRunRequest(
                task=arguments.task,
                generator=cast(GeneratorChoice, arguments.generator),
                provider=arguments.provider,
                fallback_local=arguments.fallback_local,
            )
        )
        return (
            render_agent_result(
                result,
                output_format=arguments.format,
                include_trace=arguments.trace,
            ),
            _result_exit_code(result),
        )
    if arguments.command == "eval":
        outcome = await application.run_evaluation(
            cast(EvaluationLayer, arguments.layer)
        )
        return render_command_outcome(outcome), _outcome_exit_code(outcome.status)
    if arguments.command == "doctor":
        outcome = await application.run_diagnostics()
        return render_command_outcome(outcome), _outcome_exit_code(outcome.status)
    raise RuntimeError("unsupported command")


def _result_exit_code(result: RagResult | AgentResult) -> int:
    return 1 if result.status == "failed" else 0


def _outcome_exit_code(status: str) -> int:
    return 1 if status == "failed" else 0


def _public_error(error: Exception) -> str:
    category = getattr(error, "category", None)
    if isinstance(error, ConfigurationError):
        return f"error: {category}: {error}"
    if category is not None:
        return f"error: {category}"
    return "error: operation failed"
