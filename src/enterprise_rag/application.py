from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol, Self

from .agent_service import AgentService
from .agent_tools import ToolRegistry
from .config import AppConfig
from .errors import ConfigurationError
from .execution_control import CancellationToken
from .execution_trace import TraceRecorder, _freeze_mapping
from .generation import (
    AgentPlanner,
    AnswerGenerator,
    ExplicitLocalFallbackGenerator,
    LlamaCppGenerator,
    OnlineGenerator,
)
from .grounding import EvidenceGate, GroundingValidator, GteClaimSupportScorer
from .index_store import HybridIndex, build_index, load_index
from .models import AgentResult, RagResult
from .ports import (
    ClaimSupportScorerPort,
    HybridEncoderPort,
    RerankerPort,
    StructuredGeneratorPort,
)
from .provider_profiles import ProviderProfile, load_profiles
from .rag_service import RagService
from .reranking import GteReranker
from .retrieval import GteHybridEncoder, HybridRetriever

GeneratorChoice = Literal["local", "online"]
EvaluationLayer = Literal["retriever", "reranker", "rag", "agent", "all"]
EncoderFactory = Callable[[AppConfig], HybridEncoderPort]
RerankerFactory = Callable[[AppConfig], RerankerPort]
GeneratorFactory = Callable[[AppConfig], StructuredGeneratorPort]
OnlineGeneratorFactory = Callable[
    [AppConfig, ProviderProfile], StructuredGeneratorPort
]
ProfileLoader = Callable[[Path], tuple[ProviderProfile, ...]]
SupportScorerFactory = Callable[[RerankerPort], ClaimSupportScorerPort]
IndexLoader = Callable[[Path], HybridIndex]


@dataclass(frozen=True, slots=True)
class RagAskRequest:
    question: str
    generator: GeneratorChoice
    provider: str | None = None
    fallback_local: bool | None = None

    def __post_init__(self) -> None:
        _validate_answer_request(
            self.question,
            self.generator,
            self.provider,
            self.fallback_local,
            text_name="question",
        )


@dataclass(frozen=True, slots=True)
class AgentRunRequest:
    task: str
    generator: GeneratorChoice
    provider: str | None = None
    fallback_local: bool | None = None

    def __post_init__(self) -> None:
        _validate_answer_request(
            self.task,
            self.generator,
            self.provider,
            self.fallback_local,
            text_name="task",
        )


@dataclass(frozen=True, slots=True)
class CommandOutcome:
    status: Literal["succeeded", "failed"]
    data: Mapping[str, object]
    error_category: str | None

    def __post_init__(self) -> None:
        if self.status not in {"succeeded", "failed"}:
            raise ValueError("invalid command outcome status")
        object.__setattr__(
            self,
            "data",
            _freeze_mapping(self.data, redact_sensitive=True),
        )
        if self.status == "succeeded" and self.error_category is not None:
            raise ValueError("succeeded outcome cannot have error_category")
        if self.status == "failed" and (
            not isinstance(self.error_category, str)
            or not self.error_category.strip()
        ):
            raise ValueError("failed outcome requires error_category")

    @classmethod
    def succeeded(cls, data: Mapping[str, object]) -> Self:
        return cls("succeeded", data, None)

    @classmethod
    def failed(
        cls,
        error_category: str,
        data: Mapping[str, object] | None = None,
    ) -> Self:
        return cls("failed", data or {}, error_category)


class AgentServicePort(Protocol):
    async def run(self, task: str) -> AgentResult: ...


class IndexBuilderPort(Protocol):
    async def build(self, document_root: Path) -> CommandOutcome: ...


class EvaluatorPort(Protocol):
    async def run(self, layer: str) -> CommandOutcome: ...


class DiagnosticsPort(Protocol):
    async def run(self) -> CommandOutcome: ...


class ApplicationPort(Protocol):
    async def build_knowledge(self, path: Path) -> CommandOutcome: ...

    async def ask_rag(self, request: RagAskRequest) -> RagResult: ...

    async def run_agent(self, request: AgentRunRequest) -> AgentResult: ...

    async def run_evaluation(self, layer: str) -> CommandOutcome: ...

    async def run_diagnostics(self) -> CommandOutcome: ...


@dataclass(slots=True)
class GteIndexBuilder:
    config: AppConfig
    encoder_factory: EncoderFactory
    _encoder: HybridEncoderPort | None = field(
        init=False,
        default=None,
        repr=False,
    )

    @classmethod
    def with_encoder(
        cls,
        config: AppConfig,
        encoder: HybridEncoderPort,
    ) -> GteIndexBuilder:
        """Build an index builder that reuses an already loaded encoder."""

        if not isinstance(config, AppConfig):
            raise TypeError("config must be an AppConfig")
        builder = cls(config, _create_gte_encoder)
        builder._encoder = encoder
        return builder

    async def build(self, document_root: Path) -> CommandOutcome:
        if self._encoder is None:
            self._encoder = self.encoder_factory(self.config)
        manifest = await build_index(
            document_root,
            self.config.index_dir,
            self._encoder,
            chunk_size=self.config.chunk_size,
            chunk_overlap=self.config.chunk_overlap,
        )
        return CommandOutcome.succeeded(
            {
                "backend_kind": manifest.backend_kind,
                "model_id": manifest.model_id,
                "model_revision": manifest.model_revision,
                "code_revision": manifest.code_revision,
                "source_digest": manifest.source_digest,
                "row_count": manifest.row_count,
                "index_dir": str(self.config.index_dir),
            }
        )


@dataclass(slots=True)
class Application:
    rag_service: RagService | None
    generator_kind: GeneratorChoice | None
    agent_service: AgentServicePort | None
    index_builder: IndexBuilderPort
    evaluator: EvaluatorPort | None
    diagnostics: DiagnosticsPort | None
    provider_name: str | None = None
    fallback_local_enabled: bool = False

    async def build_knowledge(self, path: Path) -> CommandOutcome:
        return await self.index_builder.build(path)

    async def ask_rag(self, request: RagAskRequest) -> RagResult:
        if self.rag_service is None or not self._matches_generator_request(
            request.generator,
            request.provider,
            request.fallback_local,
        ):
            return RagResult.failed("configuration")
        return await self.rag_service.ask(request.question)

    async def run_agent(self, request: AgentRunRequest) -> AgentResult:
        if self.agent_service is None or not self._matches_generator_request(
            request.generator,
            request.provider,
            request.fallback_local,
        ):
            return AgentResult.failed("configuration")
        return await self.agent_service.run(request.task)

    def _matches_generator_request(
        self,
        generator: GeneratorChoice,
        provider: str | None,
        fallback_local: bool | None,
    ) -> bool:
        return (
            generator == self.generator_kind
            and provider == self.provider_name
            and (fallback_local is True) == self.fallback_local_enabled
        )

    async def run_evaluation(self, layer: str) -> CommandOutcome:
        if self.evaluator is None:
            return CommandOutcome.failed("configuration")
        return await self.evaluator.run(layer)

    async def run_diagnostics(self) -> CommandOutcome:
        if self.diagnostics is None:
            return CommandOutcome.failed("configuration")
        return await self.diagnostics.run()


@dataclass(frozen=True, slots=True)
class RetrievalRuntimeComponents:
    """Products of the index and encoder stage, timed as one unit."""

    index: HybridIndex
    encoder: HybridEncoderPort
    retriever: HybridRetriever


@dataclass(frozen=True, slots=True)
class RerankingRuntimeComponents:
    """Products of the reranker stage, including its derived gate and validator."""

    reranker: RerankerPort
    support_scorer: ClaimSupportScorerPort
    gate: EvidenceGate
    validator: GroundingValidator


@dataclass(frozen=True, slots=True)
class SharedRuntimeComponents:
    """Heavy components shared by every run, with no generator or per-run state.

    The panel caches this once and binds a fresh generator, recorder, and
    cancellation token per run, so nothing here may hold run-scoped state.
    """

    config: AppConfig
    index: HybridIndex
    encoder: HybridEncoderPort
    retriever: HybridRetriever
    reranker: RerankerPort
    support_scorer: ClaimSupportScorerPort
    gate: EvidenceGate
    validator: GroundingValidator
    tools: ToolRegistry
    index_builder: GteIndexBuilder


def load_runtime_index(
    config: AppConfig,
    *,
    index_loader: IndexLoader = load_index,
) -> HybridIndex:
    """Load and validate the hybrid index as its own initialization stage."""

    if not isinstance(config, AppConfig):
        raise TypeError("config must be an AppConfig")
    index = index_loader(config.index_dir)
    _require_real_backend("index", index.manifest)
    return index


def build_runtime_retrieval(
    config: AppConfig,
    index: HybridIndex,
    *,
    encoder_factory: EncoderFactory | None = None,
) -> RetrievalRuntimeComponents:
    """Load the query encoder and build the retriever over an existing index."""

    if not isinstance(config, AppConfig):
        raise TypeError("config must be an AppConfig")
    if not isinstance(index, HybridIndex):
        raise TypeError("index must be a HybridIndex")
    encoder = (encoder_factory or _create_gte_encoder)(config)
    _require_real_backend("encoder", encoder)
    return RetrievalRuntimeComponents(
        index=index,
        encoder=encoder,
        retriever=HybridRetriever(index, encoder, rrf_k=config.rrf_k),
    )


def build_runtime_reranking(
    config: AppConfig,
    *,
    reranker_factory: RerankerFactory | None = None,
    support_scorer_factory: SupportScorerFactory | None = None,
) -> RerankingRuntimeComponents:
    """Load the reranker and derive the Evidence Gate and validator from it."""

    if not isinstance(config, AppConfig):
        raise TypeError("config must be an AppConfig")
    reranker = (reranker_factory or _create_gte_reranker)(config)
    _require_real_backend("reranker", reranker)
    support_scorer = (support_scorer_factory or _create_gte_support_scorer)(reranker)
    _require_real_backend("claim support scorer", support_scorer)
    return RerankingRuntimeComponents(
        reranker=reranker,
        support_scorer=support_scorer,
        gate=EvidenceGate(min_score=config.min_rerank_score),
        validator=GroundingValidator(
            support_scorer,
            min_claim_support_score=config.min_claim_support_score,
        ),
    )


def load_runtime_tools(
    records_root: Path = Path("data/sample_records"),
    *,
    tool_loader: Callable[[Path], ToolRegistry] = ToolRegistry.from_directory,
) -> ToolRegistry:
    """Load the read-only business tools as their own initialization stage."""

    if not isinstance(records_root, Path):
        raise TypeError("records_root must be a Path")
    return tool_loader(records_root)


def assemble_shared_runtime_components(
    config: AppConfig,
    *,
    retrieval: RetrievalRuntimeComponents,
    reranking: RerankingRuntimeComponents,
    tools: ToolRegistry,
) -> SharedRuntimeComponents:
    """Combine already-created stage products. Performs no I/O and loads nothing."""

    if not isinstance(config, AppConfig):
        raise TypeError("config must be an AppConfig")
    if not isinstance(retrieval, RetrievalRuntimeComponents):
        raise TypeError("retrieval must be RetrievalRuntimeComponents")
    if not isinstance(reranking, RerankingRuntimeComponents):
        raise TypeError("reranking must be RerankingRuntimeComponents")
    if not isinstance(tools, ToolRegistry):
        raise TypeError("tools must be a ToolRegistry")
    return SharedRuntimeComponents(
        config=config,
        index=retrieval.index,
        encoder=retrieval.encoder,
        retriever=retrieval.retriever,
        reranker=reranking.reranker,
        support_scorer=reranking.support_scorer,
        gate=reranking.gate,
        validator=reranking.validator,
        tools=tools,
        index_builder=GteIndexBuilder.with_encoder(config, retrieval.encoder),
    )


async def aclose_runtime_components(shared: SharedRuntimeComponents) -> None:
    """Close every unique heavy resource the shared runtime owns, once each.

    Only the runtime may call this: a bound Application or template never owns
    these objects. Components without a close hook are ignored, and a shared
    object (for example a support scorer wrapping the reranker) closes once.
    """

    if not isinstance(shared, SharedRuntimeComponents):
        raise TypeError("shared must be SharedRuntimeComponents")
    owned: tuple[object, ...] = (
        shared.tools,
        shared.support_scorer,
        shared.reranker,
        shared.encoder,
        shared.index,
    )
    seen: set[int] = set()
    for component in owned:
        if id(component) in seen:
            continue
        seen.add(id(component))
        await aclose_runtime_component(component)


def runtime_component_resources(product: object) -> tuple[object, ...]:
    """Flatten a stage product into the heavy resources it owns.

    A stage product is a container, so a partial-initialization rollback must
    close the encoder, reranker, and index inside it rather than the container.
    """

    if isinstance(product, RetrievalRuntimeComponents):
        return (product.encoder, product.index)
    if isinstance(product, RerankingRuntimeComponents):
        return (product.support_scorer, product.reranker)
    return (product,)


async def aclose_runtime_component(component: object) -> None:
    """Close one owned component once, ignoring objects without a close hook.

    Synchronous hooks run through a worker thread so a blocking close never
    stalls the event loop. Callers that must not double-close dedupe by identity.
    """

    aclose = getattr(component, "aclose", None)
    if callable(aclose):
        result = aclose()
        if inspect.isawaitable(result):
            await result
        return
    close = getattr(component, "close", None)
    if callable(close):
        await asyncio.to_thread(close)


@dataclass(frozen=True, slots=True)
class ApplicationTemplate:
    """A cacheable, authorization-free recipe for binding single-run Applications.

    It holds only shared heavy components plus the generator selection, so it is
    safe to cache by ``(generator_kind, provider_fingerprint, fallback_local)``:
    no run ID, recorder, cancellation token, or one-use authorization lives here.
    """

    shared: SharedRuntimeComponents
    generator_kind: GeneratorChoice
    provider_name: str | None
    provider_fingerprint: str | None
    fallback_local: bool

    def __post_init__(self) -> None:
        if not isinstance(self.shared, SharedRuntimeComponents):
            raise TypeError("shared must be SharedRuntimeComponents")
        if self.generator_kind not in {"local", "online"}:
            raise ValueError("generator_kind must be local or online")
        if self.generator_kind == "local":
            if self.provider_name is not None:
                raise ValueError("local template cannot bind a provider")
            if self.provider_fingerprint is not None:
                raise ValueError("local template cannot bind a profile fingerprint")
            if self.fallback_local:
                raise ValueError("local template cannot enable fallback")
            return
        if self.provider_name is None:
            raise ValueError("online template requires a provider name")
        if self.provider_fingerprint is None:
            raise ValueError("online template requires a profile fingerprint")

    def bind(
        self,
        *,
        generator_backend: StructuredGeneratorPort,
        trace_factory: Callable[[], TraceRecorder],
        cancellation: CancellationToken,
    ) -> Application:
        """Create one single-run Application over the shared components."""

        _require_real_backend("generator", generator_backend)
        shared = self.shared
        rag_service = RagService(
            retriever=shared.retriever,
            reranker=shared.reranker,
            gate=shared.gate,
            generator=AnswerGenerator(generator_backend),
            validator=shared.validator,
            retrieve_k=shared.config.retrieve_k,
            rerank_k=shared.config.rerank_k,
            trace_factory=trace_factory,
            cancellation=cancellation,
        )
        return Application(
            rag_service=rag_service,
            generator_kind=self.generator_kind,
            agent_service=AgentService(
                planner=AgentPlanner(generator_backend),
                rag=rag_service,
                tools=shared.tools,
                validator=shared.validator,
                max_actions=shared.config.agent_max_actions,
                trace_factory=trace_factory,
                cancellation=cancellation,
            ),
            index_builder=shared.index_builder,
            evaluator=None,
            diagnostics=None,
            provider_name=self.provider_name,
            fallback_local_enabled=self.fallback_local,
        )


def build_application(
    config: AppConfig | None = None,
    *,
    encoder_factory: EncoderFactory | None = None,
    reranker_factory: RerankerFactory | None = None,
    generator_factory: GeneratorFactory | None = None,
    online_generator_factory: OnlineGeneratorFactory | None = None,
    support_scorer_factory: SupportScorerFactory | None = None,
    index_loader: IndexLoader = load_index,
    profile_loader: ProfileLoader = load_profiles,
    compose_rag: bool = True,
    compose_agent: bool = False,
    compose_evaluation: bool = False,
    compose_diagnostics: bool = False,
    evaluation_layer: EvaluationLayer | None = None,
    records_root: Path = Path("data/sample_records"),
    knowledge_cases_path: Path = Path("evals/knowledge.jsonl"),
    agent_cases_path: Path = Path("evals/agent.jsonl"),
    generator_choice: GeneratorChoice | None = None,
    provider_name: str | None = None,
    fallback_local: bool = False,
    provider_profiles_path: Path = Path("config/providers.toml"),
    trace_factory: Callable[[], TraceRecorder] | None = None,
    cancellation: CancellationToken | None = None,
) -> Application:
    active_config = config or AppConfig.from_env()
    if type(compose_rag) is not bool:
        raise TypeError("compose_rag must be a boolean")
    if type(compose_agent) is not bool:
        raise TypeError("compose_agent must be a boolean")
    if type(compose_evaluation) is not bool:
        raise TypeError("compose_evaluation must be a boolean")
    if type(compose_diagnostics) is not bool:
        raise TypeError("compose_diagnostics must be a boolean")
    if compose_evaluation:
        if evaluation_layer not in {
            "retriever",
            "reranker",
            "rag",
            "agent",
            "all",
        }:
            raise ValueError("evaluation_layer is required for evaluation")
        compose_rag = True
        compose_agent = evaluation_layer in {"agent", "all"}
    elif evaluation_layer is not None:
        raise ValueError("evaluation_layer requires compose_evaluation")
    if compose_agent and not compose_rag:
        raise ValueError("compose_agent requires compose_rag")
    if not isinstance(records_root, Path):
        raise TypeError("records_root must be a Path")
    if not isinstance(knowledge_cases_path, Path):
        raise TypeError("knowledge_cases_path must be a Path")
    if not isinstance(agent_cases_path, Path):
        raise TypeError("agent_cases_path must be a Path")
    if generator_choice is not None and generator_choice not in {"local", "online"}:
        raise ValueError("generator_choice must be local or online")
    if provider_name is not None and (
        not isinstance(provider_name, str) or not provider_name.strip()
    ):
        raise ValueError("provider_name must be a non-empty string")
    if type(fallback_local) is not bool:
        raise TypeError("fallback_local must be a boolean")
    if not isinstance(provider_profiles_path, Path):
        raise TypeError("provider_profiles_path must be a Path")
    selected_generator = generator_choice or active_config.generator
    if selected_generator == "local" and provider_name is not None:
        raise ValueError("provider_name is only valid for online generation")
    if selected_generator == "local" and fallback_local:
        raise ValueError("fallback_local is only valid for online generation")
    if selected_generator == "online" and provider_name is None:
        raise ConfigurationError("online generation requires a provider profile")
    selected_profile: ProviderProfile | None = None
    if compose_rag and selected_generator == "online":
        selected_profile = _select_provider_profile(
            profile_loader(provider_profiles_path),
            provider_name,
        )
    active_encoder_factory = encoder_factory or _create_gte_encoder
    index_builder = GteIndexBuilder(active_config, active_encoder_factory)
    rag_service: RagService | None = None
    agent_service: AgentServicePort | None = None
    evaluator: EvaluatorPort | None = None
    diagnostics: DiagnosticsPort | None = None
    generator_kind: GeneratorChoice | None = None
    if compose_rag:
        index = index_loader(active_config.index_dir)
        _require_real_backend("index", index.manifest)
        encoder = active_encoder_factory(active_config)
        _require_real_backend("encoder", encoder)
        reranker = (reranker_factory or _create_gte_reranker)(active_config)
        _require_real_backend("reranker", reranker)
        local_factory = generator_factory or _create_local_generator
        if selected_generator == "local":
            generator_backend = local_factory(active_config)
        else:
            if selected_profile is None:
                raise AssertionError("online profile preflight was skipped")
            online_factory = online_generator_factory or _create_online_generator
            online_backend = online_factory(active_config, selected_profile)
            _require_real_backend("online generator", online_backend)
            if fallback_local:
                local_backend = local_factory(active_config)
                _require_real_backend("local fallback generator", local_backend)
                generator_backend = ExplicitLocalFallbackGenerator(
                    online_backend,
                    local_backend,
                )
            else:
                generator_backend = online_backend
        _require_real_backend("generator", generator_backend)
        support_scorer = (support_scorer_factory or _create_gte_support_scorer)(
            reranker
        )
        _require_real_backend("claim support scorer", support_scorer)

        retriever = HybridRetriever(index, encoder, rrf_k=active_config.rrf_k)
        answer_generator = AnswerGenerator(generator_backend)
        validator = GroundingValidator(
            support_scorer,
            min_claim_support_score=active_config.min_claim_support_score,
        )
        rag_service = RagService(
            retriever=retriever,
            reranker=reranker,
            gate=EvidenceGate(min_score=active_config.min_rerank_score),
            generator=answer_generator,
            validator=validator,
            retrieve_k=active_config.retrieve_k,
            rerank_k=active_config.rerank_k,
            trace_factory=trace_factory,
            cancellation=cancellation,
        )
        generator_kind = selected_generator
        index_builder._encoder = encoder
        if compose_agent:
            agent_service = AgentService(
                planner=AgentPlanner(generator_backend),
                rag=rag_service,
                tools=ToolRegistry.from_directory(records_root),
                validator=validator,
                max_actions=active_config.agent_max_actions,
                trace_factory=trace_factory,
                cancellation=cancellation,
            )
        if compose_evaluation:
            from .evaluation import (
                BackendDescriptor,
                LayeredEvaluator,
                load_agent_cases,
                load_knowledge_cases,
            )

            evaluator = LayeredEvaluator(
                knowledge_cases=load_knowledge_cases(knowledge_cases_path),
                agent_cases=load_agent_cases(agent_cases_path),
                retriever=retriever,
                reranker=reranker,
                rag=rag_service,
                agent=agent_service,
                retrieve_k=active_config.retrieve_k,
                rerank_k=active_config.rerank_k,
                corpus_digest=index.manifest.source_digest,
                descriptors={
                    "retriever": (
                        BackendDescriptor.from_component("encoder", encoder),
                    ),
                    "reranker": (
                        BackendDescriptor.from_component("encoder", encoder),
                        BackendDescriptor.from_component("reranker", reranker),
                    ),
                    "rag": (
                        BackendDescriptor.from_component("encoder", encoder),
                        BackendDescriptor.from_component("reranker", reranker),
                        BackendDescriptor.from_component(
                            "generator",
                            generator_backend,
                        ),
                    ),
                    "agent": (
                        BackendDescriptor.from_component("encoder", encoder),
                        BackendDescriptor.from_component("reranker", reranker),
                        BackendDescriptor.from_component(
                            "generator",
                            generator_backend,
                        ),
                    ),
                },
            )
    if compose_diagnostics:
        from .diagnostics import Diagnostics

        diagnostics = Diagnostics(
            active_config,
            provider_profiles_path=provider_profiles_path,
            profile_loader=profile_loader,
        )

    return Application(
        rag_service=rag_service,
        generator_kind=generator_kind,
        agent_service=agent_service,
        index_builder=index_builder,
        evaluator=evaluator,
        diagnostics=diagnostics,
        provider_name=provider_name,
        fallback_local_enabled=fallback_local,
    )


def _create_gte_encoder(config: AppConfig) -> HybridEncoderPort:
    return GteHybridEncoder.from_config(config)


def _create_gte_reranker(config: AppConfig) -> RerankerPort:
    return GteReranker.from_config(config)


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


def _select_provider_profile(
    profiles: tuple[ProviderProfile, ...],
    name: str | None,
) -> ProviderProfile:
    if name is None:
        raise ConfigurationError("online generation requires a provider profile")
    matches = tuple(profile for profile in profiles if profile.name == name)
    if len(matches) != 1:
        raise ConfigurationError("provider profile is missing or duplicated")
    return matches[0]


def _create_gte_support_scorer(
    reranker: RerankerPort,
) -> ClaimSupportScorerPort:
    if not isinstance(reranker, GteReranker):
        raise ConfigurationError(
            "the production claim support scorer requires the GTE reranker"
        )
    return GteClaimSupportScorer(reranker)


def _require_real_backend(name: str, component: object) -> None:
    if getattr(component, "backend_kind", None) != "real":
        raise ConfigurationError(
            f"{name} backend must be real in production composition"
        )


def _validate_answer_request(
    text: object,
    generator: object,
    provider: object,
    fallback_local: object,
    *,
    text_name: str,
) -> None:
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"{text_name} must be a non-empty string")
    if generator not in {"local", "online"}:
        raise ValueError("generator must be local or online")
    if provider is not None and (
        not isinstance(provider, str) or not provider.strip()
    ):
        raise ValueError("provider must be a non-empty string")
    if fallback_local not in {None, True}:
        raise ValueError("fallback_local must be absent or true")
    if generator == "local" and provider is not None:
        raise ValueError("provider is only valid for online generation")
    if generator == "local" and fallback_local is not None:
        raise ValueError("fallback_local is only valid for online generation")
