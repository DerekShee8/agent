from __future__ import annotations

import hashlib
import importlib.util
import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from enterprise_rag.config import (
    EMBEDDING_MODEL_REVISION,
    LOCAL_MODEL_REVISION,
    RERANKER_IMPLEMENTATION_REVISION,
    RERANKER_MODEL_REVISION,
    AppConfig,
)
from enterprise_rag.retrieval import _AUDITED_REMOTE_CODE

MANIFEST_PATH = Path("docs/model-manifest.md")
CACHE_ROOT = Path(".local/model-cache")

_SPEC = importlib.util.spec_from_file_location(
    "check_model_manifest",
    Path("scripts/check_model_manifest.py"),
)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)
manifest_total_bytes = _MODULE.manifest_total_bytes
ModelEntry = _MODULE.ModelEntry
parse_manifest = _MODULE.parse_manifest
validate_remote_metadata = _MODULE.validate_remote_metadata
find_cached_files = _MODULE.find_cached_files
main = _MODULE.main


def test_every_model_entry_has_supply_chain_fields() -> None:
    entries = parse_manifest(MANIFEST_PATH)

    assert len(entries) == 13
    assert len({entry.cache_path for entry in entries}) == len(entries)
    for entry in entries:
        assert len(entry.revision) == 40
        assert len(entry.sha256) == 64
        assert entry.license == "apache-2.0"
        assert entry.expected_bytes > 0
        assert entry.cache_path.is_relative_to(CACHE_ROOT)
        assert entry.source_url.startswith("https://huggingface.co/")


def test_manifest_total_and_config_revisions_are_exact() -> None:
    entries = parse_manifest(MANIFEST_PATH)
    revisions = {
        entry.artifact_id: entry.revision
        for entry in entries
    }

    assert manifest_total_bytes(entries) == 4_146_440_495
    assert revisions["gte-base:model.safetensors"] == EMBEDDING_MODEL_REVISION
    assert revisions["gte-reranker:model.safetensors"] == RERANKER_MODEL_REVISION
    assert revisions["new-impl:modeling.py"] == RERANKER_IMPLEMENTATION_REVISION
    assert revisions["qwen3-4b:q5-k-m"] == LOCAL_MODEL_REVISION
    qwen_entry = next(
        entry for entry in entries if entry.artifact_id == "qwen3-4b:q5-k-m"
    )
    assert qwen_entry.cache_path == AppConfig().local_model_path


def test_manifest_cache_paths_match_runtime_loader_layout() -> None:
    entries = parse_manifest(MANIFEST_PATH)

    for entry in entries:
        if entry.filename.endswith(".gguf"):
            assert entry.cache_path == AppConfig().local_model_path
            continue
        expected_path = (
            CACHE_ROOT
            / f"models--{entry.repository.replace('/', '--')}"
            / "snapshots"
            / entry.revision
            / entry.filename
        )
        assert entry.cache_path == expected_path


def test_runtime_remote_code_allowlist_matches_manifest() -> None:
    entries = parse_manifest(MANIFEST_PATH)
    manifest_code = {
        entry.filename: (entry.expected_bytes, entry.sha256)
        for entry in entries
        if entry.repository == "Alibaba-NLP/new-impl"
    }

    assert _AUDITED_REMOTE_CODE == manifest_code


@pytest.mark.parametrize(
    "mutation",
    [
        lambda content: content.replace(
            '"expected_bytes":1429',
            '"expected_bytes":"1429"',
            1,
        ),
        lambda content: content.replace(
            "/config.json\",\"expected_bytes\":1429",
            "/wrong.json\",\"expected_bytes\":1429",
            1,
        ),
        lambda content: content.replace(
            "/config.json\",\"expected_bytes\":1429",
            "/config.json?download=true\",\"expected_bytes\":1429",
            1,
        ),
        lambda content: content.replace(
            ".local/model-cache/models--Alibaba-NLP--gte-multilingual-base/"
            "snapshots/087a024525fd6e2fe749cb4679d218d8bcc95bdd/config.json",
            ".local/model-cache/gte-multilingual-base/config.json",
            1,
        ),
    ],
)
def test_manifest_rejects_malformed_types_or_unbound_urls(
    tmp_path: Path,
    mutation: Callable[[str], str],
) -> None:
    source = MANIFEST_PATH.read_text(encoding="utf-8")
    changed = mutation(source)
    assert changed != source
    path = tmp_path / "manifest.md"
    path.write_text(changed, encoding="utf-8")

    with pytest.raises(ValueError):
        parse_manifest(path)


def test_metadata_validation_checks_revision_size_and_lfs_digest() -> None:
    entries = parse_manifest(MANIFEST_PATH)
    weight_entry = next(
        entry
        for entry in entries
        if entry.artifact_id == "gte-base:model.safetensors"
    )
    code_bytes = b"audited code"
    code_entry = replace(
        next(
            entry
            for entry in entries
            if entry.artifact_id == "new-impl:modeling.py"
        ),
        expected_bytes=len(code_bytes),
        sha256=hashlib.sha256(code_bytes).hexdigest(),
    )
    selected = (weight_entry, code_entry)

    def fetcher(repository: str, revision: str) -> dict[str, object]:
        assert len(revision) == 40
        if repository == "Alibaba-NLP/gte-multilingual-base":
            return {
                "sha": revision,
                "siblings": [
                    {
                        "rfilename": "model.safetensors",
                        "size": 610_753_338,
                        "lfs": {
                            "sha256": (
                                "f5a35a10faa54da7717870af1517c9b41e9bd8e3880bc5a8"
                                "e9363d4c3c63e9b0"
                            )
                        },
                    }
                ],
            }
        return {
            "sha": revision,
            "siblings": [
                {
                    "rfilename": "modeling.py",
                    "size": len(code_bytes),
                }
            ],
        }

    def content_fetcher(entry: ModelEntry) -> bytes:
        assert entry.artifact_id == code_entry.artifact_id
        return code_bytes

    assert validate_remote_metadata(
        selected,
        fetcher=fetcher,
        content_fetcher=content_fetcher,
    ) == ()

    bad_code_entry = replace(code_entry, sha256="0" * 64)
    assert validate_remote_metadata(
        (bad_code_entry,),
        fetcher=fetcher,
        content_fetcher=content_fetcher,
    ) == ("new-impl:modeling.py: remote content digest does not match",)


def test_metadata_validation_does_not_download_large_file_without_lfs_digest() -> None:
    entry = next(
        item
        for item in parse_manifest(MANIFEST_PATH)
        if item.artifact_id == "gte-base:model.safetensors"
    )

    def fetcher(repository: str, revision: str) -> dict[str, object]:
        return {
            "sha": revision,
            "siblings": [
                {"rfilename": entry.filename, "size": entry.expected_bytes}
            ],
        }

    def forbidden_content_fetcher(entry: ModelEntry) -> bytes:
        raise AssertionError(f"must not fetch large artifact: {entry.artifact_id}")

    assert validate_remote_metadata(
        (entry,),
        fetcher=fetcher,
        content_fetcher=forbidden_content_fetcher,
    ) == ("gte-base:model.safetensors: large artifact lacks LFS digest metadata",)


def test_weight_inventory_scans_unexpected_nested_cache_paths(
    tmp_path: Path,
) -> None:
    unexpected = tmp_path / "models--unexpected" / "snapshots" / "main"
    unexpected.mkdir(parents=True)
    weight = unexpected / "hidden.safetensors"
    weight.write_bytes(b"not a real model")

    assert find_cached_files(tmp_path) == (weight,)


def test_empty_cache_gate_is_explicit_and_uses_requested_root(
    tmp_path: Path,
) -> None:
    arguments = [
        "--manifest",
        str(MANIFEST_PATH),
        "--assert-empty-cache",
        "--cache-root",
        str(tmp_path),
    ]
    assert main(arguments) == 0

    cached_file = tmp_path / "unexpected" / "blob"
    cached_file.parent.mkdir()
    cached_file.write_bytes(b"unexpected cached content")

    assert main(arguments) == 1
