# The public taxonomy must contain exactly the approved machine-readable values.
def test_error_category_has_all_approved_values() -> None:
    from enterprise_rag.errors import ErrorCategory

    assert {category.value for category in ErrorCategory} == {
        "configuration",
        "index",
        "embedding",
        "reranker_configuration",
        "reranker_unavailable",
        "reranker_timeout",
        "reranker_protocol",
        "local_unavailable",
        "local_missing",
        "local_timeout",
        "local_resource",
        "authentication",
        "permission",
        "quota",
        "balance",
        "rate_limit",
        "timeout",
        "provider_unavailable",
        "invalid_request",
        "protocol",
        "tool_not_found",
        "tool_validation",
        "iteration_limit",
        "retrieval_unavailable",
        "retrieval_protocol",
        "evidence_insufficient",
        "generation_protocol",
        "grounding_failed",
        "tool_execution",
        "duplicate_action",
        "budget_blocked",
        "privacy_blocked",
        "profile_not_allowed",
    }


def test_preexisting_errors_keep_their_distinct_categories() -> None:
    from enterprise_rag.errors import (
        AgentIterationLimitError,
        ConfigurationError,
        EmbeddingError,
        ErrorCategory,
        IndexUnavailableError,
        LocalModelMissingError,
        LocalModelResourceError,
        LocalModelTimeoutError,
        LocalModelUnavailableError,
        ProviderAuthenticationError,
        ProviderBalanceError,
        ProviderInvalidRequestError,
        ProviderPermissionError,
        ProviderProtocolError,
        ProviderQuotaError,
        ProviderRateLimitError,
        ProviderTimeoutError,
        ProviderUnavailableError,
        RerankerConfigurationError,
        RerankerProtocolError,
        RerankerTimeoutError,
        RerankerUnavailableError,
        ToolNotFoundError,
        ToolValidationError,
    )

    expected = {
        ConfigurationError: ErrorCategory.CONFIGURATION,
        IndexUnavailableError: ErrorCategory.INDEX,
        EmbeddingError: ErrorCategory.EMBEDDING,
        RerankerConfigurationError: ErrorCategory.RERANKER_CONFIGURATION,
        RerankerUnavailableError: ErrorCategory.RERANKER_UNAVAILABLE,
        RerankerTimeoutError: ErrorCategory.RERANKER_TIMEOUT,
        RerankerProtocolError: ErrorCategory.RERANKER_PROTOCOL,
        LocalModelUnavailableError: ErrorCategory.LOCAL_UNAVAILABLE,
        LocalModelMissingError: ErrorCategory.LOCAL_MISSING,
        LocalModelTimeoutError: ErrorCategory.LOCAL_TIMEOUT,
        LocalModelResourceError: ErrorCategory.LOCAL_RESOURCE,
        ProviderAuthenticationError: ErrorCategory.AUTHENTICATION,
        ProviderPermissionError: ErrorCategory.PERMISSION,
        ProviderQuotaError: ErrorCategory.QUOTA,
        ProviderBalanceError: ErrorCategory.BALANCE,
        ProviderRateLimitError: ErrorCategory.RATE_LIMIT,
        ProviderTimeoutError: ErrorCategory.TIMEOUT,
        ProviderUnavailableError: ErrorCategory.PROVIDER_UNAVAILABLE,
        ProviderInvalidRequestError: ErrorCategory.INVALID_REQUEST,
        ProviderProtocolError: ErrorCategory.PROTOCOL,
        ToolNotFoundError: ErrorCategory.TOOL_NOT_FOUND,
        ToolValidationError: ErrorCategory.TOOL_VALIDATION,
        AgentIterationLimitError: ErrorCategory.ITERATION_LIMIT,
    }

    assert len(expected) == 23
    assert all(
        error_type.category is category
        for error_type, category in expected.items()
    )


def test_new_failures_have_specific_machine_readable_categories() -> None:
    from enterprise_rag.errors import (
        BudgetBlockedError,
        DuplicateActionError,
        ErrorCategory,
        EvidenceInsufficientError,
        GenerationProtocolError,
        GroundingFailedError,
        PrivacyBlockedError,
        ProfileNotAllowedError,
        RetrievalProtocolError,
        RetrievalUnavailableError,
        ToolExecutionError,
    )

    expected = {
        RetrievalUnavailableError: ErrorCategory.RETRIEVAL_UNAVAILABLE,
        RetrievalProtocolError: ErrorCategory.RETRIEVAL_PROTOCOL,
        EvidenceInsufficientError: ErrorCategory.EVIDENCE_INSUFFICIENT,
        GenerationProtocolError: ErrorCategory.GENERATION_PROTOCOL,
        GroundingFailedError: ErrorCategory.GROUNDING_FAILED,
        ToolExecutionError: ErrorCategory.TOOL_EXECUTION,
        DuplicateActionError: ErrorCategory.DUPLICATE_ACTION,
        BudgetBlockedError: ErrorCategory.BUDGET_BLOCKED,
        PrivacyBlockedError: ErrorCategory.PRIVACY_BLOCKED,
        ProfileNotAllowedError: ErrorCategory.PROFILE_NOT_ALLOWED,
    }
    errors = tuple(error_type("test failure") for error_type in expected)

    assert len({type(error) for error in errors}) == 10
    assert tuple(error.category for error in errors) == tuple(expected.values())
    assert all(isinstance(error, Exception) for error in errors)
