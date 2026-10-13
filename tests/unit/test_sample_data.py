from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

DATA_ROOT = Path("data")
DOCS_ROOT = DATA_ROOT / "sample_docs"
RECORDS_ROOT = DATA_ROOT / "sample_records"
EXPECTED_PATH = Path("tests/fixtures/sample_expected.json")

DOCUMENT_PATHS = (
    DOCS_ROOT / "travel-policy.md",
    DOCS_ROOT / "expense-policy.md",
    DOCS_ROOT / "expense-faq.md",
)
RECORD_PATHS = {
    "employees": RECORDS_ROOT / "employees.json",
    "claims": RECORDS_ROOT / "claims.json",
    "trips": RECORDS_ROOT / "trips.json",
}

EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_PATTERN = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")
GOVERNMENT_ID_PATTERN = re.compile(r"(?<!\d)\d{17}[0-9Xx](?!\d)")
FORBIDDEN_PERSONAL_FIELDS = {
    "email",
    "phone",
    "phone_number",
    "mobile",
    "government_id",
    "national_id",
    "identity_card",
    "id_card",
}


def load_records(kind: str) -> list[dict[str, object]]:
    document = json.loads(RECORD_PATHS[kind].read_text(encoding="utf-8"))
    assert set(document) == {kind}
    records = document[kind]
    assert isinstance(records, list)
    assert all(isinstance(record, dict) for record in records)
    return records


def by_id(
    records: list[dict[str, object]], id_field: str
) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for record in records:
        record_id = record[id_field]
        assert isinstance(record_id, str)
        assert record_id not in result
        result[record_id] = record
    return result


def walk_values(value: object):
    if isinstance(value, dict):
        for key, nested in value.items():
            yield key
            yield from walk_values(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from walk_values(nested)
    elif isinstance(value, str):
        yield value


def test_normative_corpus_contains_exact_policy_facts_and_distractors() -> None:
    corpus = "\n".join(path.read_text(encoding="utf-8") for path in DOCUMENT_PATHS)

    required_facts = (
        "POLICY-TRAVEL-04 §3.2",
        "P4",
        "tier-A",
        "CNY 600",
        "POLICY-TRAVEL-04 §3.3",
        "VP preapproval",
        "POLICY-EXPENSE-02 §2.1",
        "CNY 50",
        "POLICY-EXPENSE-02 §4.1",
        "POLICY-EXPENSE-02 §5.2",
        "10 个自然日",
    )
    for fact in required_facts:
        assert fact in corpus

    assert "P5" in corpus
    assert "非 A 类城市" in corpus
    assert "本项目中的公司、员工、行程与报销记录均为虚构" in corpus


def test_claim_1024_matches_employee_trip_policy_and_expected_fixture() -> None:
    employees = by_id(load_records("employees"), "employee_id")
    claims = by_id(load_records("claims"), "claim_id")
    trips = by_id(load_records("trips"), "trip_id")
    expected = json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))[
        "canonical_case"
    ]

    employee = employees["EMP-003"]
    claim = claims["CLAIM-1024"]
    trip = trips["TRIP-2409-017"]

    assert employee["name"] == "林晓雪"
    assert employee["department"] == "解决方案工程部"
    assert employee["grade"] == "P4"
    assert claim["employee_id"] == employee["employee_id"]
    assert claim["trip_id"] == trip["trip_id"]
    assert claim["hotel_nightly_amount"] == 680
    assert claim["hotel_nights"] == 2
    assert claim["transport_amount"] == 120
    assert claim["receipt_present"] is True
    assert claim["status"] == "rejected"
    assert claim["rejection_code"] == "HOTEL_OVER_LIMIT"
    assert trip["destination"] == "上海"
    assert trip["city_tier"] == "A"
    assert trip["vp_exception_approved"] is False

    assert expected["employee_id"] == employee["employee_id"]
    assert expected["claim_id"] == claim["claim_id"]
    assert expected["trip_id"] == trip["trip_id"]
    assert expected["hotel_limit_cny"] == 600
    assert expected["hotel_over_limit_cny"] == 80
    assert expected["expected_rejection_code"] == claim["rejection_code"]
    assert expected["required_policy_sections"] == [
        "POLICY-TRAVEL-04 §3.2",
        "POLICY-TRAVEL-04 §3.3",
        "POLICY-EXPENSE-02 §4.1",
    ]


def test_sample_records_have_unique_ids_and_referential_integrity() -> None:
    employee_records = load_records("employees")
    claim_records = load_records("claims")
    trip_records = load_records("trips")
    employees = by_id(employee_records, "employee_id")
    claims = by_id(claim_records, "claim_id")
    trips = by_id(trip_records, "trip_id")

    assert len(employees) >= 3
    assert len(claims) >= 3
    assert len(trips) >= 3
    assert set(claims) != {"CLAIM-1024"}

    for claim in claim_records:
        assert claim["employee_id"] in employees
        assert claim["trip_id"] in trips
    assert {claim["trip_id"] for claim in claim_records} == set(trips)


def test_sample_amounts_dates_and_statuses_are_well_formed() -> None:
    claims = load_records("claims")
    trips = load_records("trips")

    for claim in claims:
        for field in (
            "hotel_nightly_amount",
            "hotel_nights",
            "transport_amount",
        ):
            value = claim[field]
            assert type(value) is int
            assert value >= 0
        assert claim["status"] in {"approved", "rejected", "submitted"}
        if claim["status"] == "rejected":
            assert isinstance(claim["rejection_code"], str)
            assert claim["rejection_code"]
        else:
            assert claim["rejection_code"] is None

    for trip in trips:
        start = date.fromisoformat(str(trip["start_date"]))
        end = date.fromisoformat(str(trip["end_date"]))
        assert start <= end
        assert trip["city_tier"] in {"A", "B", "C"}
        assert type(trip["budget"]) is int
        assert trip["budget"] >= 0


def test_sample_data_has_no_real_contact_or_government_id_shape() -> None:
    payloads = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (*RECORD_PATHS.values(), EXPECTED_PATH)
    ]
    payloads.extend(path.read_text(encoding="utf-8") for path in DOCUMENT_PATHS)

    for payload in payloads:
        for value in walk_values(payload):
            assert value.casefold() not in FORBIDDEN_PERSONAL_FIELDS
            assert EMAIL_PATTERN.search(value) is None
            assert PHONE_PATTERN.search(value) is None
            assert GOVERNMENT_ID_PATTERN.search(value) is None
