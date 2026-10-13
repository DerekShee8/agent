from __future__ import annotations

import ast
import hashlib
import json
import math
import os
import re
import shutil
import struct
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import uuid4

from .errors import IndexUnavailableError
from .ingestion import (
    DocumentChunk,
    chunk_documents,
    corpus_digest,
    load_documents,
)
from .ports import EncodedBatch, HybridEncoderPort

_SCHEMA_VERSION = 1
_INDEX_FILENAMES = frozenset(
    {"manifest.json", "chunks.jsonl", "dense.npy", "sparse.jsonl"}
)
_ARTIFACT_FILENAMES = ("chunks.jsonl", "dense.npy", "sparse.jsonl")
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class IndexManifest:
    schema_version: int
    backend_kind: str
    source_digest: str
    chunk_size: int
    chunk_overlap: int
    model_id: str
    model_revision: str
    code_revision: str
    dimension: int
    row_count: int
    built_at: str

    def __post_init__(self) -> None:
        if isinstance(self.schema_version, bool) or self.schema_version != 1:
            raise ValueError("unsupported schema_version")
        _require_nonblank("backend_kind", self.backend_kind)
        if (
            not isinstance(self.source_digest, str)
            or _SHA256_PATTERN.fullmatch(self.source_digest) is None
        ):
            raise ValueError("source_digest must be a lowercase SHA-256 digest")
        if isinstance(self.chunk_size, bool) or not isinstance(self.chunk_size, int):
            raise TypeError("chunk_size must be an integer")
        if self.chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        if (
            isinstance(self.chunk_overlap, bool)
            or not isinstance(self.chunk_overlap, int)
        ):
            raise TypeError("chunk_overlap must be an integer")
        if self.chunk_overlap < 0 or self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap must satisfy 0 <= overlap < chunk_size")
        _require_nonblank("model_id", self.model_id)
        _require_nonblank("model_revision", self.model_revision)
        _require_nonblank("code_revision", self.code_revision)
        if isinstance(self.dimension, bool) or not isinstance(self.dimension, int):
            raise TypeError("dimension must be an integer")
        if self.dimension <= 0:
            raise ValueError("dimension must be positive")
        if isinstance(self.row_count, bool) or not isinstance(self.row_count, int):
            raise TypeError("row_count must be an integer")
        if self.row_count <= 0:
            raise ValueError("row_count must be positive")
        _validate_utc_timestamp(self.built_at)


@dataclass(frozen=True, slots=True)
class HybridIndex:
    manifest: IndexManifest
    chunks: tuple[DocumentChunk, ...]
    dense: tuple[tuple[float, ...], ...]
    sparse: tuple[tuple[tuple[int, float], ...], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.manifest, IndexManifest):
            raise TypeError("manifest must be an IndexManifest")
        if not isinstance(self.chunks, tuple):
            raise TypeError("chunks must be a tuple")
        if any(not isinstance(chunk, DocumentChunk) for chunk in self.chunks):
            raise TypeError("chunks must contain DocumentChunk values")

        row_count = self.manifest.row_count
        if not (
            len(self.chunks)
            == len(self.dense)
            == len(self.sparse)
            == row_count
        ):
            raise ValueError("index row counts must match the manifest row count")

        chunk_ids = tuple(chunk.chunk_id for chunk in self.chunks)
        if len(set(chunk_ids)) != len(chunk_ids):
            raise ValueError("index contains a duplicate chunk_id")
        if corpus_digest(self.chunks) != self.manifest.source_digest:
            raise ValueError("source_digest does not match index chunks")

        batch = EncodedBatch(dense=self.dense, sparse=self.sparse)
        if not batch.dense or len(batch.dense[0]) != self.manifest.dimension:
            raise ValueError("dense dimension does not match the manifest dimension")


def save_index(index: HybridIndex, destination: Path) -> None:
    """Persist a complete index through a validated temporary sibling."""

    if not isinstance(index, HybridIndex):
        raise TypeError("index must be a HybridIndex")
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and not destination.is_dir():
        raise ValueError("index destination must be a directory")

    temporary = Path(
        tempfile.mkdtemp(
            prefix=f".{destination.name}.tmp-",
            dir=destination.parent,
        )
    )
    try:
        _write_chunks_file(temporary / "chunks.jsonl", index.chunks)
        _write_dense_file(temporary / "dense.npy", index.dense)
        _write_sparse_file(temporary / "sparse.jsonl", index.sparse)
        artifact_digests = {
            filename: _file_digest(temporary / filename)
            for filename in _ARTIFACT_FILENAMES
        }
        manifest_payload = _manifest_to_payload(index.manifest)
        manifest_payload["artifact_digests"] = artifact_digests
        _write_text_file(
            temporary / "manifest.json",
            _canonical_json(manifest_payload) + "\n",
        )
        _install_directory(temporary, destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def load_index(source: Path) -> HybridIndex:
    """Load and validate all index artifacts before exposing an index."""

    source = Path(source)
    try:
        if not source.is_dir():
            raise IndexUnavailableError("index directory is unavailable")
        actual_files = {entry.name for entry in source.iterdir() if entry.is_file()}
        actual_entries = {entry.name for entry in source.iterdir()}
        if actual_files != _INDEX_FILENAMES or actual_entries != _INDEX_FILENAMES:
            raise IndexUnavailableError("index files are incomplete or unexpected")

        manifest_payload = json.loads(
            (source / "manifest.json").read_text(encoding="utf-8")
        )
        if not isinstance(manifest_payload, dict):
            raise ValueError("manifest must be a JSON object")
        artifact_digests = manifest_payload.pop("artifact_digests", None)
        manifest = _manifest_from_payload(manifest_payload)
        _verify_artifact_digests(source, artifact_digests)

        chunks = _read_chunks_file(source / "chunks.jsonl")
        dense = _read_dense_file(source / "dense.npy")
        sparse = _read_sparse_file(source / "sparse.jsonl")
        return HybridIndex(
            manifest=manifest,
            chunks=chunks,
            dense=dense,
            sparse=sparse,
        )
    except IndexUnavailableError:
        raise
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
        SyntaxError,
        struct.error,
    ) as error:
        raise IndexUnavailableError(f"invalid index: {error}") from error


async def build_index(
    document_root: Path,
    destination: Path,
    encoder: HybridEncoderPort,
    *,
    chunk_size: int,
    chunk_overlap: int,
    built_at: str | None = None,
) -> IndexManifest:
    """Build a validated local index without selecting a concrete model backend."""

    documents = load_documents(Path(document_root))
    chunks = chunk_documents(documents, chunk_size, chunk_overlap)
    encoded = await encoder.encode(tuple(chunk.text for chunk in chunks))
    if not isinstance(encoded, EncodedBatch):
        raise TypeError("encoder must return EncodedBatch")
    if len(encoded.dense) != len(chunks) or len(encoded.sparse) != len(chunks):
        raise ValueError("encoded row counts must match the chunk row count")
    if not encoded.dense:
        raise ValueError("encoded row count must be positive")

    timestamp = built_at or datetime.now(UTC).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )
    manifest = IndexManifest(
        schema_version=_SCHEMA_VERSION,
        backend_kind=encoder.backend_kind,
        source_digest=corpus_digest(chunks),
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        model_id=encoder.model_id,
        model_revision=encoder.model_revision,
        code_revision=encoder.code_revision,
        dimension=len(encoded.dense[0]),
        row_count=len(chunks),
        built_at=timestamp,
    )
    save_index(
        HybridIndex(
            manifest=manifest,
            chunks=chunks,
            dense=encoded.dense,
            sparse=encoded.sparse,
        ),
        destination,
    )
    return manifest


def _require_nonblank(name: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


def _validate_utc_timestamp(value: object) -> None:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("built_at must be an ISO-8601 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as error:
        raise ValueError("built_at must be an ISO-8601 UTC timestamp") from error
    if parsed.tzinfo != UTC:
        raise ValueError("built_at must be an ISO-8601 UTC timestamp")


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _manifest_to_payload(manifest: IndexManifest) -> dict[str, object]:
    return {
        "backend_kind": manifest.backend_kind,
        "built_at": manifest.built_at,
        "chunk_overlap": manifest.chunk_overlap,
        "chunk_size": manifest.chunk_size,
        "code_revision": manifest.code_revision,
        "dimension": manifest.dimension,
        "model_id": manifest.model_id,
        "model_revision": manifest.model_revision,
        "row_count": manifest.row_count,
        "schema_version": manifest.schema_version,
        "source_digest": manifest.source_digest,
    }


def _manifest_from_payload(payload: dict[str, object]) -> IndexManifest:
    expected = {
        "backend_kind",
        "built_at",
        "chunk_overlap",
        "chunk_size",
        "code_revision",
        "dimension",
        "model_id",
        "model_revision",
        "row_count",
        "schema_version",
        "source_digest",
    }
    if set(payload) != expected:
        raise ValueError("manifest fields are incomplete or unexpected")
    return IndexManifest(
        schema_version=cast(int, payload["schema_version"]),
        backend_kind=cast(str, payload["backend_kind"]),
        source_digest=cast(str, payload["source_digest"]),
        chunk_size=cast(int, payload["chunk_size"]),
        chunk_overlap=cast(int, payload["chunk_overlap"]),
        model_id=cast(str, payload["model_id"]),
        model_revision=cast(str, payload["model_revision"]),
        code_revision=cast(str, payload["code_revision"]),
        dimension=cast(int, payload["dimension"]),
        row_count=cast(int, payload["row_count"]),
        built_at=cast(str, payload["built_at"]),
    )


def _write_text_file(path: Path, content: str) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def _write_bytes_file(path: Path, content: bytes) -> None:
    with path.open("wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def _write_chunks_file(path: Path, chunks: tuple[DocumentChunk, ...]) -> None:
    content = "".join(
        _canonical_json(
            {
                "chunk_id": chunk.chunk_id,
                "filename": chunk.filename,
                "text": chunk.text,
            }
        )
        + "\n"
        for chunk in chunks
    )
    _write_text_file(path, content)


def _write_sparse_file(
    path: Path,
    sparse: tuple[tuple[tuple[int, float], ...], ...],
) -> None:
    content = "".join(_canonical_json(row) + "\n" for row in sparse)
    _write_text_file(path, content)


def _write_dense_file(
    path: Path,
    dense: tuple[tuple[float, ...], ...],
) -> None:
    rows = len(dense)
    columns = len(dense[0]) if dense else 0
    header_text = (
        "{'descr': '<f8', 'fortran_order': False, "
        f"'shape': ({rows}, {columns}), }}"
    )
    header = header_text.encode("ascii")
    padding = (-(10 + len(header) + 1)) % 64
    padded_header = header + (b" " * padding) + b"\n"
    if len(padded_header) > 65_535:
        raise ValueError("dense NPY header is too large")
    prefix = b"\x93NUMPY\x01\x00" + struct.pack("<H", len(padded_header))
    values = tuple(float(value) for row in dense for value in row)
    body = struct.pack(f"<{len(values)}d", *values)
    _write_bytes_file(path, prefix + padded_header + body)


def _read_chunks_file(path: Path) -> tuple[DocumentChunk, ...]:
    chunks: list[DocumentChunk] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        payload = json.loads(line)
        if not isinstance(payload, dict) or set(payload) != {
            "chunk_id",
            "filename",
            "text",
        }:
            raise ValueError("chunk row fields are incomplete or unexpected")
        chunks.append(
            DocumentChunk(
                text=payload["text"],
                filename=payload["filename"],
                chunk_id=payload["chunk_id"],
            )
        )
    return tuple(chunks)


def _read_sparse_file(
    path: Path,
) -> tuple[tuple[tuple[int, float], ...], ...]:
    rows: list[tuple[tuple[int, float], ...]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        payload = json.loads(line)
        if not isinstance(payload, list):
            raise ValueError("sparse row must be a JSON array")
        row: list[tuple[int, float]] = []
        for pair in payload:
            if not isinstance(pair, list) or len(pair) != 2:
                raise ValueError("sparse entry must be a two-item array")
            token_id, weight = pair
            row.append((token_id, weight))
        rows.append(tuple(row))
    return tuple(rows)


def _read_dense_file(path: Path) -> tuple[tuple[float, ...], ...]:
    content = path.read_bytes()
    if len(content) < 10 or content[:8] != b"\x93NUMPY\x01\x00":
        raise ValueError("dense artifact is not NPY version 1.0")
    header_length = struct.unpack("<H", content[8:10])[0]
    header_end = 10 + header_length
    if header_end > len(content):
        raise ValueError("dense NPY header is truncated")
    header = ast.literal_eval(content[10:header_end].decode("ascii").strip())
    if not isinstance(header, dict) or set(header) != {
        "descr",
        "fortran_order",
        "shape",
    }:
        raise ValueError("dense NPY header fields are invalid")
    if header["descr"] != "<f8" or header["fortran_order"] is not False:
        raise ValueError("dense NPY encoding is unsupported")
    shape = header["shape"]
    if (
        not isinstance(shape, tuple)
        or len(shape) != 2
        or any(isinstance(value, bool) or not isinstance(value, int) for value in shape)
        or any(value <= 0 for value in shape)
    ):
        raise ValueError("dense NPY shape is invalid")
    rows, columns = shape
    value_count = rows * columns
    body = content[header_end:]
    expected_bytes = value_count * 8
    if len(body) != expected_bytes:
        raise ValueError("dense NPY payload size does not match its shape")
    values = struct.unpack(f"<{value_count}d", body)
    if any(not math.isfinite(value) for value in values):
        raise ValueError("dense NPY payload contains non-finite values")
    return tuple(
        tuple(values[index : index + columns])
        for index in range(0, value_count, columns)
    )


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _verify_artifact_digests(source: Path, payload: object) -> None:
    if not isinstance(payload, dict) or set(payload) != set(_ARTIFACT_FILENAMES):
        raise ValueError("artifact digest fields are incomplete or unexpected")
    for filename in _ARTIFACT_FILENAMES:
        expected = payload[filename]
        if (
            not isinstance(expected, str)
            or _SHA256_PATTERN.fullmatch(expected) is None
        ):
            raise ValueError("artifact digest must be a lowercase SHA-256 digest")
        if _file_digest(source / filename) != expected:
            raise IndexUnavailableError(f"artifact digest mismatch: {filename}")


def _install_directory(temporary: Path, destination: Path) -> None:
    if not destination.exists():
        os.replace(temporary, destination)
        return

    backup = destination.parent / f".{destination.name}.backup-{uuid4().hex}"
    os.replace(destination, backup)
    try:
        os.replace(temporary, destination)
    except BaseException:
        os.replace(backup, destination)
        raise
    else:
        shutil.rmtree(backup)
