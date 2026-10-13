from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
import warnings
from pathlib import Path
from types import SimpleNamespace

import pytest

import enterprise_rag.retrieval as retrieval_module
from enterprise_rag.config import (
    EMBEDDING_MODEL_ID,
    EMBEDDING_MODEL_REVISION,
    RERANKER_IMPLEMENTATION_REVISION,
    AppConfig,
)
from enterprise_rag.errors import RetrievalUnavailableError
from enterprise_rag.retrieval import (
    GteHybridEncoder,
    _prepare_audited_module_cache,
)

torch = pytest.importorskip("torch")


class FakeTokenizer:
    all_special_ids = (0, 1, 2)

    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def __call__(self, texts: tuple[str, ...], **kwargs: object) -> dict[str, object]:
        self.calls.append((texts, kwargs))
        return {
            "input_ids": torch.tensor([[0, 101, 202, 101, 1]]),
            "attention_mask": torch.tensor([[1, 1, 1, 1, 0]]),
        }


class FakeTokenModel:
    def __init__(self) -> None:
        self.forward_calls = 0
        self.eval_calls = 0
        self.to_calls: list[tuple[str, torch.dtype]] = []

    def eval(self) -> FakeTokenModel:
        self.eval_calls += 1
        return self

    def to(self, *, device: object, dtype: torch.dtype) -> FakeTokenModel:
        self.to_calls.append((str(device), dtype))
        return self

    def __call__(self, **inputs: object) -> SimpleNamespace:
        self.forward_calls += 1
        assert torch.is_grad_enabled() is False
        assert set(inputs) == {"input_ids", "attention_mask"}
        return SimpleNamespace(
            last_hidden_state=torch.tensor(
                [[[3.0, 4.0], [9.0, 9.0], [8.0, 8.0], [7.0, 7.0], [6.0, 6.0]]]
            ),
            logits=torch.tensor([[[-5.0], [1.0], [0.5], [1.5], [99.0]]]),
        )


class SlowFakeTokenModel(FakeTokenModel):
    def __call__(self, **inputs: object) -> SimpleNamespace:
        time.sleep(0.2)
        return super().__call__(**inputs)


@pytest.mark.asyncio
async def test_gte_encoder_returns_dense_and_sparse_from_one_forward_pass() -> None:
    tokenizer = FakeTokenizer()
    model = FakeTokenModel()
    encoder = GteHybridEncoder(
        tokenizer=tokenizer,
        model=model,
        device="cpu",
    )

    batch = await encoder.encode(("政策文本",))

    assert batch.dense[0] == pytest.approx((0.6, 0.8))
    assert batch.sparse == (((101, 1.5), (202, 0.5)),)
    assert model.forward_calls == 1
    assert model.eval_calls == 1
    assert model.to_calls == [("cpu", torch.float32)]
    assert encoder.device == "cpu"
    assert encoder.dtype == "float32"
    assert tokenizer.calls == [
        (
            ("政策文本",),
            {
                "padding": True,
                "truncation": True,
                "return_tensors": "pt",
            },
        )
    ]


@pytest.mark.asyncio
async def test_gte_encoder_does_not_block_the_event_loop() -> None:
    encoder = GteHybridEncoder(
        tokenizer=FakeTokenizer(),
        model=SlowFakeTokenModel(),
        device="cpu",
    )

    encoding = asyncio.create_task(encoder.encode(("policy",)))
    await asyncio.sleep(0.02)

    assert not encoding.done()
    await encoding


def test_gte_encoder_loads_only_pinned_local_artifacts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    tokenizer = FakeTokenizer()
    model = FakeTokenModel()
    tokenizer_call: dict[str, object] = {}
    model_call: dict[str, object] = {}

    def load_tokenizer(model_id: str, **kwargs: object) -> FakeTokenizer:
        warnings.warn(
            "`resume_download` is deprecated and will be removed",
            FutureWarning,
            stacklevel=1,
        )
        tokenizer_call.update({"model_id": model_id, **kwargs})
        return tokenizer

    def load_model(model_id: str, **kwargs: object) -> FakeTokenModel:
        model_call.update({"model_id": model_id, **kwargs})
        return model

    auto_tokenizer = SimpleNamespace(from_pretrained=load_tokenizer)
    auto_model = SimpleNamespace(from_pretrained=load_model)
    modules_cache = tmp_path / "modules"
    modules_cache.mkdir()
    monkeypatch.setattr(
        retrieval_module,
        "_prepare_audited_module_cache",
        lambda _cache_dir: modules_cache,
    )
    runtime_calls: list[Path] = []

    def import_runtime(path: Path) -> tuple[object, object]:
        runtime_calls.append(path)
        os.environ["HF_MODULES_CACHE"] = str(path.resolve())
        return auto_tokenizer, auto_model

    monkeypatch.setattr(
        retrieval_module,
        "_import_transformers_runtime",
        import_runtime,
    )
    monkeypatch.setattr(
        retrieval_module,
        "_verify_materialized_remote_code",
        lambda _modules_cache: None,
    )
    monkeypatch.delenv("HF_MODULES_CACHE", raising=False)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        encoder = GteHybridEncoder.from_config(AppConfig(), device="cpu")

    shared_expected = {
        "model_id": EMBEDDING_MODEL_ID,
        "revision": EMBEDDING_MODEL_REVISION,
        "code_revision": RERANKER_IMPLEMENTATION_REVISION,
        "trust_remote_code": True,
        "local_files_only": True,
        "cache_dir": AppConfig().model_cache_dir.resolve(),
    }
    assert tokenizer_call == shared_expected
    assert model_call == {**shared_expected, "torch_dtype": torch.float32}
    assert encoder.backend_kind == "real"
    assert encoder.model_id == EMBEDDING_MODEL_ID
    assert encoder.model_revision == EMBEDDING_MODEL_REVISION
    assert encoder.code_revision == RERANKER_IMPLEMENTATION_REVISION
    assert caught == []
    assert runtime_calls == [modules_cache]
    assert os.environ["HF_MODULES_CACHE"] == str(modules_cache.resolve())


def test_audited_module_cache_rejects_tampered_materialized_code(
    tmp_path: Path,
) -> None:
    cache_dir = tmp_path / "model-cache"
    source_dir = (
        cache_dir
        / "models--Alibaba-NLP--new-impl"
        / "snapshots"
        / RERANKER_IMPLEMENTATION_REVISION
    )
    source_dir.mkdir(parents=True)
    safe_files = {
        "configuration.py": b"SAFE_CONFIGURATION\n",
        "modeling.py": b"SAFE_MODELING\n",
    }
    expected = {
        name: (len(content), hashlib.sha256(content).hexdigest())
        for name, content in safe_files.items()
    }
    for name, content in safe_files.items():
        (source_dir / name).write_bytes(content)

    modules_cache = _prepare_audited_module_cache(
        cache_dir,
        expected_files=expected,
    )
    materialized = (
        modules_cache
        / "transformers_modules"
        / "Alibaba-NLP"
        / "new-impl"
        / RERANKER_IMPLEMENTATION_REVISION
    )
    materialized.mkdir(parents=True)
    (materialized / "modeling.py").write_bytes(b"FAKE_MODELING\n")

    with pytest.raises(RetrievalUnavailableError, match="digest"):
        _prepare_audited_module_cache(
            cache_dir,
            expected_files=expected,
        )


def test_audited_module_cache_rejects_tampered_source(tmp_path: Path) -> None:
    cache_dir = tmp_path / "model-cache"
    source_dir = (
        cache_dir
        / "models--Alibaba-NLP--new-impl"
        / "snapshots"
        / RERANKER_IMPLEMENTATION_REVISION
    )
    source_dir.mkdir(parents=True)
    safe_files = {
        "configuration.py": b"SAFE_CONFIGURATION\n",
        "modeling.py": b"SAFE_MODELING\n",
    }
    expected = {
        name: (len(content), hashlib.sha256(content).hexdigest())
        for name, content in safe_files.items()
    }
    (source_dir / "configuration.py").write_bytes(safe_files["configuration.py"])
    (source_dir / "modeling.py").write_bytes(b"FAKE_MODELING\n")

    with pytest.raises(RetrievalUnavailableError, match=r"digest.*modeling.py"):
        _prepare_audited_module_cache(
            cache_dir,
            expected_files=expected,
        )


def test_transformers_module_cache_is_project_scoped_in_fresh_process(
    tmp_path: Path,
) -> None:
    modules_cache = tmp_path / "modules"
    modules_cache.mkdir()
    script = """
import json
import os
import sys
from pathlib import Path

from enterprise_rag.retrieval import _import_transformers_runtime

cache = Path(sys.argv[1])
_import_transformers_runtime(cache)
from transformers import dynamic_module_utils
from transformers.utils import hub

print(json.dumps({
    "environment": os.environ["HF_MODULES_CACHE"],
    "dynamic": dynamic_module_utils.HF_MODULES_CACHE,
    "hub": hub.HF_MODULES_CACHE,
}))
"""
    environment = os.environ.copy()
    environment.pop("HF_MODULES_CACHE", None)

    completed = subprocess.run(
        [sys.executable, "-c", script, str(modules_cache)],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    payload = json.loads(completed.stdout)
    expected = str(modules_cache.resolve())

    assert payload == {
        "environment": expected,
        "dynamic": expected,
        "hub": expected,
    }


@pytest.mark.asyncio
async def test_gte_encoder_rejects_empty_or_blank_batches() -> None:
    encoder = GteHybridEncoder(
        tokenizer=FakeTokenizer(),
        model=FakeTokenModel(),
        device="cpu",
    )

    with pytest.raises(ValueError, match="texts"):
        await encoder.encode(())
    with pytest.raises(ValueError, match="texts"):
        await encoder.encode((" ",))
