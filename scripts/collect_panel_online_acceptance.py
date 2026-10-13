"""Write the one authorized real-online acceptance artifact for the panel.

This is the only script in the repository that is allowed to reach a real
provider, and only for the single run the plan authorizes: three scenes x one
round against the registered relay profile, under a request cap and a cost
ceiling, over synthetic sample data only.

It reuses the pure helpers from ``collect_panel_acceptance`` rather than writing
a second schema, statistics, or promotion implementation.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

if __package__ in {None, ""}:
    # Running this file directly puts ``scripts/`` on sys.path, not the repository
    # root, so the shared helper package would not resolve.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.collect_panel_acceptance import (
    _SCENARIO_IDS,
    ASSERTIONS,
    SCENES,
    _assert_progress_matches_trace,
    atomic_write,
    git_commit,
    latency_summary,
    peak_working_set_bytes,
    process_vram_mib,
    scan_forbidden_content,
    validate_against,
)

SCHEMA_VERSION = 1
EXECUTION_SURFACE = "panel-coordinator"
BACKEND_PROVENANCE = "real-online"
RUNS_PER_SCENE = 1

# Recorded verbatim; nothing here may ever describe the relay as verified.
CAVEATS = (
    "the model id appears in the relay's own model list (checked 2026-10-12), "
    "but whether the relay actually serves that vendor's model is not verified",
    "the privacy/terms URL resolves to a real route whose body is empty",
    "per-token prices are an assumed conservative upper bound, so cost figures "
    "are estimates, not measurements",
    "the provider is a third-party relay, not the model vendor",
)

ONLINE_ARTIFACT_SCHEMA: dict[str, object] = {
    "schema_version": int,
    "generated_at_utc": str,
    "git_commit": str,
    "execution_surface": str,
    "backend_provenance": str,
    "profile_name": str,
    "profile_fingerprint": str,
    "base_url_host": str,
    "effective_base_url": str,
    "model_id": str,
    "model_identity_verified": bool,
    "privacy_terms_body_empty": bool,
    "pricing_is_assumed_upper_bound": bool,
    "provider_is_third_party_relay": bool,
    "caveats": list,
    "total_provider_requests": int,
    "configured_request_cap": int,
    "cost_upper_bound_cny": str,
    "cost_basis": str,
    "scenarios": dict,
    "assertions": dict,
    "negative_checks": dict,
    "network_allowed_for_this_run": bool,
    "peak_ram_bytes": (int, type(None)),
    "peak_vram_mib": (int, type(None)),
}


def validate_online_artifact(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("artifact must be a JSON object")
    validate_against(ONLINE_ARTIFACT_SCHEMA, payload)
    if payload["schema_version"] != SCHEMA_VERSION:
        raise ValueError("unexpected artifact schema version")
    if payload["execution_surface"] != EXECUTION_SURFACE:
        raise ValueError("unexpected execution surface")
    if payload["backend_provenance"] != BACKEND_PROVENANCE:
        raise ValueError("unexpected backend provenance")
    if set(payload["scenarios"]) != set(SCENES):
        raise ValueError("artifact must describe exactly the three scenes")
    if payload["caveats"] != list(CAVEATS):
        raise ValueError("the four caveats must be recorded verbatim")
    if payload["model_identity_verified"] is not False:
        raise ValueError("the model identity was never verified")
    return payload


def promote(payload: object) -> dict[str, Any]:
    """Validate and sanitize an online artifact for committing."""

    artifact = validate_online_artifact(payload)
    scan_forbidden_content(artifact)
    return artifact


async def run_online_acceptance(rounds: int = RUNS_PER_SCENE) -> dict[str, Any]:
    """One round of each scene through the real online panel path."""

    from enterprise_rag.config import AppConfig
    from enterprise_rag.panel.controller import PanelController
    from enterprise_rag.panel.coordinator import RunCoordinator
    from enterprise_rag.panel.runtime import PanelRuntime, RunAuthorizationError
    from enterprise_rag.provider_profiles import profile_fingerprint

    config = AppConfig.from_env()
    runtime = PanelRuntime(config)
    coordinator = RunCoordinator(runtime)
    controller = PanelController(coordinator, runtime)
    try:
        await coordinator.initialize()
        registry = runtime.registry
        profiles = registry.profiles
        if len(profiles) != 1:
            raise RuntimeError("exactly one provider profile must be registered")
        profile = profiles[0]
        controller.select_generator("online")
        if controller.view.provider_name != profile.name:
            controller.select_provider(profile.name)

        per_scene: dict[str, list[float]] = {scene: [] for scene in SCENES}
        outcomes: dict[str, list[str]] = {scene: [] for scene in SCENES}
        categories: dict[str, list[str | None]] = {scene: [] for scene in SCENES}
        errors: dict[str, list[str | None]] = {scene: [] for scene in SCENES}
        failures: dict[str, list[str]] = {scene: [] for scene in SCENES}
        for round_index in range(rounds):
            for scene in SCENES:
                controller.select_scenario(_SCENARIO_IDS[scene])
                started = time.perf_counter()
                confirmation = await controller.prepare_run()
                if confirmation is None:
                    failures[scene].append(
                        f"round {round_index + 1}: the run was blocked "
                        "before confirmation"
                    )
                    continue
                run_id = await controller.confirm_online()
                assert run_id is not None
                record = await coordinator.wait(run_id)
                per_scene[scene].append(time.perf_counter() - started)
                outcomes[scene].append(record.state)
                # Both of these are already sanitized: a stable error category and
                # a public message that never carries a key, prompt, or traceback.
                categories[scene].append(record.error_category)
                errors[scene].append(record.public_error)
                try:
                    ASSERTIONS[scene](record)
                    _assert_progress_matches_trace(record)
                except AssertionError as error:
                    failures[scene].append(
                        f"round {round_index + 1}: {error} "
                        f"(state={record.state}, "
                        f"error_category={record.error_category}, "
                        f"public_error={record.public_error})"
                    )

        online_backend = await registry.get_online(profile)
        usage = await online_backend.usage_snapshot()
        before = usage.request_number
        negative = await _negative_checks(
            registry,
            profile,
            profile_fingerprint,
            RunAuthorizationError,
        )
        after = (await online_backend.usage_snapshot()).request_number
        negative["no_provider_request_during_the_negative_checks"] = after == before
    finally:
        await runtime.aclose()

    return {
        "per_scene": per_scene,
        "outcomes": outcomes,
        "error_categories": categories,
        "public_errors": errors,
        "failures": failures,
        "effective_base_url": _effective_base_url(profile),
        "profile": {
            "name": profile.name,
            "fingerprint": profile_fingerprint(profile),
            "host": urlsplit(profile.base_url).hostname or "",
            "model_id": profile.model_id,
            "request_limit": profile.request_limit,
        },
        "total_provider_requests": after,
        "cost_upper_bound_cny": str(usage.spent_cny),
        "negative_checks": negative,
        "network_allowed_for_this_run": config.allow_network,
    }


def _effective_base_url(profile: Any) -> str:
    """The URL the SDK client is actually built with, not just the configured one.

    Constructed locally with a placeholder key: this performs no network call and
    reads no credential, but it does show what the production code path hands to
    the SDK, which the raw profile value alone does not.
    """

    from enterprise_rag.generation import _create_async_openai

    client = _create_async_openai(
        api_key="probe",
        base_url=profile.base_url,
        timeout=5.0,
        max_retries=0,
    )
    return str(client.base_url)


async def _negative_checks(
    registry: Any,
    profile: Any,
    fingerprint_of: Any,
    authorization_error: type[Exception],
) -> dict[str, bool]:
    """Prove a replayed or mismatched authorization is refused before any call."""

    from enterprise_rag.panel.contracts import (
        RunRequest,
        RunSpec,
    )

    request = RunRequest(
        run_id="online-negative-check",
        created_at=0.0,
        mode="rag",
        scenario_id="online-negative-check",
        question_or_task="synthetic negative-check task",
        generator="online",
        provider_name=profile.name,
        provider_fingerprint=fingerprint_of(profile),
        fallback_local=False,
    )
    preflight = await registry.preflight(request)
    authorization = registry.authorize(preflight)
    spec = RunSpec(
        run_id=request.run_id,
        created_at=request.created_at,
        mode=request.mode,
        scenario_id=request.scenario_id,
        question_or_task=request.question_or_task,
        generator=request.generator,
        provider_name=request.provider_name,
        provider_fingerprint=request.provider_fingerprint,
        fallback_local=request.fallback_local,
        online_authorization=authorization,
    )
    checks: dict[str, bool] = {}
    await registry.backend_for(spec)
    try:
        await registry.backend_for(spec)
    except authorization_error:
        checks["replayed_authorization_is_refused"] = True
    else:
        checks["replayed_authorization_is_refused"] = False

    mismatched = RunRequest(
        run_id="online-negative-mismatch",
        created_at=0.0,
        mode="rag",
        scenario_id="online-negative-mismatch",
        question_or_task="synthetic negative-check task",
        generator="online",
        provider_name=profile.name,
        provider_fingerprint="0" * 64,
        fallback_local=False,
    )
    try:
        await registry.preflight(mismatched)
    except Exception:
        checks["mismatched_fingerprint_is_refused"] = True
    else:
        checks["mismatched_fingerprint_is_refused"] = False
    return checks


def collect(output: Path, rounds: int = RUNS_PER_SCENE) -> dict[str, Any]:
    """Run the authorized online acceptance and write the machine-local artifact."""

    if not os.environ.get("RAG_AGENT_ALLOW_NETWORK", "").lower() == "true":
        raise RuntimeError(
            "set RAG_AGENT_ALLOW_NETWORK=true for this one authorized run"
        )
    measurements = asyncio.run(run_online_acceptance(rounds))
    profile = measurements["profile"]
    artifact = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "git_commit": git_commit(),
        "execution_surface": EXECUTION_SURFACE,
        "backend_provenance": BACKEND_PROVENANCE,
        "profile_name": profile["name"],
        "profile_fingerprint": profile["fingerprint"],
        "base_url_host": profile["host"],
        "effective_base_url": measurements["effective_base_url"],
        "model_id": profile["model_id"],
        "model_identity_verified": False,
        "privacy_terms_body_empty": True,
        "pricing_is_assumed_upper_bound": True,
        "provider_is_third_party_relay": True,
        "caveats": list(CAVEATS),
        "total_provider_requests": measurements["total_provider_requests"],
        "configured_request_cap": profile["request_limit"],
        "cost_upper_bound_cny": measurements["cost_upper_bound_cny"],
        "cost_basis": (
            "assumed conservative per-token upper bound, not the relay's "
            "advertised pricing"
        ),
        "scenarios": {
            scene: {
                "outcomes": measurements["outcomes"][scene],
                "error_categories": measurements["error_categories"][scene],
                "public_errors": measurements["public_errors"][scene],
                "seconds": measurements["per_scene"][scene],
                "summary": latency_summary(measurements["per_scene"][scene])
                if measurements["per_scene"][scene]
                else None,
            }
            for scene in SCENES
        },
        "assertions": {
            scene: {
                "rounds": rounds,
                "failures": measurements["failures"][scene],
                "passed": not measurements["failures"][scene],
            }
            for scene in SCENES
        },
        "negative_checks": measurements["negative_checks"],
        "network_allowed_for_this_run": measurements["network_allowed_for_this_run"],
        "peak_ram_bytes": peak_working_set_bytes(),
        "peak_vram_mib": process_vram_mib(),
    }
    atomic_write(output, artifact)
    return artifact


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="collect-panel-online-acceptance")
    commands = parser.add_subparsers(dest="command", required=True)
    collect_parser = commands.add_parser("collect")
    collect_parser.add_argument("--output", type=Path, required=True)
    collect_parser.add_argument("--rounds", type=int, default=RUNS_PER_SCENE)
    promote_parser = commands.add_parser("promote")
    promote_parser.add_argument("--input", type=Path, required=True)
    promote_parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args(argv)

    if arguments.command == "collect":
        artifact = collect(arguments.output, arguments.rounds)
        print(json.dumps(artifact, ensure_ascii=False, sort_keys=True))
        return 0
    payload = json.loads(arguments.input.read_text(encoding="utf-8"))
    atomic_write(arguments.output, promote(payload))
    print(f"promoted {arguments.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
