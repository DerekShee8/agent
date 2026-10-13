import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path


# Loaded-document results preserve immutable text and relative source identity.
@dataclass(frozen=True, slots=True)
class LoadedDocument:
    text: str
    filename: str


# Chunk results preserve immutable text, source, and stable chunk identity.
@dataclass(frozen=True, slots=True)
class DocumentChunk:
    text: str
    filename: str
    chunk_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise TypeError("chunk text must be a string")
        if not self.text.strip():
            raise ValueError("chunk text must not be empty")
        if not isinstance(self.filename, str):
            raise TypeError("filename must be a string")
        if (
            not self.filename
            or self.filename.startswith("/")
            or re.match(r"^[A-Za-z]:", self.filename) is not None
        ):
            raise ValueError("filename must be a relative POSIX path")
        if "\\" in self.filename:
            raise ValueError("filename must use POSIX separators")
        filename_parts = self.filename.split("/")
        if any(part in {"", ".", ".."} for part in filename_parts):
            raise ValueError("filename must not contain traversal segments")
        if not isinstance(self.chunk_id, str):
            raise TypeError("chunk_id must be a string")
        expected_prefix = f"{self.filename}::"
        if not self.chunk_id.startswith(expected_prefix):
            raise ValueError("chunk_id must preserve the filename identity")
        suffix = self.chunk_id[len(expected_prefix) :]
        if re.fullmatch(r"[0-9]{4}", suffix) is None:
            raise ValueError("chunk_id must end with a four-digit chunk number")


def corpus_digest(chunks: tuple[DocumentChunk, ...]) -> str:
    """Return a stable SHA-256 over ordered chunk content and provenance."""

    if not isinstance(chunks, tuple):
        raise TypeError("chunks must be a tuple")
    payload: list[dict[str, str]] = []
    for chunk in chunks:
        if not isinstance(chunk, DocumentChunk):
            raise TypeError("chunks must contain DocumentChunk values")
        payload.append(
            {
                "chunk_id": chunk.chunk_id,
                "filename": chunk.filename,
                "text": chunk.text,
            }
        )
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


# Text cleaning collapses whitespace within paragraphs and retains paragraphs.
_PARAGRAPH_SEPARATOR = "\n\n"


def _normalize_text(text: str) -> str:
    paragraphs: list[str] = []
    paragraph_lines: list[str] = []

    for line in text.splitlines():
        normalized_line = " ".join(line.split())

        if normalized_line:
            paragraph_lines.append(normalized_line)
            continue

        if paragraph_lines:
            paragraphs.append(" ".join(paragraph_lines))
            paragraph_lines.clear()

    if paragraph_lines:
        paragraphs.append(" ".join(paragraph_lines))

    return _PARAGRAPH_SEPARATOR.join(paragraphs)


# Document discovery recursively loads approved UTF-8 formats in stable order.
_SUPPORTED_SUFFIXES = frozenset({".md", ".txt"})


# Path boundaries validate the root and contain every discovered entry.
def _discover_supported_paths(root: Path) -> tuple[Path, ...]:
    if not root.is_dir():
        raise ValueError(f"root must be an existing directory: {root}")

    resolved_root = root.resolve()
    supported_paths: list[Path] = []

    for path in root.rglob("*"):
        filename = path.relative_to(root).as_posix()
        resolved_path = path.resolve()

        if not resolved_path.is_relative_to(resolved_root):
            raise ValueError(f"document path escapes root: {filename}")

        if path.is_file() and path.suffix.casefold() in _SUPPORTED_SUFFIXES:
            supported_paths.append(path)

    if not supported_paths:
        raise ValueError(f"no supported documents found under root: {root}")

    return tuple(
        sorted(
            supported_paths,
            key=lambda path: path.relative_to(root).as_posix(),
        )
    )


# UTF-8 loading reports source context while retaining decoder diagnostics.
def _read_utf8_text(path: Path, filename: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(f"invalid UTF-8 document: {filename}") from error


# Document loading composes validated discovery, decoding, and normalization.
def load_documents(root: Path) -> tuple[LoadedDocument, ...]:
    supported_paths = _discover_supported_paths(root)
    documents: list[LoadedDocument] = []

    for path in supported_paths:
        filename = path.relative_to(root).as_posix()
        normalized_text = _normalize_text(_read_utf8_text(path, filename))

        if not normalized_text:
            raise ValueError(f"document is empty after normalization: {filename}")

        documents.append(
            LoadedDocument(
                text=normalized_text,
                filename=filename,
            )
        )

    return tuple(documents)


# Chunk-window rules guarantee progress and valid character overlap.
def _validate_chunk_window(size: int, overlap: int) -> None:
    if size <= 0:
        raise ValueError("size must be positive")
    if overlap < 0:
        raise ValueError("overlap must be non-negative")
    if overlap >= size:
        raise ValueError("overlap must be smaller than size")


# Character chunking prefers paragraph boundaries before hard-size cuts.
def _split_document_text(
    text: str,
    size: int,
    overlap: int,
) -> tuple[str, ...]:
    chunk_texts: list[str] = []
    start = 0

    while start < len(text):
        maximum_end = min(start + size, len(text))
        end = maximum_end
        next_start: int | None = None

        if maximum_end < len(text):
            paragraph_end = text.rfind(
                _PARAGRAPH_SEPARATOR,
                start,
                maximum_end,
            )
            if paragraph_end > start:
                end = paragraph_end
                next_start = paragraph_end + len(_PARAGRAPH_SEPARATOR)

        chunk_text = text[start:end].strip()
        if chunk_text:
            chunk_texts.append(chunk_text)

        if end >= len(text):
            break

        if next_start is not None:
            start = next_start
        else:
            start = end - overlap

    return tuple(chunk_texts)


# Chunk assembly preserves document order and provenance in stable IDs.
def chunk_documents(
    documents: tuple[LoadedDocument, ...],
    size: int,
    overlap: int,
) -> tuple[DocumentChunk, ...]:
    _validate_chunk_window(size, overlap)
    chunks: list[DocumentChunk] = []

    for document in documents:
        chunk_texts = _split_document_text(
            document.text,
            size,
            overlap,
        )

        for index, chunk_text in enumerate(chunk_texts):
            chunks.append(
                DocumentChunk(
                    text=chunk_text,
                    filename=document.filename,
                    chunk_id=f"{document.filename}::{index:04d}",
                )
            )

    return tuple(chunks)
