import os
import subprocess
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest


# Document discovery loads approved text formats in deterministic source order.
def test_load_documents_orders_supported_files_and_excludes_unsupported(
    tmp_path: Path,
) -> None:
    from enterprise_rag.ingestion import load_documents

    (tmp_path / "b.txt").write_text("第二份制度", encoding="utf-8")
    (tmp_path / "a.md").write_text("第一份制度", encoding="utf-8")
    (tmp_path / "ignored.pdf").write_bytes(b"%PDF-example")

    documents = load_documents(tmp_path)

    assert [(document.filename, document.text) for document in documents] == [
        ("a.md", "第一份制度"),
        ("b.txt", "第二份制度"),
    ]


def test_load_documents_recurses_case_insensitively_and_preserves_relative_sources(
    tmp_path: Path,
) -> None:
    from enterprise_rag.ingestion import load_documents

    nested = tmp_path / "nested"
    nested.mkdir()

    (tmp_path / "a.MD").write_text("根目录制度", encoding="utf-8")
    (nested / "b.TXT").write_text("子目录制度", encoding="utf-8")
    (nested / "ignored.csv").write_text("不支持", encoding="utf-8")

    documents = load_documents(tmp_path)

    assert [(document.filename, document.text) for document in documents] == [
        ("a.MD", "根目录制度"),
        ("nested/b.TXT", "子目录制度"),
    ]


# Loaded documents expose an immutable, fixed-shape discovery result contract.
def test_loaded_document_is_immutable_and_slotted() -> None:
    from enterprise_rag.ingestion import LoadedDocument

    document = LoadedDocument(
        text="差旅制度",
        filename="policy.md",
    )

    assert not hasattr(document, "__dict__")

    with pytest.raises(FrozenInstanceError):
        setattr(document, "text", "已改写")  # noqa: B010


# Text cleaning normalizes intra-paragraph whitespace and retains paragraphs.
def test_load_documents_normalizes_whitespace_and_preserves_paragraphs(
    tmp_path: Path,
) -> None:
    from enterprise_rag.ingestion import load_documents

    (tmp_path / "policy.md").write_text(
        "  第一条   规则。\r\n继续\t说明。 \r\n \r\n 第二段\t规则。  \n",
        encoding="utf-8",
        newline="",
    )

    documents = load_documents(tmp_path)

    assert documents[0].text == ("第一条 规则。 继续 说明。\n\n第二段 规则。")


def test_load_documents_rejects_document_empty_after_normalization(
    tmp_path: Path,
) -> None:
    from enterprise_rag.ingestion import load_documents

    (tmp_path / "empty.md").write_text(
        " \n\t\n\n ",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="empty after normalization"):
        load_documents(tmp_path)


# Path and loading boundaries reject unusable or untrusted document inputs.
def test_load_documents_rejects_missing_root(tmp_path: Path) -> None:
    from enterprise_rag.ingestion import load_documents

    missing_root = tmp_path / "missing"

    with pytest.raises(
        ValueError,
        match="root must be an existing directory",
    ):
        load_documents(missing_root)


def test_load_documents_rejects_file_root(tmp_path: Path) -> None:
    from enterprise_rag.ingestion import load_documents

    file_root = tmp_path / "not-a-directory"
    file_root.write_text("这不是目录", encoding="utf-8")

    with pytest.raises(
        ValueError,
        match="root must be an existing directory",
    ):
        load_documents(file_root)


# An empty knowledge root cannot provide indexable documents.
def test_load_documents_rejects_empty_directory(tmp_path: Path) -> None:
    from enterprise_rag.ingestion import load_documents

    with pytest.raises(ValueError, match="no supported documents"):
        load_documents(tmp_path)


def test_load_documents_rejects_when_no_supported_documents(
    tmp_path: Path,
) -> None:
    from enterprise_rag.ingestion import load_documents

    (tmp_path / "ignored.pdf").write_bytes(b"%PDF-example")

    with pytest.raises(ValueError, match="no supported documents"):
        load_documents(tmp_path)


def test_load_documents_rejects_symlink_escape(
    tmp_path: Path,
) -> None:
    from enterprise_rag.ingestion import load_documents

    outside_directory = tmp_path.parent / f"{tmp_path.name}-outside"
    outside_directory.mkdir()
    (outside_directory / "secret.txt").write_text(
        "根目录外的内容",
        encoding="utf-8",
    )

    escaped_directory = tmp_path / "escape"

    if os.name == "nt":
        result = subprocess.run(
            [
                "cmd",
                "/c",
                "mklink",
                "/J",
                str(escaped_directory),
                str(outside_directory),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            pytest.fail(
                "failed to create Windows directory junction: "
                f"{result.stderr or result.stdout}"
            )
    else:
        escaped_directory.symlink_to(
            outside_directory,
            target_is_directory=True,
        )

    with pytest.raises(
        ValueError,
        match="document path escapes root: escape",
    ):
        load_documents(tmp_path)


def test_load_documents_reports_invalid_utf8_with_source(
    tmp_path: Path,
) -> None:
    from enterprise_rag.ingestion import load_documents

    (tmp_path / "broken.txt").write_bytes(b"\xff\xfe")

    with pytest.raises(
        ValueError,
        match=r"invalid UTF-8 document: broken\.txt",
    ) as error:
        load_documents(tmp_path)

    assert isinstance(error.value.__cause__, UnicodeDecodeError)


# Chunk results preserve an immutable shape and reject unusable text.
def test_document_chunk_is_immutable_and_slotted() -> None:
    from enterprise_rag.ingestion import DocumentChunk

    chunk = DocumentChunk(
        text="差旅制度",
        filename="policy.md",
        chunk_id="policy.md::0000",
    )

    assert not hasattr(chunk, "__dict__")

    with pytest.raises(FrozenInstanceError):
        setattr(chunk, "text", "已改写")  # noqa: B010


def test_document_chunk_rejects_blank_text() -> None:
    from enterprise_rag.ingestion import DocumentChunk

    with pytest.raises(ValueError, match="chunk text must not be empty"):
        DocumentChunk(
            text=" \n\n ",
            filename="policy.md",
            chunk_id="policy.md::0000",
        )


# Character chunking preserves provenance, stable IDs, overlap, and paragraphs.
def test_chunk_documents_keeps_provenance_stable_ids_and_overlap() -> None:
    from enterprise_rag.ingestion import LoadedDocument, chunk_documents

    documents = (
        LoadedDocument(
            text="abcdefghij",
            filename="one/policy.md",
        ),
        LoadedDocument(
            text="xyz",
            filename="two/policy.md",
        ),
    )

    chunks = chunk_documents(documents, size=6, overlap=2)

    assert [(chunk.text, chunk.filename, chunk.chunk_id) for chunk in chunks] == [
        ("abcdef", "one/policy.md", "one/policy.md::0000"),
        ("efghij", "one/policy.md", "one/policy.md::0001"),
        ("xyz", "two/policy.md", "two/policy.md::0000"),
    ]


def test_chunk_documents_prefers_paragraph_boundary_within_size() -> None:
    from enterprise_rag.ingestion import LoadedDocument, chunk_documents

    document = LoadedDocument(
        text="第一段。\n\n第二段内容。",
        filename="policy.md",
    )

    chunks = chunk_documents((document,), size=8, overlap=0)

    assert [chunk.text for chunk in chunks] == [
        "第一段。",
        "第二段内容。",
    ]
    assert all(len(chunk.text) <= 8 for chunk in chunks)


@pytest.mark.parametrize(
    ("size", "overlap", "message"),
    [
        pytest.param(0, 0, "size must be positive", id="zero-size"),
        pytest.param(-1, 0, "size must be positive", id="negative-size"),
        pytest.param(
            4,
            -1,
            "overlap must be non-negative",
            id="negative-overlap",
        ),
        pytest.param(
            4,
            4,
            "overlap must be smaller than size",
            id="overlap-equals-size",
        ),
        pytest.param(
            4,
            5,
            "overlap must be smaller than size",
            id="overlap-exceeds-size",
        ),
    ],
)
def test_chunk_documents_rejects_invalid_configuration(
    size: int,
    overlap: int,
    message: str,
) -> None:
    from enterprise_rag.ingestion import LoadedDocument, chunk_documents

    document = LoadedDocument(
        text="有效内容",
        filename="policy.md",
    )

    with pytest.raises(ValueError, match=message):
        chunk_documents(
            (document,),
            size=size,
            overlap=overlap,
        )
