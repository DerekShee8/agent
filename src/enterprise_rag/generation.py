from __future__ import annotations

import asyncio
import hashlib
import importlib
import inspect
import json
import math
import re
import threading
import unicodedata
from collections.abc import Callable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from .config import (
    LOCAL_MODEL_FILENAME,
    LOCAL_MODEL_REPOSITORY_ID,
    LOCAL_MODEL_REVISION,
    AppConfig,
)
from .errors import (
    BudgetBlockedError,
    ConfigurationError,
    GenerationProtocolError,
    LocalModelMissingError,
    LocalModelResourceError,
    LocalModelTimeoutError,
    LocalModelUnavailableError,
    PrivacyBlockedError,
    ProfileNotAllowedError,
    ProviderAuthenticationError,
    ProviderBalanceError,
    ProviderInvalidRequestError,
    ProviderPermissionError,
    ProviderProtocolError,
    ProviderQuotaError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from .execution_trace import _freeze_mapping
from .models import (
    AgentAction,
    AnswerDraft,
    EvidenceClaim,
    Observation,
    RerankedChunk,
    StructuredGenerationRequest,
    ValidationIssue,
)
from .ports import StructuredGeneratorPort
from .provider_profiles import ProviderProfile, preflight_online_call

ANSWER_SCHEMA: Mapping[str, object] = _freeze_mapping(
    {
        "type": "object",
        "additionalProperties": False,
        "required": ["answer", "claims"],
        "properties": {
            "answer": {
                "type": "string",
                "minLength": 1,
                "pattern": r'^[^"\\\n\r]*[一-鿿][^"\\\n\r]*$',
            },
            "claims": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["text", "support_ids", "supporting_quotes"],
                    "properties": {
                        "text": {"type": "string", "minLength": 1},
                        "support_ids": {
                            "type": "array",
                            "minItems": 1,
                            "items": {"type": "string", "minLength": 1},
                        },
                        "supporting_quotes": {
                            "type": "array",
                            "minItems": 1,
                            "items": {"type": "string", "minLength": 1},
                        },
                    },
                },
            },
        },
    },
    redact_sensitive=False,
)

ACTION_SCHEMA: Mapping[str, object] = _freeze_mapping(
    {
        "oneOf": [
            {
                "type": "object",
                "required": ["action", "question"],
                "properties": {
                    "action": {"const": "search_knowledge"},
                    "question": {"type": "string", "minLength": 1},
                },
                "additionalProperties": False,
            },
            {
                "type": "object",
                "required": ["action", "employee_id"],
                "properties": {
                    "action": {"const": "get_employee_profile"},
                    "employee_id": {"type": "string", "pattern": "^EMP-[0-9]{3}$"},
                },
                "additionalProperties": False,
            },
            {
                "type": "object",
                "required": ["action", "claim_id"],
                "properties": {
                    "action": {"const": "get_expense_claim"},
                    "claim_id": {"type": "string", "pattern": "^CLAIM-[0-9]{4}$"},
                },
                "additionalProperties": False,
            },
            {
                "type": "object",
                "required": ["action", "trip_id"],
                "properties": {
                    "action": {"const": "get_business_trip"},
                    "trip_id": {
                        "type": "string",
                        "pattern": "^TRIP-[0-9]{4}-[0-9]{3}$",
                    },
                },
                "additionalProperties": False,
            },
            {
                "type": "object",
                "required": ["action", "draft"],
                "properties": {
                    "action": {"const": "finish"},
                    "draft": ANSWER_SCHEMA,
                },
                "additionalProperties": False,
            },
            {
                "type": "object",
                "required": ["action", "reason"],
                "properties": {
                    "action": {"const": "refuse"},
                    "reason": {"type": "string", "minLength": 1},
                },
                "additionalProperties": False,
            },
        ]
    },
    redact_sensitive=False,
)

FINAL_ACTION_SCHEMA: Mapping[str, object] = _freeze_mapping(
    {
        "oneOf": [
            {
                "type": "object",
                "required": ["action", "draft"],
                "properties": {
                    "action": {"const": "finish"},
                    "draft": ANSWER_SCHEMA,
                },
                "additionalProperties": False,
            },
            {
                "type": "object",
                "required": ["action", "reason"],
                "properties": {
                    "action": {"const": "refuse"},
                    "reason": {"type": "string", "minLength": 1},
                },
                "additionalProperties": False,
            },
        ]
    },
    redact_sensitive=False,
)

_ANSWER_FIELDS = frozenset({"answer", "claims"})
_CLAIM_FIELDS = frozenset({"text", "support_ids", "supporting_quotes"})
_ACTION_VALUE_FIELDS = {
    "search_knowledge": "question",
    "get_employee_profile": "employee_id",
    "get_expense_claim": "claim_id",
    "get_business_trip": "trip_id",
    "refuse": "reason",
}
_BUSINESS_ID_ACTIONS = (
    (
        "get_expense_claim",
        "claim_id",
        re.compile(r"(?<![A-Z0-9-])CLAIM-[0-9]{4}(?![A-Z0-9-])"),
    ),
    (
        "get_employee_profile",
        "employee_id",
        re.compile(r"(?<![A-Z0-9-])EMP-[0-9]{3}(?![A-Z0-9-])"),
    ),
    (
        "get_business_trip",
        "trip_id",
        re.compile(r"(?<![A-Z0-9-])TRIP-[0-9]{4}-[0-9]{3}(?![A-Z0-9-])"),
    ),
)
_LLAMA_CPP_VERSION = "0.3.36"
_LOCAL_MODEL_EXPECTED_BYTES = 2_889_513_184
_LOCAL_MODEL_SHA256 = "aca596860e8cb40af6539e3f2ea40df305f42515deac56d49c08d39a02e6533f"


class LlamaCppGenerator:
    """Deterministic structured generation through a verified local GGUF."""

    backend_kind = "real"
    model_id = LOCAL_MODEL_REPOSITORY_ID
    model_revision = LOCAL_MODEL_REVISION

    def __init__(
        self,
        *,
        llm: Any,
        model_path: Path,
        model_sha256: str,
    ) -> None:
        if not isinstance(model_path, Path):
            raise TypeError("model_path must be a Path")
        if (
            not isinstance(model_sha256, str)
            or len(model_sha256) != 64
            or any(character not in "0123456789abcdef" for character in model_sha256)
        ):
            raise ValueError("model_sha256 must be a lowercase SHA-256 digest")
        self._llm = llm
        self._model_path = model_path.resolve()
        self._model_sha256 = model_sha256
        self._inference_lock = threading.Lock()
        self._closed = False
        _configure_qwen_no_think_handler(llm)

    @property
    def model_path(self) -> Path:
        return self._model_path

    @property
    def model_sha256(self) -> str:
        return self._model_sha256

    def close(self) -> None:
        """Release the underlying llama runtime at most once.

        Safe to call repeatedly and safe when the llama object exposes no close
        hook at all, so an owner can close unconditionally on any exit path.
        """

        if self._closed:
            return
        self._closed = True
        close = getattr(self._llm, "close", None)
        if callable(close):
            close()

    @classmethod
    def from_config(cls, config: AppConfig) -> LlamaCppGenerator:
        if not isinstance(config, AppConfig):
            raise TypeError("config must be an AppConfig")
        model_path = config.local_model_path.resolve()
        if model_path.name != LOCAL_MODEL_FILENAME:
            raise LocalModelResourceError(
                "local model filename does not match the approved artifact"
            )
        if not model_path.is_file():
            raise LocalModelMissingError("approved local GGUF is missing")
        try:
            size = model_path.stat().st_size
        except OSError as error:
            raise LocalModelUnavailableError(
                "local model metadata is unavailable"
            ) from error
        if size != _LOCAL_MODEL_EXPECTED_BYTES:
            raise LocalModelResourceError(
                "local model size does not match the manifest"
            )
        try:
            digest = _sha256_file(model_path)
        except OSError as error:
            raise LocalModelUnavailableError("local model cannot be read") from error
        if digest != _LOCAL_MODEL_SHA256:
            raise LocalModelResourceError(
                "local model digest does not match the manifest"
            )

        llama = _import_llama_runtime()
        try:
            llm = llama(
                model_path=str(model_path),
                n_ctx=8192,
                n_gpu_layers=-1,
                seed=42,
                verbose=False,
            )
        except TimeoutError as error:
            raise LocalModelTimeoutError("local model loading timed out") from error
        except MemoryError as error:
            raise LocalModelResourceError(
                "local model loading exhausted available memory"
            ) from error
        except (OSError, RuntimeError, ValueError) as error:
            raise LocalModelUnavailableError("local model loading failed") from error
        return cls(
            llm=llm,
            model_path=model_path,
            model_sha256=digest,
        )

    async def generate_json(
        self,
        request: StructuredGenerationRequest,
    ) -> Mapping[str, object]:
        if not isinstance(request, StructuredGenerationRequest):
            raise TypeError("request must be a StructuredGenerationRequest")
        messages = _qwen_messages(request.messages)
        response_format = {
            "type": "json_object",
            "schema": _thaw_json(request.schema),
        }
        try:
            response = await asyncio.to_thread(
                self._create_chat_completion,
                messages,
                response_format,
                request.max_output_tokens,
            )
        except TimeoutError as error:
            raise LocalModelTimeoutError("local generation timed out") from error
        except MemoryError as error:
            raise LocalModelResourceError(
                "local generation exhausted available memory"
            ) from error
        except (OSError, RuntimeError, ValueError) as error:
            raise LocalModelUnavailableError("local generation failed") from error
        return _parse_llama_response(response)

    def _create_chat_completion(
        self,
        messages: list[dict[str, object]],
        response_format: dict[str, object],
        max_output_tokens: int,
    ) -> object:
        with self._inference_lock:
            return self._llm.create_chat_completion(
                messages=messages,
                response_format=response_format,
                temperature=0.0,
                max_tokens=max_output_tokens,
            )


@dataclass(frozen=True, slots=True)
class OnlineUsageSnapshot:
    """Persistent online counters, with no key, prompt, response, or client."""

    request_number: int
    spent_cny: Decimal


class OnlineGenerator:
    """OpenAI-compatible structured generator guarded by an approved profile."""

    backend_kind = "real"

    def __init__(
        self,
        profile: ProviderProfile,
        *,
        allow_network: bool,
        timeout_seconds: float,
        input_token_cap: int,
        environ: Mapping[str, str] | None = None,
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        if not isinstance(profile, ProviderProfile):
            raise TypeError("profile must be a ProviderProfile")
        if type(allow_network) is not bool:
            raise TypeError("allow_network must be a boolean")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be finite and positive")
        if type(input_token_cap) is not int or input_token_cap <= 0:
            raise ValueError("input_token_cap must be a positive integer")
        if environ is not None and not isinstance(environ, Mapping):
            raise TypeError("environ must be a mapping")
        self._profile = profile
        self._allow_network = allow_network
        self._timeout_seconds = float(timeout_seconds)
        self._input_token_cap = input_token_cap
        self._environ = environ
        self._client_factory = client_factory or _create_async_openai
        self._request_number = 0
        self._spent_cny = Decimal(0)
        self._call_lock = asyncio.Lock()

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(profile={self._profile.name!r}, "
            f"model_id={self._profile.model_id!r})"
        )

    async def usage_snapshot(self) -> OnlineUsageSnapshot:
        """Public request/spend counters, with no key, prompt, or client."""

        async with self._call_lock:
            return OnlineUsageSnapshot(self._request_number, self._spent_cny)

    async def generate_json(
        self,
        request: StructuredGenerationRequest,
    ) -> Mapping[str, object]:
        if not isinstance(request, StructuredGenerationRequest):
            raise TypeError("request must be a StructuredGenerationRequest")
        messages = _thaw_json(request.messages)
        if not isinstance(messages, list):
            raise ProviderProtocolError("online request messages are invalid")
        serialized_bytes = len(
            json.dumps(
                messages,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        if serialized_bytes > self._input_token_cap:
            raise ProviderInvalidRequestError("online input cap would be exceeded")

        async with self._call_lock:
            permit = preflight_online_call(
                self._profile,
                allow_network=self._allow_network,
                input_token_cap=self._input_token_cap,
                output_token_cap=request.max_output_tokens,
                request_number=self._request_number + 1,
                already_spent_cny=self._spent_cny,
                environ=self._environ,
            )
            self._request_number = permit.request_number
            self._spent_cny = permit.projected_total_cny
            client = self._client_factory(
                api_key=permit.api_key,
                base_url=permit.base_url,
                timeout=self._timeout_seconds,
                max_retries=0,
            )
            try:
                response = await client.chat.completions.create(
                    model=permit.model_id,
                    messages=messages,
                    response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": "enterprise_rag_output",
                            "strict": True,
                            "schema": _thaw_json(request.schema),
                        },
                    },
                    temperature=0,
                    max_completion_tokens=request.max_output_tokens,
                )
            except Exception as error:
                raise _mapped_online_error(error) from error
            finally:
                # The client is created per call so a rotated key is never cached;
                # it must therefore be closed on success, mapped failure, and
                # cancellation alike.
                await _aclose_online_client(client)
        return _parse_online_response(response)


async def _aclose_online_client(client: object) -> None:
    """Close one per-call SDK client through the first hook it actually exposes.

    The SDKs differ: some expose an async ``aclose``, others only an async
    ``close``, and a few expose a plain synchronous ``close``. The result is
    awaited whenever it is awaitable, so an async ``close`` can never be called
    and dropped un-awaited (which would leak the connection).
    """

    for name in ("aclose", "close"):
        hook = getattr(client, name, None)
        if not callable(hook):
            continue
        result = hook()
        if inspect.isawaitable(result):
            await result
        return


# Failures that may legitimately switch a run from the online backend to the
# local one. Panel authorization errors are deliberately outside this tuple.
ONLINE_FALLBACK_FAILURES = (
    ConfigurationError,
    BudgetBlockedError,
    PrivacyBlockedError,
    ProfileNotAllowedError,
    ProviderAuthenticationError,
    ProviderPermissionError,
    ProviderQuotaError,
    ProviderBalanceError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    ProviderInvalidRequestError,
    ProviderProtocolError,
)


class ExplicitLocalFallbackGenerator:
    """Use the local structured backend once only after an online failure."""

    backend_kind = "real"

    def __init__(
        self,
        online: StructuredGeneratorPort,
        local: StructuredGeneratorPort,
    ) -> None:
        for name, backend in (("online", online), ("local", local)):
            if getattr(backend, "backend_kind", None) != "real":
                raise ValueError(f"{name} fallback backend must be real")
        self._online = online
        self._local = local
        self._switch_category: ContextVar[str | None] = ContextVar(
            f"generator_switch_{id(self)}",
            default=None,
        )

    async def generate_json(
        self,
        request: StructuredGenerationRequest,
    ) -> Mapping[str, object]:
        self._switch_category.set(None)
        try:
            return await self._online.generate_json(request)
        except ONLINE_FALLBACK_FAILURES as error:
            self._switch_category.set(_exception_category(error))
            return await self._local.generate_json(request)

    def consume_backend_switch(self) -> str | None:
        category = self._switch_category.get()
        self._switch_category.set(None)
        return category


def _create_async_openai(
    *,
    api_key: str,
    base_url: str,
    timeout: float,
    max_retries: int,
) -> Any:
    try:
        openai = importlib.import_module("openai")
        client_type = openai.AsyncOpenAI
    except (AttributeError, ImportError, OSError) as error:
        raise ConfigurationError("OpenAI-compatible SDK is unavailable") from error
    return client_type(
        api_key=api_key,
        base_url=base_url,
        timeout=timeout,
        max_retries=max_retries,
    )


def _parse_online_response(response: object) -> Mapping[str, object]:
    try:
        choices = getattr(response, "choices", None)
        if not isinstance(choices, (list, tuple)) or len(choices) != 1:
            raise ValueError("response must contain exactly one choice")
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", None)
        if not isinstance(content, str) or not content.strip():
            raise ValueError("response content is missing")
        payload = json.loads(content)
        if not isinstance(payload, dict):
            raise ValueError("response JSON must be an object")
        return payload
    except (AttributeError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise ProviderProtocolError(
            "provider returned invalid structured JSON"
        ) from error


def _mapped_online_error(error: Exception) -> Exception:
    status_code = getattr(error, "status_code", None)
    code = _provider_error_code(getattr(error, "body", None))
    class_name = type(error).__name__
    if code in {"json_schema_not_supported", "unsupported_response_format"}:
        return ProviderProtocolError("provider does not support strict JSON schema")
    if status_code == 401 or class_name == "AuthenticationError":
        return ProviderAuthenticationError("provider authentication failed")
    if status_code == 403 or class_name == "PermissionDeniedError":
        return ProviderPermissionError("provider permission denied")
    if status_code == 402 or code in {"insufficient_balance", "billing_not_active"}:
        return ProviderBalanceError("provider balance is insufficient")
    if code in {"insufficient_quota", "quota_exceeded"}:
        return ProviderQuotaError("provider quota is exhausted")
    if status_code == 429 or class_name == "RateLimitError":
        return ProviderRateLimitError("provider rate limit was reached")
    if isinstance(error, TimeoutError) or class_name == "APITimeoutError":
        return ProviderTimeoutError("provider request timed out")
    if class_name in {"APIConnectionError", "InternalServerError"} or (
        isinstance(status_code, int) and status_code >= 500
    ):
        return ProviderUnavailableError("provider is unavailable")
    if class_name in {
        "BadRequestError",
        "NotFoundError",
        "UnprocessableEntityError",
    } or status_code in {400, 404, 409, 422}:
        return ProviderInvalidRequestError("provider rejected the request")
    return ProviderProtocolError("provider returned an unrecognized failure")


def _provider_error_code(body: object) -> str | None:
    if not isinstance(body, Mapping):
        return None
    error = body.get("error")
    if isinstance(error, Mapping):
        code = error.get("code")
    else:
        code = body.get("code")
    return code if isinstance(code, str) else None


def _exception_category(error: Exception) -> str:
    category = getattr(error, "category", ProviderProtocolError.category)
    value = getattr(category, "value", category)
    return value if isinstance(value, str) and value else "protocol"


def _import_llama_runtime() -> Any:
    try:
        llama_cpp = importlib.import_module("llama_cpp")
    except (ImportError, OSError) as error:
        raise LocalModelUnavailableError(
            "llama-cpp-python runtime is unavailable"
        ) from error
    if llama_cpp.__version__ != _LLAMA_CPP_VERSION:
        raise LocalModelUnavailableError(
            "llama-cpp-python runtime version does not match the approved version"
        )
    return llama_cpp.Llama


def _configure_qwen_no_think_handler(llm: Any) -> None:
    handler = getattr(llm, "chat_handler", None)
    if handler is None:
        chat_format = getattr(llm, "chat_format", None)
        handlers = getattr(llm, "_chat_handlers", None)
        if isinstance(chat_format, str) and isinstance(handlers, Mapping):
            handler = handlers.get(chat_format)
    if not callable(handler):
        return

    def no_think_handler(**kwargs: object) -> object:
        kwargs["enable_thinking"] = False
        return handler(**kwargs)

    try:
        llm.chat_handler = no_think_handler
    except (AttributeError, TypeError) as error:
        raise LocalModelUnavailableError(
            "local model chat handler cannot disable thinking"
        ) from error


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _qwen_messages(
    messages: tuple[Mapping[str, object], ...],
) -> list[dict[str, object]]:
    prepared: list[dict[str, object]] = []
    last_user_index: int | None = None
    for index, message in enumerate(messages):
        role = message.get("role")
        content = message.get("content")
        if not isinstance(role, str) or not role.strip():
            raise GenerationProtocolError("generator message role is invalid")
        if not isinstance(content, str) or not content.strip():
            raise GenerationProtocolError("generator message content is invalid")
        row = {key: _thaw_json(value) for key, value in message.items()}
        if role == "system":
            row["content"] = (
                f"{content.rstrip()} Return only the requested schema object. "
                "Do not include reasoning, analysis, or markdown."
            )
        if role == "user":
            last_user_index = index
        prepared.append(row)
    if last_user_index is None:
        raise GenerationProtocolError("generator request requires a user message")
    user_content = prepared[last_user_index]["content"]
    if not isinstance(user_content, str):
        raise GenerationProtocolError("generator user content is invalid")
    prepared[last_user_index]["content"] = f"{user_content.rstrip()}\n/no_think"
    return prepared


def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw_json(item) for item in value]
    return value


def _parse_llama_response(response: object) -> Mapping[str, object]:
    try:
        if not isinstance(response, Mapping):
            raise TypeError("response must be an object")
        choices = response["choices"]
        if not isinstance(choices, list) or len(choices) != 1:
            raise ValueError("response must contain exactly one choice")
        choice = choices[0]
        if not isinstance(choice, Mapping):
            raise TypeError("response choice must be an object")
        message = choice["message"]
        if not isinstance(message, Mapping):
            raise TypeError("response message must be an object")
        content = message["content"]
        if not isinstance(content, str) or not content.strip():
            raise ValueError("response content must be a non-blank string")
        payload = json.loads(content)
        if not isinstance(payload, dict):
            raise TypeError("response JSON must be an object")
        return payload
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise GenerationProtocolError(
            "local generator returned invalid JSON"
        ) from error


def parse_answer_draft(payload: Mapping[str, object]) -> AnswerDraft:
    """Strictly parse an untrusted structured-generation payload."""

    try:
        if not isinstance(payload, Mapping) or set(payload) != _ANSWER_FIELDS:
            raise ValueError("answer object fields are incomplete or unexpected")
        answer = _clean_text(payload["answer"], "answer")
        claims_value = payload["claims"]
        if not isinstance(claims_value, (list, tuple)) or not claims_value:
            raise ValueError("claims must be a non-empty array")

        claims: list[EvidenceClaim] = []
        for claim_value in claims_value:
            if (
                not isinstance(claim_value, Mapping)
                or set(claim_value) != _CLAIM_FIELDS
            ):
                raise ValueError("claim fields are incomplete or unexpected")
            text = _clean_text(claim_value["text"], "claim text")
            support_ids = _string_array(
                claim_value["support_ids"],
                "support_ids",
            )
            if len(set(support_ids)) != len(support_ids):
                raise ValueError("support_ids must not contain duplicates")
            supporting_quotes = _string_array(
                claim_value["supporting_quotes"],
                "supporting_quotes",
            )
            claims.append(
                EvidenceClaim(
                    text=text,
                    support_ids=support_ids,
                    supporting_quotes=supporting_quotes,
                )
            )
        return AnswerDraft(answer=answer, claims=tuple(claims))
    except GenerationProtocolError:
        raise
    except (KeyError, TypeError, ValueError, UnicodeError) as error:
        raise GenerationProtocolError(f"invalid answer payload: {error}") from error


def parse_agent_action(payload: Mapping[str, object]) -> AgentAction:
    """Strictly parse one schema-constrained Plan-Act-Observe action."""

    try:
        if not isinstance(payload, Mapping):
            raise TypeError("action payload must be an object")
        action_name = _clean_text(payload.get("action"), "action")
        if action_name == "finish":
            if set(payload) != {"action", "draft"}:
                raise ValueError("finish fields are incomplete or unexpected")
            draft_payload = payload["draft"]
            if not isinstance(draft_payload, Mapping):
                raise TypeError("finish draft must be an object")
            return AgentAction(
                name="finish",
                arguments={},
                finish_draft=parse_answer_draft(draft_payload),
            )

        value_field = _ACTION_VALUE_FIELDS.get(action_name)
        if value_field is None:
            raise ValueError("unknown action")
        if set(payload) != {"action", value_field}:
            raise ValueError("action fields are incomplete or unexpected")
        value = _clean_text(payload[value_field], value_field)
        return AgentAction(
            name=action_name,
            arguments={value_field: value},
            finish_draft=None,
        )
    except GenerationProtocolError:
        raise
    except (KeyError, TypeError, ValueError, UnicodeError) as error:
        raise GenerationProtocolError(f"invalid action payload: {error}") from error


class AgentPlanner:
    """Structured local-model planner and one-shot Agent answer repairer."""

    def __init__(
        self,
        backend: StructuredGeneratorPort,
        *,
        max_action_tokens: int = 1024,
        max_repair_tokens: int = 768,
    ) -> None:
        for name, value in (
            ("max_action_tokens", max_action_tokens),
            ("max_repair_tokens", max_repair_tokens),
        ):
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        backend_kind = getattr(backend, "backend_kind", None)
        if not isinstance(backend_kind, str) or not backend_kind.strip():
            raise ValueError("generator backend must expose backend_kind")
        self._backend = backend
        self._max_action_tokens = max_action_tokens
        self._max_repair_tokens = max_repair_tokens
        self.backend_kind = backend_kind

    async def next_action(
        self,
        task: str,
        observations: tuple[Observation, ...],
    ) -> AgentAction:
        _validate_agent_inputs(task, observations)
        deterministic_action = _next_business_lookup(task, observations)
        if deterministic_action is not None:
            return deterministic_action
        policy_search = _policy_search_from_business_facts(observations)
        if policy_search is not None:
            return policy_search
        if any(
            item.source == "search_knowledge" and item.status == "found"
            for item in observations
        ):
            request = agent_finish_request(
                task,
                observations,
                max_output_tokens=self._max_action_tokens,
            )
            try:
                draft = parse_answer_draft(await self._backend.generate_json(request))
            except GenerationProtocolError:
                retry_request = agent_finish_retry_request(
                    task,
                    observations,
                    max_output_tokens=min(self._max_action_tokens, 512),
                )
                draft = parse_answer_draft(
                    await self._backend.generate_json(retry_request)
                )
            except (TypeError, ValueError) as error:
                raise GenerationProtocolError(
                    "planner returned invalid final answer"
                ) from error
            return AgentAction("finish", {}, draft)
        request = agent_action_request(
            task,
            observations,
            max_output_tokens=self._max_action_tokens,
        )
        try:
            return parse_agent_action(await self._backend.generate_json(request))
        except GenerationProtocolError:
            raise
        except (TypeError, ValueError) as error:
            raise GenerationProtocolError("planner returned invalid data") from error

    async def repair_answer(
        self,
        task: str,
        observations: tuple[Observation, ...],
        draft: AnswerDraft,
        issues: tuple[ValidationIssue, ...],
    ) -> AnswerDraft:
        request = agent_repair_request(
            task,
            observations,
            draft,
            issues,
            max_output_tokens=self._max_repair_tokens,
        )
        try:
            return parse_answer_draft(await self._backend.generate_json(request))
        except GenerationProtocolError:
            raise
        except (TypeError, ValueError) as error:
            raise GenerationProtocolError("repair returned invalid data") from error

    def consume_backend_switch(self) -> str | None:
        consume = getattr(self._backend, "consume_backend_switch", None)
        if not callable(consume):
            return None
        category = consume()
        return category if isinstance(category, str) and category else None


class AnswerGenerator:
    def __init__(
        self,
        backend: StructuredGeneratorPort,
        *,
        max_output_tokens: int = 512,
    ) -> None:
        if (
            isinstance(max_output_tokens, bool)
            or not isinstance(max_output_tokens, int)
            or max_output_tokens <= 0
        ):
            raise ValueError("max_output_tokens must be a positive integer")
        backend_kind = getattr(backend, "backend_kind", None)
        if not isinstance(backend_kind, str) or not backend_kind.strip():
            raise ValueError("generator backend must expose backend_kind")
        self._backend = backend
        self._max_output_tokens = max_output_tokens
        self.backend_kind = backend_kind

    @property
    def backend(self) -> StructuredGeneratorPort:
        """The wrapped structured backend, exposed only for public provenance."""

        return self._backend

    async def generate_answer(
        self,
        question: str,
        evidence: tuple[RerankedChunk, ...],
    ) -> AnswerDraft:
        request = answer_request(
            question,
            evidence,
            max_output_tokens=self._max_output_tokens,
        )
        return await self._generate(request)

    async def repair_answer(
        self,
        question: str,
        evidence: tuple[RerankedChunk, ...],
        draft: AnswerDraft,
        issues: tuple[ValidationIssue, ...],
    ) -> AnswerDraft:
        request = repair_request(
            question,
            evidence,
            draft,
            issues,
            max_output_tokens=self._max_output_tokens,
        )
        return await self._generate(request)

    def consume_backend_switch(self) -> str | None:
        consume = getattr(self._backend, "consume_backend_switch", None)
        if not callable(consume):
            return None
        category = consume()
        return category if isinstance(category, str) and category else None

    async def _generate(
        self,
        request: StructuredGenerationRequest,
    ) -> AnswerDraft:
        try:
            payload = await self._backend.generate_json(request)
            return parse_answer_draft(payload)
        except GenerationProtocolError:
            raise
        except (TypeError, ValueError) as error:
            raise GenerationProtocolError("generator returned invalid data") from error


def answer_request(
    question: str,
    evidence: tuple[RerankedChunk, ...],
    *,
    max_output_tokens: int,
) -> StructuredGenerationRequest:
    _validate_inputs(question, evidence)
    return StructuredGenerationRequest(
        messages=(
            {
                "role": "system",
                "content": (
                    "Answer only from the supplied evidence. Return only the "
                    "requested JSON object; cite exact support IDs and quotes. "
                    "Use exactly one claim for the direct answer unless multiple "
                    "independent facts are required. Every claim text must appear "
                    "verbatim in the answer. Every supporting quote must be a "
                    "verbatim substring of the text belonging to each cited "
                    "support ID; never cite an ID whose text lacks the quote. "
                    "If one evidence item directly answers the question, use only "
                    "that item; do not add related rules, exceptions, or contrasts "
                    "that the question did not ask for. "
                    "Write the answer and each claim text as a complete concise "
                    "Chinese sentence, not merely an amount or identifier. For "
                    "example: 每晚住宿上限为 CNY 600。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "question": question,
                        "evidence": _evidence_payload(evidence),
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            },
        ),
        schema=ANSWER_SCHEMA,
        max_output_tokens=max_output_tokens,
    )


def repair_request(
    question: str,
    evidence: tuple[RerankedChunk, ...],
    draft: AnswerDraft,
    issues: tuple[ValidationIssue, ...],
    *,
    max_output_tokens: int,
) -> StructuredGenerationRequest:
    _validate_inputs(question, evidence)
    if not isinstance(draft, AnswerDraft):
        raise TypeError("draft must be an AnswerDraft")
    if (
        not isinstance(issues, tuple)
        or not issues
        or any(not isinstance(issue, ValidationIssue) for issue in issues)
    ):
        raise ValueError("repair issues must be a non-empty ValidationIssue tuple")
    return StructuredGenerationRequest(
        messages=(
            {
                "role": "system",
                "content": (
                    "Repair the answer using only supplied evidence. Return only "
                    "the requested JSON object and resolve every validation issue. "
                    "Do not repeat the invalid draft. Use exactly one claim for "
                    "the direct answer unless multiple independent facts are "
                    "required. Every claim text must appear verbatim in the answer. "
                    "Every supporting quote must be a verbatim substring of the "
                    "text belonging to each cited support ID; never cite an ID "
                    "whose text lacks the quote. If one evidence item directly "
                    "answers the question, use only that item; do not add related "
                    "rules, exceptions, or contrasts that the question did not "
                    "ask for. "
                    "Write the answer and each claim text as a complete concise "
                    "Chinese sentence, not merely an amount or identifier. For "
                    "example: 每晚住宿上限为 CNY 600。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "task": "repair",
                        "question": question,
                        "evidence": _evidence_payload(evidence),
                        "draft": _draft_payload(draft),
                        "issues": [
                            {
                                "code": issue.code,
                                "claim_index": issue.claim_index,
                                "detail": issue.detail,
                            }
                            for issue in issues
                        ],
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            },
        ),
        schema=ANSWER_SCHEMA,
        max_output_tokens=max_output_tokens,
    )


def agent_action_request(
    task: str,
    observations: tuple[Observation, ...],
    *,
    max_output_tokens: int,
) -> StructuredGenerationRequest:
    _validate_agent_inputs(task, observations)
    knowledge_ready = any(
        item.source == "search_knowledge" and item.status == "found"
        for item in observations
    )
    if knowledge_ready:
        instructions = (
            "Knowledge evidence is already available. Select finish or refuse. "
            "Do not call another tool or repeat a search. The final draft must "
            "cite at least one knowledge chunk ID. Use exactly one claim for the "
            "direct answer unless multiple independent facts are required. Every "
            "claim text must appear verbatim in the answer. Every supporting quote "
            "must be a verbatim substring of the text belonging to its cited "
            "support ID. Every required_business_support_id must appear in at "
            "least one claim. Cite policy rules from search_knowledge and instance "
            "facts from business observations. Write concise complete Chinese "
            "sentences. Return only the action schema and never expose reasoning."
        )
        available_actions = ["finish", "refuse"]
        schema = FINAL_ACTION_SCHEMA
    else:
        instructions = (
            "Select exactly one safe action for the enterprise travel-expense "
            "task. Search policy knowledge before finishing. When the task "
            "contains business record IDs, inspect those records first. Use "
            "business lookup tools only for explicit IDs in the task or prior "
            "observations. Then formulate policy searches from business facts "
            "such as grade, city tier, amount, receipt state, or rejection code, "
            "and omit record IDs from the policy question. Never repeat an action. "
            "Return only the action schema; never expose reasoning."
        )
        available_actions = [
            "search_knowledge",
            "get_employee_profile",
            "get_expense_claim",
            "get_business_trip",
            "refuse",
        ]
        schema = ACTION_SCHEMA
    return StructuredGenerationRequest(
        messages=(
            {
                "role": "system",
                "content": instructions,
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "task": task,
                        "observations": _observation_payload(observations),
                        "available_actions": available_actions,
                        "required_business_support_ids": (
                            _required_business_support_ids(observations)
                        ),
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            },
        ),
        schema=schema,
        max_output_tokens=max_output_tokens,
    )


def agent_finish_request(
    task: str,
    observations: tuple[Observation, ...],
    *,
    max_output_tokens: int,
) -> StructuredGenerationRequest:
    _validate_agent_inputs(task, observations)
    if not any(
        item.source == "search_knowledge" and item.status == "found"
        for item in observations
    ):
        raise ValueError("final answer requires found knowledge evidence")
    return StructuredGenerationRequest(
        messages=(
            {
                "role": "system",
                "content": (
                    "Knowledge evidence is available. Produce the grounded final "
                    "answer directly; return only the requested answer JSON. "
                    "Cite at least one knowledge chunk ID. Every "
                    "required_business_support_id must appear in at least one "
                    "claim. Keep policy rules and business-record facts in "
                    "separate claims. Every claim text must appear verbatim in "
                    "the answer, and every supporting quote must be a verbatim "
                    "substring of its cited support. Write concise complete "
                    "Chinese sentences and never expose reasoning."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "task": task,
                        "observations": _observation_payload(observations),
                        "required_business_support_ids": (
                            _required_business_support_ids(observations)
                        ),
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            },
        ),
        schema=ANSWER_SCHEMA,
        max_output_tokens=max_output_tokens,
    )


def agent_finish_retry_request(
    task: str,
    observations: tuple[Observation, ...],
    *,
    max_output_tokens: int,
) -> StructuredGenerationRequest:
    _validate_agent_inputs(task, observations)
    knowledge = tuple(
        item
        for item in observations
        if item.source == "search_knowledge" and item.status == "found"
    )
    if not knowledge:
        raise ValueError("final answer retry requires found knowledge evidence")
    return StructuredGenerationRequest(
        messages=(
            {
                "role": "system",
                "content": (
                    "The previous final answer violated the JSON contract. "
                    "Return exactly one concise knowledge claim backed by one "
                    "knowledge chunk ID and one exact supporting quote. Do not "
                    "include business-record claims; validated record facts are "
                    "appended programmatically. Make answer identical to the "
                    "claim text, use a complete Chinese sentence, and return "
                    "only the requested answer JSON."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "knowledge_observations": _observation_payload(knowledge),
                        "task": task,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            },
        ),
        schema=ANSWER_SCHEMA,
        max_output_tokens=max_output_tokens,
    )


def agent_repair_request(
    task: str,
    observations: tuple[Observation, ...],
    draft: AnswerDraft,
    issues: tuple[ValidationIssue, ...],
    *,
    max_output_tokens: int,
) -> StructuredGenerationRequest:
    _validate_agent_inputs(task, observations)
    if not isinstance(draft, AnswerDraft):
        raise TypeError("draft must be an AnswerDraft")
    if (
        not isinstance(issues, tuple)
        or not issues
        or any(not isinstance(issue, ValidationIssue) for issue in issues)
    ):
        raise ValueError("repair issues must be a non-empty ValidationIssue tuple")
    return StructuredGenerationRequest(
        messages=(
            {
                "role": "system",
                "content": (
                    "Repair the final answer using only found observations. "
                    "Resolve every validation issue. Use exactly one claim for the "
                    "direct answer unless multiple independent facts are required. "
                    "Every claim text must appear verbatim in the answer. Every "
                    "supporting quote must be a verbatim substring of the text "
                    "belonging to its cited support ID. At least one claim must cite "
                    "a search_knowledge chunk. Every required_business_support_id "
                    "must appear in at least one claim. Cite business facts from "
                    "their Observation IDs. Write concise complete Chinese "
                    "sentences. Return only the answer schema and never expose "
                    "reasoning."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "task": task,
                        "observations": _observation_payload(observations),
                        "required_business_support_ids": (
                            _required_business_support_ids(observations)
                        ),
                        "invalid_draft": _draft_payload(draft),
                        "issues": [
                            {
                                "code": issue.code,
                                "claim_index": issue.claim_index,
                                "detail": issue.detail,
                            }
                            for issue in issues
                        ],
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            },
        ),
        schema=ANSWER_SCHEMA,
        max_output_tokens=max_output_tokens,
    )


def _clean_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    for character in value:
        category = unicodedata.category(character)
        if category == "Cs" or (category == "Cc" and character not in "\n\r\t"):
            raise ValueError(f"{name} contains invalid control characters")
    return value.strip()


def _string_array(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError(f"{name} must be a non-empty array")
    return tuple(_clean_text(item, name) for item in value)


def _validate_inputs(
    question: str,
    evidence: tuple[RerankedChunk, ...],
) -> None:
    _clean_text(question, "question")
    if (
        not isinstance(evidence, tuple)
        or not evidence
        or any(not isinstance(item, RerankedChunk) for item in evidence)
    ):
        raise ValueError("evidence must be a non-empty RerankedChunk tuple")


def _validate_agent_inputs(
    task: str,
    observations: tuple[Observation, ...],
) -> None:
    _clean_text(task, "task")
    if not isinstance(observations, tuple) or any(
        not isinstance(item, Observation) for item in observations
    ):
        raise TypeError("observations must contain Observation values")


def _observation_payload(
    observations: tuple[Observation, ...],
) -> list[dict[str, object]]:
    return [
        {
            "observation_id": item.observation_id,
            "source": item.source,
            "status": item.status,
            "facts": _thaw_json(item.facts),
            "support_text": item.support_text,
        }
        for item in observations
    ]


def _required_business_support_ids(
    observations: tuple[Observation, ...],
) -> list[str]:
    return [
        item.observation_id
        for item in observations
        if item.source != "search_knowledge" and item.status == "found"
    ]


def _next_business_lookup(
    task: str,
    observations: tuple[Observation, ...],
) -> AgentAction | None:
    searchable_text = [task]
    for observation in observations:
        searchable_text.extend(_nested_strings(observation.facts))

    resolved: set[tuple[str, str]] = set()
    for observation in observations:
        for tool_name, _argument_name, pattern in _BUSINESS_ID_ACTIONS:
            prefix = f"obs:{tool_name}:"
            is_resolved = observation.observation_id.startswith(prefix)
            if observation.source != tool_name or not is_resolved:
                continue
            record_id = observation.observation_id.removeprefix(prefix)
            if pattern.fullmatch(record_id) is not None:
                resolved.add((tool_name, record_id))

    for tool_name, argument_name, pattern in _BUSINESS_ID_ACTIONS:
        candidates = sorted(
            {
                match.group(0)
                for text in searchable_text
                for match in pattern.finditer(text)
            }
        )
        for record_id in candidates:
            if (tool_name, record_id) not in resolved:
                return AgentAction(
                    name=tool_name,
                    arguments={argument_name: record_id},
                    finish_draft=None,
                )
    return None


def _nested_strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        return [text for nested in value.values() for text in _nested_strings(nested)]
    if isinstance(value, (list, tuple)):
        return [text for nested in value for text in _nested_strings(nested)]
    return []


def _policy_search_from_business_facts(
    observations: tuple[Observation, ...],
) -> AgentAction | None:
    if any(item.source == "search_knowledge" for item in observations):
        return None
    claim = _first_found_facts(observations, "get_expense_claim")
    employee = _first_found_facts(observations, "get_employee_profile")
    trip = _first_found_facts(observations, "get_business_trip")
    if claim is None or employee is None or trip is None:
        return None

    grade = employee.get("grade")
    destination = trip.get("destination")
    city_tier = trip.get("city_tier")
    rejection_code = claim.get("rejection_code")
    identity = (grade, destination, city_tier)
    if not all(isinstance(value, str) and value for value in identity):
        return None

    if rejection_code == "HOTEL_OVER_LIMIT":
        question = (
            f"{grade} 员工前往{destination} ({city_tier} 类城市) 出差, "
            "每晚酒店住宿报销上限是多少?"
        )
    elif rejection_code == "MISSING_RECEIPT":
        question = (
            f"{grade} 员工前往{destination} ({city_tier} 类城市) 出差, "
            "费用缺少发票或 receipt 时的报销规则是什么?"
        )
    else:
        question = (
            f"{grade} 员工前往{destination} ({city_tier} 类城市) 出差, "
            "适用的住宿与费用报销规则是什么?"
        )
    return AgentAction(
        name="search_knowledge",
        arguments={"question": question},
        finish_draft=None,
    )


def _first_found_facts(
    observations: tuple[Observation, ...],
    source: str,
) -> Mapping[str, object] | None:
    for observation in observations:
        if observation.source == source and observation.status == "found":
            return observation.facts
    return None


def _evidence_payload(
    evidence: tuple[RerankedChunk, ...],
) -> list[dict[str, object]]:
    return [
        {
            "chunk_id": item.candidate.chunk_id,
            "filename": item.candidate.filename,
            "rerank_score": item.rerank_score,
            "text": item.candidate.text,
        }
        for item in evidence
    ]


def _draft_payload(draft: AnswerDraft) -> dict[str, object]:
    return {
        "answer": draft.answer,
        "claims": [
            {
                "text": claim.text,
                "support_ids": list(claim.support_ids),
                "supporting_quotes": list(claim.supporting_quotes),
            }
            for claim in draft.claims
        ],
    }
