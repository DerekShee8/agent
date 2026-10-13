from enum import StrEnum


# Stable machine-readable categories used by exceptions, logs, and traces.
class ErrorCategory(StrEnum):
    CONFIGURATION = "configuration"
    INDEX = "index"
    EMBEDDING = "embedding"
    RERANKER_CONFIGURATION = "reranker_configuration"
    RERANKER_UNAVAILABLE = "reranker_unavailable"
    RERANKER_TIMEOUT = "reranker_timeout"
    RERANKER_PROTOCOL = "reranker_protocol"
    LOCAL_UNAVAILABLE = "local_unavailable"
    LOCAL_MISSING = "local_missing"
    LOCAL_TIMEOUT = "local_timeout"
    LOCAL_RESOURCE = "local_resource"
    AUTHENTICATION = "authentication"
    PERMISSION = "permission"
    QUOTA = "quota"
    BALANCE = "balance"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    INVALID_REQUEST = "invalid_request"
    PROTOCOL = "protocol"
    TOOL_NOT_FOUND = "tool_not_found"
    TOOL_VALIDATION = "tool_validation"
    ITERATION_LIMIT = "iteration_limit"
    RETRIEVAL_UNAVAILABLE = "retrieval_unavailable"
    RETRIEVAL_PROTOCOL = "retrieval_protocol"
    EVIDENCE_INSUFFICIENT = "evidence_insufficient"
    GENERATION_PROTOCOL = "generation_protocol"
    GROUNDING_FAILED = "grounding_failed"
    TOOL_EXECUTION = "tool_execution"
    DUPLICATE_ACTION = "duplicate_action"
    BUDGET_BLOCKED = "budget_blocked"
    PRIVACY_BLOCKED = "privacy_blocked"
    PROFILE_NOT_ALLOWED = "profile_not_allowed"


# Reranker-specific failures: configuration, availability, timeout, and protocol.
class RerankerConfigurationError(Exception):
    category = ErrorCategory.RERANKER_CONFIGURATION


class RerankerUnavailableError(Exception):
    category = ErrorCategory.RERANKER_UNAVAILABLE


class RerankerTimeoutError(Exception):
    category = ErrorCategory.RERANKER_TIMEOUT


class RerankerProtocolError(Exception):
    category = ErrorCategory.RERANKER_PROTOCOL


# Shared provider access failures: authentication, permission, quota, and limits.
class ProviderAuthenticationError(Exception):
    category = ErrorCategory.AUTHENTICATION


class ProviderPermissionError(Exception):
    category = ErrorCategory.PERMISSION


class ProviderQuotaError(Exception):
    category = ErrorCategory.QUOTA


class ProviderBalanceError(Exception):
    category = ErrorCategory.BALANCE


class ProviderRateLimitError(Exception):
    category = ErrorCategory.RATE_LIMIT


# Core application failures: configuration, index availability, and embedding.
class ConfigurationError(Exception):
    category = ErrorCategory.CONFIGURATION


class IndexUnavailableError(Exception):
    category = ErrorCategory.INDEX


class EmbeddingError(Exception):
    category = ErrorCategory.EMBEDDING


# Local model failures: availability, model presence, timeout, and resources.
class LocalModelUnavailableError(Exception):
    category = ErrorCategory.LOCAL_UNAVAILABLE


class LocalModelMissingError(Exception):
    category = ErrorCategory.LOCAL_MISSING


class LocalModelTimeoutError(Exception):
    category = ErrorCategory.LOCAL_TIMEOUT


class LocalModelResourceError(Exception):
    category = ErrorCategory.LOCAL_RESOURCE


# Shared provider runtime failures: timeout, availability, request, and protocol.
class ProviderTimeoutError(Exception):
    category = ErrorCategory.TIMEOUT


class ProviderUnavailableError(Exception):
    category = ErrorCategory.PROVIDER_UNAVAILABLE


class ProviderInvalidRequestError(Exception):
    category = ErrorCategory.INVALID_REQUEST


class ProviderProtocolError(Exception):
    category = ErrorCategory.PROTOCOL


# Tool boundary failures: registry lookup and argument validation.
class ToolNotFoundError(Exception):
    category = ErrorCategory.TOOL_NOT_FOUND


class ToolValidationError(Exception):
    category = ErrorCategory.TOOL_VALIDATION


# Agent loop-control failure: the configured iteration budget was exhausted.
class AgentIterationLimitError(Exception):
    category = ErrorCategory.ITERATION_LIMIT


# Retrieval and evidence failures remain stable across concrete implementations.
class RetrievalUnavailableError(Exception):
    category = ErrorCategory.RETRIEVAL_UNAVAILABLE


class RetrievalProtocolError(Exception):
    category = ErrorCategory.RETRIEVAL_PROTOCOL


class EvidenceInsufficientError(Exception):
    category = ErrorCategory.EVIDENCE_INSUFFICIENT


# Generation and grounding failures expose machine-readable boundaries.
class GenerationProtocolError(Exception):
    category = ErrorCategory.GENERATION_PROTOCOL


class GroundingFailedError(Exception):
    category = ErrorCategory.GROUNDING_FAILED


# Agent execution and policy failures never require message parsing.
class ToolExecutionError(Exception):
    category = ErrorCategory.TOOL_EXECUTION


class DuplicateActionError(Exception):
    category = ErrorCategory.DUPLICATE_ACTION


class BudgetBlockedError(Exception):
    category = ErrorCategory.BUDGET_BLOCKED


class PrivacyBlockedError(Exception):
    category = ErrorCategory.PRIVACY_BLOCKED


class ProfileNotAllowedError(Exception):
    category = ErrorCategory.PROFILE_NOT_ALLOWED
