"""The one authorized real-online panel acceptance: three scenes x one round.

This is the only test in the repository that may reach a real provider. It never
runs without ``RAG_AGENT_ONLINE_API_KEY``, and it never reads, prints, or stores
that value: the profile only ever names the environment variable.
"""

from __future__ import annotations

import os

import pytest

from scripts.collect_panel_online_acceptance import (
    RUNS_PER_SCENE,
    SCENES,
    run_online_acceptance,
)

API_KEY_ENV = "RAG_AGENT_ONLINE_API_KEY"


@pytest.mark.live
@pytest.mark.asyncio
async def test_panel_online_three_scenes_in_one_authorized_round() -> None:
    if not os.environ.get(API_KEY_ENV):
        pytest.fail(
            f"{API_KEY_ENV} is not configured: the one authorized real-online "
            "panel run has not happened. Set it in the environment of the process "
            "that runs this test; it is never passed through the repository."
        )

    measurements = await run_online_acceptance(RUNS_PER_SCENE)

    # Every scene answered/refused exactly as asserted, in its single round. The
    # message carries the sanitized category so a provider rejection is reported
    # as a finding rather than retried blindly.
    for scene in SCENES:
        assert measurements["failures"][scene] == [], (
            f"scene {scene!r}: {measurements['failures'][scene]}; "
            f"outcomes={measurements['outcomes'][scene]}, "
            f"error_categories={measurements['error_categories'][scene]}"
        )
        assert len(measurements["per_scene"][scene]) == RUNS_PER_SCENE, scene

    # The negative that matters most: a replayed or mismatched authorization is
    # refused before any provider client is created.
    checks = measurements["negative_checks"]
    assert checks["replayed_authorization_is_refused"] is True
    assert checks["mismatched_fingerprint_is_refused"] is True
    assert checks["no_provider_request_during_the_negative_checks"] is True

    # The run stayed inside its authorized request budget and really did call out.
    requests = measurements["total_provider_requests"]
    assert requests > 0
    assert requests <= measurements["profile"]["request_limit"]
    assert measurements["network_allowed_for_this_run"] is True
    print(
        "online acceptance:",
        {
            "profile": measurements["profile"]["name"],
            "requests": requests,
            "cost_upper_bound_cny": measurements["cost_upper_bound_cny"],
            "outcomes": measurements["outcomes"],
        },
    )
