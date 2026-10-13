from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from enterprise_rag.errors import IndexUnavailableError
from enterprise_rag.index_store import (
    HybridIndex,
    IndexManifest,
    load_index,
    save_index,
)
from enterprise_rag.ingestion import DocumentChunk, corpus_digest
from enterprise_rag.ports import EncodedBatch


def make_index() -> HybridIndex:
    chunks = (
        DocumentChunk("P4 酒店上限为 CNY 600。", "policy.md", "policy.md::0000"),
        DocumentChunk("超额需要事前审批。", "policy.md", "policy.md::0001"),
    )
    manifest = IndexManifest(
        schema_version=1,
        backend_kind="fake",
        source_digest=corpus_digest(chunks),
        chunk_size=384,
        chunk_overlap=48,
        model_id="fake-hybrid-encoder",
        model_revision="fake-revision",
        code_revision="fake-code-revision",
        dimension=2,
        row_count=2,
        built_at="2026-10-09T00:00:00Z",
    )
    return HybridIndex(
        manifest=manifest,
        chunks=chunks,
        dense=((0.6, 0.8), (0.0, 1.0)),
        sparse=(((101, 1.25),), ((101, 0.5), (202, 2.0))),
    )


def test_index_round_trip_preserves_manifest_chunks_dense_and_sparse(
    tmp_path: Path,
) -> None:
    index = make_index()
    destination = tmp_path / "hybrid-index"

    save_index(index, destination)

    assert load_index(destination) == index
    assert {path.name for path in destination.iterdir()} == {
        "manifest.json",
        "chunks.jsonl",
        "dense.npy",
        "sparse.jsonl",
    }
    assert (destination / "dense.npy").read_bytes().startswith(b"\x93NUMPY")


def test_manifest_and_encoded_batch_are_immutable_and_validate_shape() -> None:
    index = make_index()
    batch = EncodedBatch(dense=index.dense, sparse=index.sparse)

    assert batch.dense == index.dense
    with pytest.raises((AttributeError, TypeError)):
        batch.dense = ()  # type: ignore[misc]
    with pytest.raises(ValueError, match="schema_version"):
        replace(index.manifest, schema_version=0)
    with pytest.raises(ValueError, match="source_digest"):
        replace(index.manifest, source_digest="not-a-digest")
    with pytest.raises(ValueError, match="chunk"):
        replace(index.manifest, chunk_overlap=384)
    with pytest.raises(ValueError, match="built_at"):
        replace(index.manifest, built_at="not-a-timestamp")


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("chunks", ()),
        ("dense", ((0.6, 0.8),)),
        ("sparse", (((101, 1.25),),)),
    ],
)
def test_index_rejects_mismatched_row_counts(
    field_name: str, value: object
) -> None:
    index = make_index()

    with pytest.raises(ValueError, match="row count"):
        HybridIndex(
            manifest=index.manifest,
            chunks=value if field_name == "chunks" else index.chunks,  # type: ignore[arg-type]
            dense=value if field_name == "dense" else index.dense,  # type: ignore[arg-type]
            sparse=value if field_name == "sparse" else index.sparse,  # type: ignore[arg-type]
        )


def test_index_rejects_duplicate_chunk_ids_and_digest_mismatch() -> None:
    index = make_index()
    duplicate_chunks = (index.chunks[0], index.chunks[0])

    with pytest.raises(ValueError, match="duplicate chunk_id"):
        HybridIndex(
            manifest=replace(
                index.manifest,
                source_digest=corpus_digest(duplicate_chunks),
            ),
            chunks=duplicate_chunks,
            dense=index.dense,
            sparse=index.sparse,
        )
    with pytest.raises(ValueError, match="source_digest"):
        HybridIndex(
            manifest=replace(index.manifest, source_digest="0" * 64),
            chunks=index.chunks,
            dense=index.dense,
            sparse=index.sparse,
        )


@pytest.mark.parametrize(
    ("dense", "sparse", "message"),
    [
        (((float("nan"), 0.0), (0.0, 1.0)), None, "dense"),
        (((0.0,), (0.0, 1.0)), None, "dimension"),
        (None, (((101, float("inf")),), ((202, 1.0),)), "sparse"),
        (None, (((101, 1.0), (101, 2.0)), ((202, 1.0),)), "token"),
    ],
)
def test_index_rejects_invalid_dense_and_sparse_values(
    dense: object, sparse: object, message: str
) -> None:
    index = make_index()

    with pytest.raises((TypeError, ValueError), match=message):
        HybridIndex(
            manifest=index.manifest,
            chunks=index.chunks,
            dense=index.dense if dense is None else dense,  # type: ignore[arg-type]
            sparse=index.sparse if sparse is None else sparse,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("filename", "chunk_id"),
    [
        ("../secret.md", "../secret.md::0000"),
        ("/absolute.md", "/absolute.md::0000"),
        ("C:/absolute.md", "C:/absolute.md::0000"),
        ("folder\\escape.md", "folder\\escape.md::0000"),
        ("policy.md", "other.md::0000"),
        ("policy.md", "policy.md::../../0000"),
    ],
)
def test_document_chunk_rejects_path_traversal_and_identity_mismatch(
    filename: str, chunk_id: str
) -> None:
    with pytest.raises(ValueError, match=r"filename|chunk_id"):
        DocumentChunk("text", filename, chunk_id)


def test_load_rejects_unknown_schema_and_artifact_corruption(tmp_path: Path) -> None:
    destination = tmp_path / "index"
    save_index(make_index(), destination)
    manifest_path = destination / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["schema_version"] = 999
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(IndexUnavailableError, match="schema"):
        load_index(destination)

    save_index(make_index(), destination)
    with (destination / "chunks.jsonl").open("a", encoding="utf-8") as stream:
        stream.write("{}\n")
    with pytest.raises(IndexUnavailableError, match="digest"):
        load_index(destination)


def test_load_rejects_partial_or_unexpected_index_directory(tmp_path: Path) -> None:
    partial = tmp_path / "partial"
    partial.mkdir()
    (partial / "manifest.json").write_text("{}", encoding="utf-8")

    with pytest.raises(IndexUnavailableError, match="files"):
        load_index(partial)

    destination = tmp_path / "index"
    save_index(make_index(), destination)
    (destination / "unexpected.txt").write_text("surprise", encoding="utf-8")
    with pytest.raises(IndexUnavailableError, match="files"):
        load_index(destination)


def test_failed_save_leaves_no_partial_target_or_temp_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import enterprise_rag.index_store as index_store

    destination = tmp_path / "index"

    def fail_dense_write(*_args: object, **_kwargs: object) -> None:
        raise OSError("injected write failure")

    monkeypatch.setattr(index_store, "_write_dense_file", fail_dense_write)
    with pytest.raises(OSError, match="injected"):
        save_index(make_index(), destination)

    assert not destination.exists()
    assert not tuple(tmp_path.glob(".index.tmp-*"))


def test_failed_replacement_preserves_previous_complete_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import enterprise_rag.index_store as index_store

    destination = tmp_path / "index"
    original = make_index()
    save_index(original, destination)

    def fail_install(*_args: object, **_kwargs: object) -> None:
        raise OSError("injected install failure")

    monkeypatch.setattr(index_store, "_install_directory", fail_install)
    with pytest.raises(OSError, match="injected"):
        save_index(original, destination)

    assert load_index(destination) == original
    assert not tuple(tmp_path.glob(".index.tmp-*"))


def test_corpus_digest_is_stable_and_content_sensitive() -> None:
    index = make_index()
    same_chunks = tuple(
        DocumentChunk(chunk.text, chunk.filename, chunk.chunk_id)
        for chunk in index.chunks
    )
    changed = (
        DocumentChunk("changed", "policy.md", "policy.md::0000"),
        index.chunks[1],
    )

    assert corpus_digest(same_chunks) == index.manifest.source_digest
    assert corpus_digest(changed) != index.manifest.source_digest

