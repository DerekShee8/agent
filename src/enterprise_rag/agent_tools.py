from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from types import MappingProxyType
from typing import Protocol

from .errors import ToolExecutionError, ToolNotFoundError, ToolValidationError
from .execution_trace import _freeze_mapping
from .models import Observation

_EMPLOYEE_ID_PATTERN = re.compile(r"EMP-[0-9]{3}")
_CLAIM_ID_PATTERN = re.compile(r"CLAIM-[0-9]{4}")
_TRIP_ID_PATTERN = re.compile(r"TRIP-[0-9]{4}-[0-9]{3}")

_EMPLOYEE_FIELDS = frozenset(
    {"employee_id", "name", "department", "grade", "travel_tier"}
)
_CLAIM_FIELDS = frozenset(
    {
        "claim_id",
        "employee_id",
        "trip_id",
        "hotel_nightly_amount",
        "hotel_nights",
        "transport_amount",
        "receipt_present",
        "status",
        "rejection_code",
    }
)
_TRIP_FIELDS = frozenset(
    {
        "trip_id",
        "destination",
        "city_tier",
        "start_date",
        "end_date",
        "approved",
        "vp_exception_approved",
        "budget",
    }
)


class _Tool(Protocol):
    name: str

    def validate(self, arguments: Mapping[str, object]) -> str: ...

    async def execute(self, record_id: str) -> Observation: ...


class RecordRepository:
    """Validated, immutable in-memory view of the fictional business records."""

    def __init__(
        self,
        *,
        employees: Mapping[str, Mapping[str, object]],
        claims: Mapping[str, Mapping[str, object]],
        trips: Mapping[str, Mapping[str, object]],
    ) -> None:
        self._employees = _freeze_table(employees)
        self._claims = _freeze_table(claims)
        self._trips = _freeze_table(trips)

    @classmethod
    def from_directory(cls, root: Path) -> RecordRepository:
        if not isinstance(root, Path):
            raise TypeError("record root must be a Path")
        try:
            employees = _load_table(
                root / "employees.json",
                root_key="employees",
                id_field="employee_id",
                id_pattern=_EMPLOYEE_ID_PATTERN,
                expected_fields=_EMPLOYEE_FIELDS,
                validator=_validate_employee,
            )
            claims = _load_table(
                root / "claims.json",
                root_key="claims",
                id_field="claim_id",
                id_pattern=_CLAIM_ID_PATTERN,
                expected_fields=_CLAIM_FIELDS,
                validator=_validate_claim,
            )
            trips = _load_table(
                root / "trips.json",
                root_key="trips",
                id_field="trip_id",
                id_pattern=_TRIP_ID_PATTERN,
                expected_fields=_TRIP_FIELDS,
                validator=_validate_trip,
            )
            _validate_foreign_keys(employees, claims, trips)
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            KeyError,
            TypeError,
            ValueError,
        ) as error:
            raise ToolExecutionError("business record store is unavailable") from error
        return cls(employees=employees, claims=claims, trips=trips)

    def employee(self, employee_id: str) -> Mapping[str, object] | None:
        return self._employees.get(employee_id)

    def claim(self, claim_id: str) -> Mapping[str, object] | None:
        return self._claims.get(claim_id)

    def trip(self, trip_id: str) -> Mapping[str, object] | None:
        return self._trips.get(trip_id)


class _LookupTool:
    def __init__(
        self,
        *,
        name: str,
        argument_name: str,
        id_pattern: re.Pattern[str],
        lookup: object,
    ) -> None:
        if not callable(lookup):
            raise TypeError("lookup must be callable")
        self.name = name
        self._argument_name = argument_name
        self._id_pattern = id_pattern
        self._lookup = lookup

    def validate(self, arguments: Mapping[str, object]) -> str:
        if not isinstance(arguments, Mapping) or set(arguments) != {
            self._argument_name
        }:
            raise ToolValidationError("tool arguments are invalid")
        value = arguments[self._argument_name]
        if not isinstance(value, str) or self._id_pattern.fullmatch(value) is None:
            raise ToolValidationError("tool arguments are invalid")
        return value

    async def execute(self, record_id: str) -> Observation:
        record = self._lookup(record_id)
        observation_id = f"obs:{self.name}:{record_id}"
        if record is None:
            return Observation(
                observation_id=observation_id,
                source=self.name,
                status="not_found",
                facts={},
                support_text=None,
            )
        if not isinstance(record, Mapping):
            raise ToolExecutionError("business tool execution failed")
        return Observation(
            observation_id=observation_id,
            source=self.name,
            status="found",
            facts=record,
            support_text=json.dumps(
                dict(record),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
        )


class ToolRegistry:
    """Allowlisted read-only business-tool dispatch."""

    def __init__(self, tools: tuple[_Tool, ...]) -> None:
        if not isinstance(tools, tuple) or not tools:
            raise ValueError("tools must be a non-empty tuple")
        by_name: dict[str, _Tool] = {}
        for tool in tools:
            if tool.name in by_name:
                raise ValueError("tool names must be unique")
            by_name[tool.name] = tool
        self._tools: Mapping[str, _Tool] = MappingProxyType(by_name)

    @classmethod
    def from_directory(cls, root: Path) -> ToolRegistry:
        repository = RecordRepository.from_directory(root)
        return cls(
            (
                _LookupTool(
                    name="get_employee_profile",
                    argument_name="employee_id",
                    id_pattern=_EMPLOYEE_ID_PATTERN,
                    lookup=repository.employee,
                ),
                _LookupTool(
                    name="get_expense_claim",
                    argument_name="claim_id",
                    id_pattern=_CLAIM_ID_PATTERN,
                    lookup=repository.claim,
                ),
                _LookupTool(
                    name="get_business_trip",
                    argument_name="trip_id",
                    id_pattern=_TRIP_ID_PATTERN,
                    lookup=repository.trip,
                ),
            )
        )

    @property
    def tool_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))

    async def call(
        self,
        name: str,
        arguments: Mapping[str, object],
    ) -> Observation:
        tool = self._tools.get(name) if isinstance(name, str) else None
        if tool is None:
            raise ToolNotFoundError("tool is not allowlisted")
        record_id = tool.validate(arguments)
        try:
            return await tool.execute(record_id)
        except ToolExecutionError:
            raise
        except Exception as error:
            raise ToolExecutionError("business tool execution failed") from error


def _load_table(
    path: Path,
    *,
    root_key: str,
    id_field: str,
    id_pattern: re.Pattern[str],
    expected_fields: frozenset[str],
    validator: object,
) -> dict[str, Mapping[str, object]]:
    if not callable(validator):
        raise TypeError("validator must be callable")
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or set(document) != {root_key}:
        raise ValueError("record document has an invalid root")
    records = document[root_key]
    if not isinstance(records, list):
        raise TypeError("record collection must be a list")

    table: dict[str, Mapping[str, object]] = {}
    for record in records:
        if not isinstance(record, dict) or set(record) != expected_fields:
            raise ValueError("record fields are invalid")
        record_id = record[id_field]
        if not isinstance(record_id, str) or id_pattern.fullmatch(record_id) is None:
            raise ValueError("record ID is invalid")
        if record_id in table:
            raise ValueError("record IDs must be unique")
        validator(record)
        table[record_id] = _freeze_mapping(record, redact_sensitive=False)
    if not table:
        raise ValueError("record collection must not be empty")
    return table


def _freeze_table(
    table: Mapping[str, Mapping[str, object]],
) -> Mapping[str, Mapping[str, object]]:
    return MappingProxyType(
        {
            key: _freeze_mapping(value, redact_sensitive=False)
            for key, value in table.items()
        }
    )


def _validate_employee(record: Mapping[str, object]) -> None:
    _require_nonblank(record["name"])
    _require_nonblank(record["department"])
    grade = record["grade"]
    if not isinstance(grade, str) or re.fullmatch(r"P[0-9]+", grade) is None:
        raise ValueError("employee grade is invalid")
    if record["travel_tier"] not in {"standard", "premium"}:
        raise ValueError("employee travel tier is invalid")


def _validate_claim(record: Mapping[str, object]) -> None:
    employee_id = record["employee_id"]
    trip_id = record["trip_id"]
    if (
        not isinstance(employee_id, str)
        or _EMPLOYEE_ID_PATTERN.fullmatch(employee_id) is None
    ):
        raise ValueError("claim employee ID is invalid")
    if not isinstance(trip_id, str) or _TRIP_ID_PATTERN.fullmatch(trip_id) is None:
        raise ValueError("claim trip ID is invalid")
    for field in ("hotel_nightly_amount", "hotel_nights", "transport_amount"):
        _require_nonnegative_integer(record[field])
    if type(record["receipt_present"]) is not bool:
        raise TypeError("claim receipt flag is invalid")
    status = record["status"]
    rejection_code = record["rejection_code"]
    if status not in {"approved", "rejected", "submitted"}:
        raise ValueError("claim status is invalid")
    if status == "rejected":
        _require_nonblank(rejection_code)
    elif rejection_code is not None:
        raise ValueError("non-rejected claim cannot have a rejection code")


def _validate_trip(record: Mapping[str, object]) -> None:
    _require_nonblank(record["destination"])
    if record["city_tier"] not in {"A", "B", "C"}:
        raise ValueError("trip city tier is invalid")
    start = date.fromisoformat(_require_nonblank(record["start_date"]))
    end = date.fromisoformat(_require_nonblank(record["end_date"]))
    if start > end:
        raise ValueError("trip dates are invalid")
    if type(record["approved"]) is not bool:
        raise TypeError("trip approval flag is invalid")
    if type(record["vp_exception_approved"]) is not bool:
        raise TypeError("trip exception flag is invalid")
    _require_nonnegative_integer(record["budget"])


def _validate_foreign_keys(
    employees: Mapping[str, Mapping[str, object]],
    claims: Mapping[str, Mapping[str, object]],
    trips: Mapping[str, Mapping[str, object]],
) -> None:
    for claim in claims.values():
        if claim["employee_id"] not in employees or claim["trip_id"] not in trips:
            raise ValueError("claim foreign key is invalid")


def _require_nonblank(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("record text is invalid")
    return value


def _require_nonnegative_integer(value: object) -> None:
    if type(value) is not int or value < 0:
        raise ValueError("record number is invalid")
