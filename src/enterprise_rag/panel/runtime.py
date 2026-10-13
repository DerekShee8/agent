"""Shared panel runtime: cached generators, one-use authorization, and staged init.

The runtime is the only owner of heavy resources and of the persistent online
counters. It hands each accepted run a freshly bound, single-use ``Application``
and never lets an authorization, recorder, or cancellation token reach a cache.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Mapping
from decimal import Decimal
from pathlib import Path
from typing import Literal, cast
from uuid import uuid4

from ..agent_tools import ToolRegistry
from ..application import (
    Application,
    ApplicationTemplate,
    EncoderFactory,
    IndexLoader,
    OnlineGeneratorFactory,
    RerankerFactory,
    RerankingRuntimeComponents,
    RetrievalRuntimeComponents,
    SharedRuntimeComponents,
    SupportScorerFactory,
    aclose_runtime_component,
    aclose_runtime_components,
    assemble_shared_runtime_components,
    build_runtime_reranking,
    build_runtime_retrieval,
    load_runtime_index,
    load_runtime_tools,
    runtime_component_resources,
)
from ..config import AppConfig
from ..errors import ConfigurationError, ErrorCategory
from ..execution_control import CancellationToken
from ..execution_trace import TraceObserver, TraceRecorder
from ..generation import (
    ONLINE_FALLBACK_FAILURES,
    LlamaCppGenerator,
    OnlineGenerator,
    OnlineUsageSnapshot,
)
from ..index_store import HybridIndex
from ..models import StructuredGenerationRequest
from ..ports import StructuredGeneratorPort
from ..provider_profiles import (
    OnlineRunPreflight,
    ProviderProfile,
    load_profiles,
    preflight_online_run,
    profile_fingerprint,
)
from .contracts import (
    InitializationStage,
    OnlineRunAuthorization,
    RunRequest,
    RunSpec,
)

INITIALIZATION_STAGES = (
    "CHECKING_CONFIG",
    "LOADING_INDEX",
    "LOADING_RETRIEVER",
    "LOADING_RERANKER",
    "PREPARING_DEFAULT_LOCAL_GENERATOR",
    "READY",
)

# One answer attempt plus at most one grounding repair.
_RAG_MAX_REQUESTS = 2

LocalGeneratorFactory = Callable[[AppConfig], StructuredGeneratorPort]

PanelRuntimeState = Literal["initializing", "initialization_failed", "ready"]


class RunAuthorizationError(Exception):
    """A one-use run authorization was absent, replayed, mismatched, or exhausted.

    It is deliberately not a member or subclass of ``ONLINE_FALLBACK_FAILURES``:
    an exhausted or replayed authorization is a FAILED business result and must
    never switch the run to the local backend.
    """

    category = ErrorCategory.PROFILE_NOT_ALLOWED


def rag_max_requests() -> int:
    """Largest number of provider calls one RAG run may make."""

    return _RAG_MAX_REQUESTS


def agent_max_requests(max_actions: int) -> int:
    """Largest number of provider calls one Agent run may make.

    One generator attempt per action, plus one possible final-answer protocol
    retry, plus one grounding repair.
    """

    if isinstance(max_actions, bool) or not isinstance(max_actions, int):
        raise TypeError("max_actions must be an integer")
    if max_actions <= 0:
        raise ValueError("max_actions must be positive")
    return max_actions + 2


class AuthorizedOnlineGenerator:
    """Run-scoped wrapper that enforces the authorization's request budget."""

    backend_kind = "real"

    def __init__(
        self,
        backend: StructuredGeneratorPort,
        authorization: OnlineRunAuthorization,
    ) -> None:
        if not isinstance(authorization, OnlineRunAuthorization):
            raise TypeError("authorization must be an OnlineRunAuthorization")
        self._backend = backend
        self._authorization = authorization
        self._calls = 0

    @property
    def authorization_id(self) -> str:
        return self._authorization.authorization_id

    async def generate_json(
        self,
        request: StructuredGenerationRequest,
    ) -> Mapping[str, object]:
        if self._calls >= self._authorization.max_requests:
            raise RunAuthorizationError("run authorization request budget is exhausted")
        self._calls += 1
        return await self._backend.generate_json(request)


class AuthorizedLocalFallbackGenerator:
    """Per-run, non-owning fallback that latches to the shared local backend.

    It catches only ``ONLINE_FALLBACK_FAILURES``. An authorization error is not
    one of them, so exhaustion can never silently become a local answer, and
    after the first eligible failure the run never retries Online.
    """

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
        self._switched = False
        self._switch_category: str | None = None

    async def generate_json(
        self,
        request: StructuredGenerationRequest,
    ) -> Mapping[str, object]:
        if self._switched:
            return await self._local.generate_json(request)
        try:
            return await self._online.generate_json(request)
        except ONLINE_FALLBACK_FAILURES as error:
            self._switched = True
            self._switch_category = _exception_category(error)
            return await self._local.generate_json(request)

    def consume_backend_switch(self) -> str | None:
        category = self._switch_category
        self._switch_category = None
        return category


class GeneratorRegistry:
    """Owns the persistent generator backends and issues one-use run bindings."""

    def __init__(
        self,
        config: AppConfig,
        profiles: tuple[ProviderProfile, ...] = (),
        *,
        local_factory: LocalGeneratorFactory | None = None,
        online_factory: OnlineGeneratorFactory | None = None,
        environ: Mapping[str, str] | None = None,
        input_token_cap: int = 16_384,
        output_token_cap: int = 512,
    ) -> None:
        if not isinstance(config, AppConfig):
            raise TypeError("config must be an AppConfig")
        if environ is not None and not isinstance(environ, Mapping):
            raise TypeError("environ must be a mapping")
        for name, value in (
            ("input_token_cap", input_token_cap),
            ("output_token_cap", output_token_cap),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        self._config = config
        self._profiles = tuple(profiles)
        self._local_factory = local_factory or _create_local_generator
        self._online_factory = online_factory or _create_online_generator
        self._environ = environ
        self._input_token_cap = input_token_cap
        self._output_token_cap = output_token_cap
        self._local: StructuredGeneratorPort | None = None
        self._local_lock = asyncio.Lock()
        self._online: dict[str, StructuredGeneratorPort] = {}
        self._online_lock = asyncio.Lock()
        self._authorization_lock = asyncio.Lock()
        self._issued: set[str] = set()
        self._closed = False

    @property
    def profiles(self) -> tuple[ProviderProfile, ...]:
        return self._profiles

    def profile_for(self, name: str | None) -> ProviderProfile:
        if name is None:
            raise ConfigurationError("online generation requires a provider profile")
        matches = tuple(profile for profile in self._profiles if profile.name == name)
        if len(matches) != 1:
            raise ConfigurationError("provider profile is missing or duplicated")
        return matches[0]

    async def get_local(self) -> StructuredGeneratorPort:
        """Single-flight the blocking local load and reuse it for every run."""

        if self._local is not None:
            return self._local
        async with self._local_lock:
            if self._local is None:
                factory = self._local_factory
                self._local = await asyncio.to_thread(factory, self._config)
        return self._local

    async def get_online(self, profile: ProviderProfile) -> StructuredGeneratorPort:
        """Return the one persistent generator for a complete profile fingerprint."""

        fingerprint = profile_fingerprint(profile)
        existing = self._online.get(fingerprint)
        if existing is not None:
            return existing
        async with self._online_lock:
            existing = self._online.get(fingerprint)
            if existing is not None:
                return existing
            backend = self._online_factory(self._config, profile)
            self._online[fingerprint] = backend
            return backend

    async def preflight(self, request: RunRequest) -> OnlineRunPreflight:
        """Bound one whole run without creating any client."""

        if not isinstance(request, RunRequest):
            raise TypeError("request must be a RunRequest")
        if request.generator != "online":
            raise ConfigurationError("online preflight requires an online run request")
        profile = self.profile_for(request.provider_name)
        if (
            request.provider_fingerprint is not None
            and request.provider_fingerprint != profile_fingerprint(profile)
        ):
            # A request naming one profile but carrying another's fingerprint is
            # refused here rather than silently preflighted against the wrong one.
            raise ConfigurationError(
                "the run request does not match the selected provider profile"
            )
        snapshot = await self._usage_snapshot(profile_fingerprint(profile))
        max_requests = (
            rag_max_requests()
            if request.mode == "rag"
            else agent_max_requests(self._config.agent_max_actions)
        )
        return preflight_online_run(
            profile,
            run_id=request.run_id,
            allow_network=self._config.allow_network,
            max_requests=max_requests,
            input_token_cap=self._input_token_cap,
            output_token_cap=self._output_token_cap,
            fallback_local=request.fallback_local,
            request_number=snapshot.request_number,
            already_spent_cny=snapshot.spent_cny,
            environ=self._environ,
        )

    def authorize(self, preflight: OnlineRunPreflight) -> OnlineRunAuthorization:
        """Mint the single-use authorization a confirmed run will consume."""

        if not isinstance(preflight, OnlineRunPreflight):
            raise TypeError("preflight must be an OnlineRunPreflight")
        authorization = OnlineRunAuthorization(
            authorization_id=uuid4().hex,
            run_id=preflight.run_id,
            profile_fingerprint=preflight.provider_fingerprint,
            max_requests=preflight.max_requests,
            input_token_cap=preflight.input_token_cap,
            output_token_cap=preflight.output_token_cap,
            max_cost_cny=preflight.max_cost_cny,
            synthetic_data_only=preflight.synthetic_data_only,
            fallback_local=preflight.fallback_local,
        )
        self._issued.add(authorization.authorization_id)
        return authorization

    async def backend_for(self, spec: RunSpec) -> StructuredGeneratorPort:
        """Resolve one run's generator binding, consuming its authorization."""

        if not isinstance(spec, RunSpec):
            raise TypeError("spec must be a RunSpec")
        if spec.generator == "local":
            return await self.get_local()
        authorization = spec.online_authorization
        if authorization is None:
            raise RunAuthorizationError("online run has no authorization")
        profile = self.profile_for(spec.provider_name)
        if authorization.profile_fingerprint != profile_fingerprint(profile):
            raise RunAuthorizationError(
                "authorization does not match the selected profile"
            )
        if authorization.run_id != spec.run_id:
            raise RunAuthorizationError("authorization does not match the run")
        if authorization.fallback_local != spec.fallback_local:
            raise RunAuthorizationError(
                "authorization does not match the fallback setting"
            )
        async with self._authorization_lock:
            if authorization.authorization_id not in self._issued:
                raise RunAuthorizationError(
                    "authorization is unknown or has already been used"
                )
            self._issued.discard(authorization.authorization_id)
        online = await self.get_online(profile)
        authorized = AuthorizedOnlineGenerator(online, authorization)
        if not spec.fallback_local:
            return authorized
        return AuthorizedLocalFallbackGenerator(authorized, await self.get_local())

    async def _usage_snapshot(self, fingerprint: str) -> OnlineUsageSnapshot:
        backend = self._online.get(fingerprint)
        snapshot = getattr(backend, "usage_snapshot", None)
        if not callable(snapshot):
            return OnlineUsageSnapshot(0, Decimal(0))
        return await snapshot()

    async def aclose(self) -> None:
        """Close every unique persistent backend exactly once."""

        if self._closed:
            return
        self._closed = True
        owned: list[object] = []
        if self._local is not None:
            owned.append(self._local)
        owned.extend(self._online.values())
        seen: set[int] = set()
        for backend in owned:
            if id(backend) in seen:
                continue
            seen.add(id(backend))
            await aclose_runtime_component(backend)
        self._local = None
        self._online.clear()
        self._issued.clear()


class PanelRuntime:
    """One process-wide owner of the staged heavy components and run bindings."""

    def __init__(
        self,
        config: AppConfig | None = None,
        *,
        index_loader: IndexLoader | None = None,
        encoder_factory: EncoderFactory | None = None,
        reranker_factory: RerankerFactory | None = None,
        support_scorer_factory: SupportScorerFactory | None = None,
        tool_loader: Callable[[Path], ToolRegistry] | None = None,
        local_factory: LocalGeneratorFactory | None = None,
        online_factory: OnlineGeneratorFactory | None = None,
        profile_loader: Callable[[Path], tuple[ProviderProfile, ...]] = load_profiles,
        provider_profiles_path: Path = Path("config/providers.toml"),
        records_root: Path = Path("data/sample_records"),
        environ: Mapping[str, str] | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._config = config
        self._index_loader = index_loader
        self._encoder_factory = encoder_factory
        self._reranker_factory = reranker_factory
        self._support_scorer_factory = support_scorer_factory
        self._tool_loader = tool_loader or ToolRegistry.from_directory
        self._local_factory = local_factory
        self._online_factory = online_factory
        self._profile_loader = profile_loader
        self._provider_profiles_path = provider_profiles_path
        self._records_root = records_root
        self._environ = environ
        self._clock = clock
        self._initialization: asyncio.Task[tuple[InitializationStage, ...]] | None = (
            None
        )
        self._observers: list[Callable[[InitializationStage], None]] = []
        self._stages: list[InitializationStage] = []
        self._warnings: tuple[str, ...] = ()
        self._tools: ToolRegistry | None = None
        self._shared: SharedRuntimeComponents | None = None
        self._registry: GeneratorRegistry | None = None
        self._templates: dict[tuple[str, str | None, bool], ApplicationTemplate] = {}
        self._state: PanelRuntimeState = "initializing"
        self._started_at: float | None = None
        self._runtime_ready_seconds: float | None = None
        self._closed = False

    @property
    def state(self) -> PanelRuntimeState:
        return self._state

    @property
    def stages(self) -> tuple[InitializationStage, ...]:
        return tuple(self._stages)

    @property
    def runtime_ready_seconds(self) -> float | None:
        return self._runtime_ready_seconds

    @property
    def registry(self) -> GeneratorRegistry:
        if self._registry is None:
            raise ConfigurationError("panel runtime is not initialized")
        return self._registry

    async def initialize(
        self,
        observer: Callable[[InitializationStage], None] | None = None,
    ) -> tuple[InitializationStage, ...]:
        """Run (or join) the single shared initialization task."""

        if observer is not None:
            self._observers.append(observer)
            for stage in self._stages:
                _notify(observer, stage)
        if self._initialization is None:
            self._started_at = self._clock()
            self._initialization = asyncio.create_task(self._run_initialization())
        return await self._initialization

    async def _run_initialization(self) -> tuple[InitializationStage, ...]:
        created: list[object] = []
        try:
            await self._stage("CHECKING_CONFIG", self._check_config, created)
            index = cast(
                HybridIndex,
                await self._stage("LOADING_INDEX", self._load_index, created),
            )
            retrieval = cast(
                RetrievalRuntimeComponents,
                await self._stage(
                    "LOADING_RETRIEVER",
                    lambda created: self._load_retriever(index, created),
                    created,
                ),
            )
            reranking = cast(
                RerankingRuntimeComponents,
                await self._stage("LOADING_RERANKER", self._load_reranker, created),
            )
            await self._stage(
                "PREPARING_DEFAULT_LOCAL_GENERATOR",
                self._prepare_local_generator,
                created,
                blocking=False,
            )
            await self._stage(
                "READY",
                lambda created: self._assemble(retrieval, reranking),
                created,
                blocking=False,
            )
        except Exception:
            await self._close_partial(created)
            self._state = "initialization_failed"
            return self.stages
        self._state = "ready"
        if self._started_at is not None:
            self._runtime_ready_seconds = max(0.0, self._clock() - self._started_at)
        return self.stages

    def _check_config(self, created: list[object]) -> None:
        config = self._config or AppConfig.from_env()
        warnings: list[str] = []
        profiles: tuple[ProviderProfile, ...] = ()
        try:
            profiles = self._profile_loader(self._provider_profiles_path)
        except Exception:
            warnings.append("online provider profiles are unavailable")
        self._config = config
        self._registry = GeneratorRegistry(
            config,
            profiles,
            local_factory=self._local_factory,
            online_factory=self._online_factory,
            environ=self._environ,
        )
        tools = load_runtime_tools(
            self._records_root,
            tool_loader=self._tool_loader,
        )
        created.append(tools)
        self._tools = tools
        self._warnings = tuple(warnings)

    def _load_index(self, created: list[object]) -> HybridIndex:
        index = load_runtime_index(
            self._require_config(),
            index_loader=self._index_loader or _default_index_loader(),
        )
        created.append(index)
        return index

    def _load_retriever(
        self,
        index: HybridIndex,
        created: list[object],
    ) -> RetrievalRuntimeComponents:
        retrieval = build_runtime_retrieval(
            self._require_config(),
            index,
            encoder_factory=self._encoder_factory,
        )
        created.append(retrieval)
        return retrieval

    def _load_reranker(self, created: list[object]) -> RerankingRuntimeComponents:
        reranking = build_runtime_reranking(
            self._require_config(),
            reranker_factory=self._reranker_factory,
            support_scorer_factory=self._support_scorer_factory,
        )
        created.append(reranking)
        return reranking

    async def _prepare_local_generator(self, created: list[object]) -> None:
        backend = await self._registry_or_raise().get_local()
        created.append(backend)

    def _assemble(
        self,
        retrieval: RetrievalRuntimeComponents,
        reranking: RerankingRuntimeComponents,
    ) -> None:
        tools = self._tools
        if tools is None:
            raise ConfigurationError("panel runtime tools were not loaded")
        self._shared = assemble_shared_runtime_components(
            self._require_config(),
            retrieval=retrieval,
            reranking=reranking,
            tools=tools,
        )

    async def _stage(
        self,
        name: str,
        action: Callable[[list[object]], object],
        created: list[object],
        *,
        blocking: bool = True,
    ) -> object:
        started = self._clock()
        try:
            # Blocking loaders (index, encoder, reranker, tools) must never stall
            # the event loop that renders the page and serves Stop.
            result = (
                await asyncio.to_thread(action, created)
                if blocking
                else action(created)
            )
            if asyncio.iscoroutine(result):
                result = await result
        except Exception as error:
            category = _exception_category(error)
            self._record(
                InitializationStage(
                    name=name,
                    status="failed",
                    duration_ms=max(0.0, (self._clock() - started) * 1000.0),
                    error_category=category,
                    public_error=f"panel initialization failed at {name}: {category}",
                )
            )
            raise
        self._record(
            InitializationStage(
                name=name,
                status="succeeded",
                duration_ms=max(0.0, (self._clock() - started) * 1000.0),
                warnings=self._warnings if name == "CHECKING_CONFIG" else (),
            )
        )
        return result

    async def _close_partial(self, created: list[object]) -> None:
        """Close the resources of every created stage product, in reverse order."""

        seen: set[int] = set()
        for product in reversed(created):
            for resource in runtime_component_resources(product):
                if id(resource) in seen:
                    continue
                seen.add(id(resource))
                await aclose_runtime_component(resource)

    def _record(self, stage: InitializationStage) -> None:
        self._stages.append(stage)
        for observer in tuple(self._observers):
            _notify(observer, stage)

    def _require_config(self) -> AppConfig:
        if self._config is None:
            raise ConfigurationError("panel runtime configuration is unavailable")
        return self._config

    def _registry_or_raise(self) -> GeneratorRegistry:
        if self._registry is None:
            raise ConfigurationError("panel runtime registry is unavailable")
        return self._registry

    async def application_for(
        self,
        spec: RunSpec,
        token: CancellationToken,
        observer: TraceObserver | None = None,
    ) -> Application:
        """Bind one single-run Application over the cached template."""

        if not isinstance(spec, RunSpec):
            raise TypeError("spec must be a RunSpec")
        if not isinstance(token, CancellationToken):
            raise TypeError("token must be a CancellationToken")
        shared = self._shared
        if shared is None or self._state != "ready":
            raise ConfigurationError("panel runtime is not ready")
        backend = await self.registry.backend_for(spec)
        key = (spec.generator, spec.provider_fingerprint, spec.fallback_local)
        template = self._templates.get(key)
        if template is None:
            template = ApplicationTemplate(
                shared=shared,
                generator_kind=spec.generator,
                provider_name=spec.provider_name,
                provider_fingerprint=spec.provider_fingerprint,
                fallback_local=spec.fallback_local,
            )
            self._templates[key] = template
        return template.bind(
            generator_backend=backend,
            trace_factory=lambda: TraceRecorder(spec.run_id, observer=observer),
            cancellation=token,
        )

    async def aclose(self) -> None:
        """Settle initialization, close owned resources once, clear templates."""

        if self._closed:
            return
        self._closed = True
        if self._initialization is not None:
            try:
                await self._initialization
            except Exception:
                pass
        if self._shared is not None:
            await aclose_runtime_components(self._shared)
            self._shared = None
        if self._registry is not None:
            await self._registry.aclose()
        self._templates.clear()


def _notify(
    observer: Callable[[InitializationStage], None],
    stage: InitializationStage,
) -> None:
    try:
        observer(stage)
    except Exception:
        return


def _exception_category(error: Exception) -> str:
    category = getattr(error, "category", ErrorCategory.PROTOCOL)
    if isinstance(category, ErrorCategory):
        category = category.value
    if isinstance(category, str) and category:
        return category
    return ErrorCategory.PROTOCOL.value


def _create_local_generator(config: AppConfig) -> StructuredGeneratorPort:
    return LlamaCppGenerator.from_config(config)


def _create_online_generator(
    config: AppConfig,
    profile: ProviderProfile,
) -> StructuredGeneratorPort:
    return OnlineGenerator(
        profile,
        allow_network=config.allow_network,
        timeout_seconds=config.online_timeout_seconds,
        input_token_cap=16_384,
    )


def _default_index_loader() -> IndexLoader:
    from ..index_store import load_index

    return load_index
