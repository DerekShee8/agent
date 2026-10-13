from __future__ import annotations

import json
from pathlib import Path

import pytest

from enterprise_rag.agent_tools import ToolRegistry
from enterprise_rag.errors import (
    ToolExecutionError,
    ToolNotFoundError,
    ToolValidationError,
)

RECORDS_ROOT = Path("data/sample_records")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool_name",
    [
        "delete_expense_claim",
        "../get_expense_claim",
        "https://example.invalid/tool",
        "C:\\records\\claims.json",
    ],
)
async def test_unknown_or_path_like_tool_names_are_rejected(tool_name: str) -> None:
    registry = ToolRegistry.from_directory(RECORDS_ROOT)

    with pytest.raises(ToolNotFoundError, match="allowlisted") as captured:
        await registry.call(tool_name, {"claim_id": "CLAIM-1024"})

    assert tool_name not in str(captured.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"claim_id": "CLAIM-1024", "extra": True},
        {"wrong_id": "CLAIM-1024"},
        {"claim_id": "CLAIM-24"},
        {"claim_id": "claim-1024"},
        {"claim_id": "https://example.invalid/CLAIM-1024"},
        {"claim_id": "../CLAIM-1024"},
        {"claim_id": Path("CLAIM-1024")},
        {"claim_id": {"value": "CLAIM-1024"}},
        {"claim_id": ["CLAIM-1024"]},
    ],
)
async def test_tool_arguments_reject_unexpected_malformed_or_nested_values(
    arguments: dict[str, object],
) -> None:
    registry = ToolRegistry.from_directory(RECORDS_ROOT)

    with pytest.raises(ToolValidationError, match="arguments are invalid"):
        await registry.call("get_expense_claim", arguments)


@pytest.mark.asyncio
async def test_lookup_tools_do_not_modify_source_files() -> None:
    paths = tuple(sorted(RECORDS_ROOT.glob("*.json")))
    before = {path: path.read_bytes() for path in paths}
    registry = ToolRegistry.from_directory(RECORDS_ROOT)

    await registry.call("get_employee_profile", {"employee_id": "EMP-003"})
    await registry.call("get_expense_claim", {"claim_id": "CLAIM-1024"})
    await registry.call("get_business_trip", {"trip_id": "TRIP-2409-017"})

    assert {path: path.read_bytes() for path in paths} == before


def test_corrupted_json_is_a_sanitized_typed_execution_failure(
    tmp_path: Path,
) -> None:
    _copy_valid_store(tmp_path)
    secret = "secret-record-content"
    (tmp_path / "claims.json").write_text("{" + secret, encoding="utf-8")

    with pytest.raises(ToolExecutionError) as captured:
        ToolRegistry.from_directory(tmp_path)

    assert str(tmp_path) not in str(captured.value)
    assert secret not in str(captured.value)


def test_duplicate_record_ids_fail_closed(tmp_path: Path) -> None:
    _copy_valid_store(tmp_path)
    document = _load_json(tmp_path / "employees.json")
    employees = document["employees"]
    assert isinstance(employees, list)
    employees.append(dict(employees[0]))
    _write_json(tmp_path / "employees.json", document)

    with pytest.raises(ToolExecutionError, match="unavailable"):
        ToolRegistry.from_directory(tmp_path)


def test_missing_foreign_keys_fail_closed(tmp_path: Path) -> None:
    _copy_valid_store(tmp_path)
    document = _load_json(tmp_path / "claims.json")
    claims = document["claims"]
    assert isinstance(claims, list)
    claim = claims[0]
    assert isinstance(claim, dict)
    claim["employee_id"] = "EMP-999"
    _write_json(tmp_path / "claims.json", document)

    with pytest.raises(ToolExecutionError, match="unavailable"):
        ToolRegistry.from_directory(tmp_path)


def _copy_valid_store(destination: Path) -> None:
    for source in RECORDS_ROOT.glob("*.json"):
        (destination / source.name).write_bytes(source.read_bytes())


def _load_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
