from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from enterprise_rag.execution_trace import RunTrace
from enterprise_rag.models import RagResult, Source
from enterprise_rag.panel.contracts import (
    InitializationStage,
    OnlineRunAuthorization,
    PanelRunRecord,
    RunRequest,
    RunSpec,
)

_QUESTION = "P4 员工去上海出差的每晚住宿上限是多少？"  # noqa: RUF001


def _local_request(run_id: str = "run-1") -> RunRequest:
    return RunRequest(
        run_id=run_id,
        created_at=100.0,
        mode="rag",
        scenario_id="rag-shanghai-p4",
        question_or_task=_QUESTION,
        generator="local",
        provider_name=None,
        provider_fingerprint=None,
        fallback_local=False,
    )


def _online_request(run_id: str = "run-1") -> RunRequest:
    return RunRequest(
        run_id=run_id,
        created_at=100.0,
        mode="agent",
        scenario_id="agent-claim-1024",
        question_or_task="解释 EMP-003 的 CLAIM-1024 为什么被拒绝。",
        generator="online",
        provider_name="acme",
        provider_fingerprint="fp-1",
        fallback_local=False,
    )


def _authorization(
    *,
    run_id: str = "run-1",
    profile_fingerprint: str = "fp-1",
    fallback_local: bool = False,
) -> OnlineRunAuthorization:
    return OnlineRunAuthorization(
        authorization_id="auth-1",
        run_id=run_id,
        profile_fingerprint=profile_fingerprint,
        max_requests=2,
        input_token_cap=4_000,
        output_token_cap=1_000,
        max_cost_cny=Decimal("1.50"),
        synthetic_data_only=True,
        fallback_local=fallback_local,
    )


def _local_spec(run_id: str = "run-1") -> RunSpec:
    request = _local_request(run_id)
    return RunSpec(
        run_id=request.run_id,
        created_at=request.created_at,
        mode=request.mode,
        scenario_id=request.scenario_id,
        question_or_task=request.question_or_task,
        generator=request.generator,
        provider_name=request.provider_name,
        provider_fingerprint=request.provider_fingerprint,
        fallback_local=request.fallback_local,
        online_authorization=None,
    )


def _online_spec(
    *,
    run_id: str = "run-1",
    fallback_local: bool = False,
    authorization: OnlineRunAuthorization | None = None,
) -> RunSpec:
    return RunSpec(
        run_id=run_id,
        created_at=100.0,
        mode="agent",
        scenario_id="agent-claim-1024",
        question_or_task="解释 EMP-003 的 CLAIM-1024 为什么被拒绝。",
        generator="online",
        provider_name="acme",
        provider_fingerprint="fp-1",
        fallback_local=fallback_local,
        online_authorization=(
            authorization
            if authorization is not None
            else _authorization(run_id=run_id, fallback_local=fallback_local)
        ),
    )


def _trace(run_id: str = "run-1") -> RunTrace:
    return RunTrace(run_id=run_id, status="succeeded", events=())


def _answered_result(trace: RunTrace) -> RagResult:
    return RagResult.answered(
        "每晚上限为 CNY 600。",
        (Source("travel-policy.md", "travel-policy.md::0000", 0.91),),
        trace,
    )


def _failed_result(trace: RunTrace) -> RagResult:
    return RagResult.failed("generation_protocol", trace)


def _record(**overrides: object) -> PanelRunRecord:
    values: dict[str, object] = {
        "run_id": "run-1",
        "request": _local_request(),
        "spec": _local_spec(),
        "state": "answered",
        "result": _answered_result(_trace()),
        "trace": _trace(),
        "progress_events": (),
        "initialization_duration_ms": 1200.0,
        "run_duration_ms": 800.0,
        "error_category": None,
        "public_error": None,
    }
    values.update(overrides)
    return PanelRunRecord(**values)  # ty: ignore[invalid-argument-type]


def test_local_run_rejects_provider_and_authorization() -> None:
    with pytest.raises(ValueError, match="local run cannot select a provider"):
        RunRequest(
            run_id="run-1",
            created_at=100.0,
            mode="rag",
            scenario_id="scenario-1",
            question_or_task=_QUESTION,
            generator="local",
            provider_name="acme",
            provider_fingerprint=None,
            fallback_local=False,
        )

    with pytest.raises(ValueError, match="local run cannot carry an authorization"):
        RunSpec(
            run_id="run-1",
            created_at=100.0,
            mode="rag",
            scenario_id="scenario-1",
            question_or_task=_QUESTION,
            generator="local",
            provider_name=None,
            provider_fingerprint=None,
            fallback_local=False,
            online_authorization=_authorization(),
        )

    with pytest.raises(ValueError, match="local run cannot enable fallback"):
        RunRequest(
            run_id="run-1",
            created_at=100.0,
            mode="rag",
            scenario_id="scenario-1",
            question_or_task=_QUESTION,
            generator="local",
            provider_name=None,
            provider_fingerprint=None,
            fallback_local=True,
        )


def test_online_run_requires_matching_authorization() -> None:
    with pytest.raises(ValueError, match="online run requires an authorization"):
        RunSpec(
            run_id="run-1",
            created_at=100.0,
            mode="agent",
            scenario_id="scenario-1",
            question_or_task="task",
            generator="online",
            provider_name="acme",
            provider_fingerprint="fp-1",
            fallback_local=False,
            online_authorization=None,
        )

    with pytest.raises(ValueError, match="online run requires a provider"):
        RunRequest(
            run_id="run-1",
            created_at=100.0,
            mode="agent",
            scenario_id="scenario-1",
            question_or_task="task",
            generator="online",
            provider_name=None,
            provider_fingerprint=None,
            fallback_local=False,
        )


def test_authorization_must_bind_run_profile_and_fallback() -> None:
    with pytest.raises(ValueError, match="authorization must bind the run id"):
        _online_spec(authorization=_authorization(run_id="run-2"))

    with pytest.raises(ValueError, match="authorization must bind the profile"):
        _online_spec(authorization=_authorization(profile_fingerprint="fp-2"))

    with pytest.raises(ValueError, match="authorization must bind the fallback flag"):
        _online_spec(authorization=_authorization(fallback_local=True))

    assert _online_spec().online_authorization is not None


def test_blocked_record_has_request_category_but_no_spec_result_or_trace() -> None:
    blocked = _record(
        spec=None,
        state="blocked",
        result=None,
        trace=None,
        error_category="network_disabled",
        public_error="network is disabled for local runs",
    )
    assert blocked.spec is None
    assert blocked.result is None
    assert blocked.trace is None

    with pytest.raises(ValueError, match="blocked record cannot carry a spec"):
        _record(
            state="blocked",
            result=None,
            trace=None,
            error_category="network_disabled",
            public_error="network is disabled for local runs",
        )

    with pytest.raises(ValueError, match="blocked record requires an error_category"):
        _record(spec=None, state="blocked", result=None, trace=None, public_error="why")

    with pytest.raises(ValueError, match="blocked record requires a public_error"):
        _record(
            spec=None,
            state="blocked",
            result=None,
            trace=None,
            error_category="network_disabled",
        )


def test_answered_record_requires_matching_spec_result_and_trace() -> None:
    assert _record().state == "answered"

    with pytest.raises(ValueError, match="answered record requires a spec"):
        _record(spec=None)

    with pytest.raises(ValueError, match="answered record requires a business result"):
        _record(result=None)

    with pytest.raises(ValueError, match="answered record requires a completed trace"):
        _record(trace=None)

    with pytest.raises(ValueError, match="record run_id must match the spec"):
        _record(spec=_local_spec(run_id="run-2"))

    with pytest.raises(ValueError, match="record trace must match the result trace"):
        _record(trace=RunTrace(run_id="run-1", status="degraded", events=()))

    with pytest.raises(
        ValueError, match="answered record cannot carry an error_category"
    ):
        _record(error_category="unexpected")


def test_business_failed_record_requires_matching_failed_result_and_trace() -> None:
    failed = _record(
        state="failed",
        result=_failed_result(_trace()),
        trace=_trace(),
        error_category="generation_protocol",
    )
    assert failed.state == "failed"

    with pytest.raises(ValueError, match="failed record requires a failed result"):
        _record(state="failed", error_category="generation_protocol")

    with pytest.raises(ValueError, match="failed record requires a completed trace"):
        _record(
            state="failed",
            result=_failed_result(_trace()),
            trace=None,
            error_category="generation_protocol",
        )

    with pytest.raises(
        ValueError, match="record category must match the result category"
    ):
        _record(
            state="failed",
            result=_failed_result(_trace()),
            trace=_trace(),
            error_category="other_category",
        )


def test_pre_service_failed_record_requires_public_error_and_no_result() -> None:
    pre_service = _record(
        state="failed",
        result=None,
        trace=None,
        error_category="binding_error",
        public_error="the run could not be started",
    )
    assert pre_service.result is None
    assert pre_service.trace is None

    with pytest.raises(ValueError, match="pre-service failure cannot carry a trace"):
        _record(
            state="failed",
            result=None,
            trace=_trace(),
            error_category="binding_error",
            public_error="the run could not be started",
        )

    with pytest.raises(ValueError, match="failed record requires a public_error"):
        _record(
            state="failed",
            result=None,
            trace=None,
            error_category="binding_error",
        )


def test_cancelled_record_preserves_spec_and_public_prefix_without_result() -> None:
    cancelled = _record(
        state="cancelled",
        result=None,
        trace=None,
        progress_events=(),
    )
    assert cancelled.spec is not None
    assert cancelled.result is None
    assert cancelled.trace is None

    with pytest.raises(ValueError, match="cancelled record requires a spec"):
        _record(state="cancelled", spec=None, result=None, trace=None)

    with pytest.raises(ValueError, match="cancelled record cannot carry a result"):
        _record(state="cancelled", trace=None)

    with pytest.raises(
        ValueError, match="cancelled record cannot carry a completed trace"
    ):
        _record(state="cancelled", result=None, trace=_trace())

    with pytest.raises(
        ValueError, match="cancelled record cannot carry an error_category"
    ):
        _record(
            state="cancelled",
            result=None,
            trace=None,
            error_category="unexpected",
        )


def test_failed_initialization_stage_requires_category_and_message() -> None:
    failed = InitializationStage(
        name="LOADING_INDEX",
        status="failed",
        duration_ms=12.0,
        error_category="missing_artifact",
        public_error="the index artifact is missing",
    )
    assert failed.status == "failed"

    with pytest.raises(ValueError, match="failed stage requires an error_category"):
        InitializationStage(
            name="LOADING_INDEX",
            status="failed",
            duration_ms=12.0,
            public_error="the index artifact is missing",
        )

    with pytest.raises(ValueError, match="failed stage requires a public_error"):
        InitializationStage(
            name="LOADING_INDEX",
            status="failed",
            duration_ms=12.0,
            error_category="missing_artifact",
        )

    with pytest.raises(
        ValueError, match="non-failed stage cannot carry an error_category"
    ):
        InitializationStage(
            name="READY",
            status="succeeded",
            duration_ms=12.0,
            error_category="missing_artifact",
        )


def test_run_request_and_spec_are_frozen() -> None:
    request = _local_request()
    spec = _local_spec()

    with pytest.raises(FrozenInstanceError):
        setattr(request, "run_id", "run-2")  # noqa: B010
    with pytest.raises(FrozenInstanceError):
        setattr(spec, "run_id", "run-2")  # noqa: B010

    assert not hasattr(request, "__dict__")
    assert not hasattr(spec, "__dict__")
