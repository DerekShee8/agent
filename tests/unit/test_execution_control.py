import pytest

from enterprise_rag.execution_control import CancellationToken, RunCancelled


def test_token_starts_active_and_raises_only_after_cancel() -> None:
    token = CancellationToken()
    token.raise_if_cancelled()
    token.cancel()
    assert token.cancelled is True
    with pytest.raises(RunCancelled):
        token.raise_if_cancelled()


def test_cancel_is_idempotent() -> None:
    token = CancellationToken()
    token.cancel()
    token.cancel()
    assert token.cancelled is True
