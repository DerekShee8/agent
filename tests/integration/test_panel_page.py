"""The NiceGUI engineering workbench, driven through the locked test harness.

A missing approved panel extra is an explicit failure here: these tests never
call ``pytest.importorskip``.
"""

from __future__ import annotations

import pytest

MAIN_FILE = "tests/integration/panel_main.py"
RETRIES = 25

pytestmark = pytest.mark.nicegui_main_file(MAIN_FILE)


@pytest.fixture
def initialization_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_PANEL_TEST_READY", "false")


@pytest.fixture
def refused_then_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_PANEL_TEST_OUTCOMES", "refused,failed")


async def see(user, text: str) -> None:
    await user.should_see(text, retries=RETRIES)


async def see_not(user, text: str) -> None:
    await user.should_not_see(text, retries=RETRIES)


def element(user, marker: str):
    found = user.find(marker=marker).elements
    assert found, f"no element marked {marker!r}"
    return next(iter(found))


async def open_ready_page(user) -> None:
    await user.open("/")
    await see(user, "Panel state: ready")
    await see(user, "rag-shanghai-p4")


async def run_preset(user, scenario_id: str = "rag-shanghai-p4") -> None:
    user.find(marker=f"scenario-card-{scenario_id}").click()
    user.find(marker="run-button").click()


async def select_online(user) -> None:
    user.find(marker="scenario-card-rag-shanghai-p4").click()
    user.find(marker="generator-online").click()
    await see(user, "Provider profile")


async def test_page_renders_the_shell_and_initialization_stages(user) -> None:
    await user.open("/")

    await see(user, "Local engineering panel")
    await see(user, "Panel state: ready")
    await see(user, "CHECKING_CONFIG: succeeded")
    await see(user, "LOADING_INDEX: succeeded")
    await see(user, "READY: succeeded")


async def test_run_is_disabled_when_initialization_failed(
    initialization_failed: None,
    user,
) -> None:
    await user.open("/")

    await see(user, "Panel state: initialization_failed")
    assert element(user, "run-button").enabled is False


async def test_run_is_enabled_once_ready(user) -> None:
    await open_ready_page(user)

    assert element(user, "run-button").enabled is True


async def test_three_preset_cards_populate_without_auto_running(user) -> None:
    await open_ready_page(user)

    await see(user, "rag-shanghai-p4")
    await see(user, "agent-claim-1024")
    await see(user, "rag-pet-boarding-refusal")
    await see(user, "No run yet.")


async def test_selecting_a_preset_loads_its_text_without_starting_a_run(
    user,
) -> None:
    await open_ready_page(user)

    user.find(marker="scenario-card-agent-claim-1024").click()

    await see(user, "解释 EMP-003 的 CLAIM-1024 为什么被拒绝。")
    await see(user, "No run yet.")


async def test_custom_text_is_editable_and_a_run_uses_it(user) -> None:
    await open_ready_page(user)

    user.find(marker="custom-input").clear().type("a custom question")
    user.find(marker="run-button").click()

    await see(user, "Answered (check_circle)")
    await see(user, "The P4 hotel cap is CNY 600 per night.")


async def test_online_offers_only_registered_profiles_and_fallback_defaults_off(
    user,
) -> None:
    await open_ready_page(user)

    user.find(marker="generator-online").click()

    await see(user, "Provider profile")
    await see(user, "relay")
    await see(user, "Fall back to local after an online failure")
    assert element(user, "fallback-switch").value is False
    # The local/online choice is exclusive: the active one cannot be re-picked.
    assert element(user, "generator-online").enabled is False
    assert element(user, "generator-local").enabled is True


async def test_online_confirmation_shows_the_safety_envelope(user) -> None:
    await open_ready_page(user)

    await select_online(user)
    user.find(marker="run-button").click()

    await see(user, "Provider: relay")
    await see(user, "Model: provider-model")
    await see(user, "Endpoint: https://relay.example.invalid/v1")
    await see(user, "Privacy terms: https://relay.example.invalid/privacy")
    await see(user, "Request cap: 2 of 30")
    await see(user, "Cost ceiling: CNY 0.09")
    await see(user, "Fallback to local: False")
    assert element(user, "confirm-online")
    assert element(user, "cancel-online")


async def test_cancelling_the_confirmation_blocks_without_a_run(user) -> None:
    await open_ready_page(user)

    await select_online(user)
    user.find(marker="run-button").click()
    await see(user, "Provider: relay")

    user.find(marker="cancel-online").click()

    await see(user, "the operator declined the online confirmation")
    await see(user, "Blocked (lock)")
    await see_not(user, "Provider: relay")


async def test_a_local_run_renders_answer_sources_and_pipeline_nodes(user) -> None:
    await open_ready_page(user)

    await run_preset(user)

    await see(user, "Answered (check_circle)")
    await see(user, "source: travel-policy.md::0000")
    await see(user, "Retrieval")
    await see(user, "Generation")


async def test_selecting_a_node_changes_only_the_inspector(user) -> None:
    await open_ready_page(user)

    await run_preset(user)
    await see(user, "Generation")

    user.find(marker="pipeline-node-generation").click()

    await see(user, "Inspector: Generation")
    await see(user, "model id: local-model")
    # The result panel is untouched by node selection.
    await see(user, "The P4 hotel cap is CNY 600 per night.")


async def test_history_renders_and_selection_does_not_mutate_the_record(
    user,
) -> None:
    await open_ready_page(user)

    await run_preset(user)
    await see(user, "Answered (check_circle)")
    assert element(user, "recent-runs")

    user.find("Answered: rag-shanghai-p4").click()

    await see(user, "Answered (check_circle)")
    await see(user, "source: travel-policy.md::0000")


async def test_refused_and_failed_runs_keep_their_own_text_and_icon(
    refused_then_failed: None,
    user,
) -> None:
    await open_ready_page(user)

    await run_preset(user)
    await see(user, "Refused (blocked)")
    await see(user, "refusal: insufficient_evidence")

    user.find(marker="run-button").click()
    await see(user, "Failed (error)")


async def test_the_rendered_page_never_contains_a_forbidden_string(
    user,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RAG_AGENT_ONLINE_API_KEY", "sk-live-should-never-surface")
    await open_ready_page(user)
    await run_preset(user)
    await see(user, "Answered (check_circle)")

    rendered = "\n".join(repr(item) for item in user.find(kind=None).elements)

    for forbidden in (
        "sk-live-should-never-surface",
        "api_key",
        "system_prompt",
        "Traceback (most recent call last)",
    ):
        assert forbidden not in rendered


async def test_two_clients_share_one_initializer_and_history(
    user,
    create_user,
) -> None:
    await open_ready_page(user)
    await run_preset(user)
    await see(user, "Answered (check_circle)")

    second = create_user()
    await second.open("/")

    await see(second, "Answered (check_circle)")
    assert element(second, "initialization-stage-READY")
