from __future__ import annotations

from pathlib import Path

import pytest

from enterprise_rag.index_store import build_index, load_index
from enterprise_rag.ports import EncodedBatch, HybridEncoderPort


class FakeHybridEncoder:
    backend_kind = "fake"
    model_id = "deterministic-fake-encoder"
    model_revision = "fake-model-revision"
    code_revision = "fake-code-revision"

    async def encode(self, texts: tuple[str, ...]) -> EncodedBatch:
        dense = tuple(
            (float(index + 1), float(len(text)))
            for index, text in enumerate(texts)
        )
        sparse = tuple(
            ((101, float(index + 1)), (202, float(len(text))))
            for index, text in enumerate(texts)
        )
        return EncodedBatch(dense=dense, sparse=sparse)


class WrongRowCountEncoder(FakeHybridEncoder):
    async def encode(self, texts: tuple[str, ...]) -> EncodedBatch:
        batch = await super().encode(texts)
        return EncodedBatch(dense=batch.dense[:-1], sparse=batch.sparse)


@pytest.mark.asyncio
async def test_fake_build_round_trip_is_explicitly_labelled_and_deterministic(
    tmp_path: Path,
) -> None:
    encoder = FakeHybridEncoder()
    index_dir = tmp_path / "index"

    manifest = await build_index(
        Path("data/sample_docs"),
        index_dir,
        encoder,
        chunk_size=384,
        chunk_overlap=48,
        built_at="2026-10-09T00:00:00Z",
    )
    loaded = load_index(index_dir)

    assert isinstance(encoder, HybridEncoderPort)
    assert manifest == loaded.manifest
    assert manifest.backend_kind == "fake"
    assert manifest.model_id == "deterministic-fake-encoder"
    assert manifest.row_count == len(loaded.chunks) > 3
    assert manifest.dimension == 2
    assert all(
        chunk.chunk_id.startswith(f"{chunk.filename}::")
        for chunk in loaded.chunks
    )


@pytest.mark.asyncio
async def test_fake_build_rejects_encoder_row_mismatch(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="row count"):
        await build_index(
            Path("data/sample_docs"),
            tmp_path / "index",
            WrongRowCountEncoder(),
            chunk_size=384,
            chunk_overlap=48,
            built_at="2026-10-09T00:00:00Z",
        )

    assert not (tmp_path / "index").exists()
