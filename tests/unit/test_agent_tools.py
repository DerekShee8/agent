from __future__ import annotations

from pathlib import Path

import pytest

from enterprise_rag.agent_tools import ToolRegistry

RECORDS_ROOT = Path("data/sample_records")


@pytest.fixture
def registry() -> ToolRegistry:
    return ToolRegistry.from_directory(RECORDS_ROOT)


@pytest.mark.asyncio
async def test_claim_tool_returns_stable_immutable_observation(
    registry: ToolRegistry,
) -> None:
    observation = await registry.call(
        "get_expense_claim",
        {"claim_id": "CLAIM-1024"},
    )

    assert observation.observation_id == "obs:get_expense_claim:CLAIM-1024"
    assert observation.source == "get_expense_claim"
    assert observation.status == "found"
    assert observation.payload["rejection_code"] == "HOTEL_OVER_LIMIT"
    assert observation.payload["employee_id"] == "EMP-003"
    assert observation.support_text is not None
    assert "CLAIM-1024" in observation.support_text
    with pytest.raises(TypeError):
        observation.payload["status"] = "approved"  # ty: ignore[index-assignment]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "arguments", "id_field", "expected_id"),
    [
        (
            "get_employee_profile",
            {"employee_id": "EMP-003"},
            "employee_id",
            "EMP-003",
        ),
        (
            "get_expense_claim",
            {"claim_id": "CLAIM-1024"},
            "claim_id",
            "CLAIM-1024",
        ),
        (
            "get_business_trip",
            {"trip_id": "TRIP-2409-017"},
            "trip_id",
            "TRIP-2409-017",
        ),
    ],
)
async def test_registry_exposes_only_the_three_read_only_lookup_tools(
    registry: ToolRegistry,
    tool_name: str,
    arguments: dict[str, object],
    id_field: str,
    expected_id: str,
) -> None:
    observation = await registry.call(tool_name, arguments)

    assert registry.tool_names == (
        "get_business_trip",
        "get_employee_profile",
        "get_expense_claim",
    )
    assert observation.payload[id_field] == expected_id


@pytest.mark.asyncio
async def test_well_formed_missing_record_returns_stable_not_found(
    registry: ToolRegistry,
) -> None:
    observation = await registry.call(
        "get_expense_claim",
        {"claim_id": "CLAIM-9999"},
    )

    assert observation.observation_id == "obs:get_expense_claim:CLAIM-9999"
    assert observation.status == "not_found"
    assert observation.payload == {}
    assert observation.support_text is None


@pytest.mark.asyncio
async def test_repository_files_are_loaded_once_and_never_reopened(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = Path.read_text
    reads: list[str] = []

    def recording_read(path: Path, *args: object, **kwargs: object) -> str:
        if path.parent == RECORDS_ROOT:
            reads.append(path.name)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", recording_read)
    registry = ToolRegistry.from_directory(RECORDS_ROOT)

    await registry.call("get_employee_profile", {"employee_id": "EMP-003"})
    await registry.call("get_employee_profile", {"employee_id": "EMP-003"})
    await registry.call("get_expense_claim", {"claim_id": "CLAIM-1024"})

    assert sorted(reads) == ["claims.json", "employees.json", "trips.json"]
