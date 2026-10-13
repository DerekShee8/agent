from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

_START_MARKER = "<!-- model-manifest:start -->"
_END_MARKER = "<!-- model-manifest:end -->"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_REVISION = re.compile(r"[0-9a-f]{40}\Z")
_CACHE_ROOT = Path(".local/model-cache")
_MAX_INLINE_VERIFICATION_BYTES = 1_000_000


@dataclass(frozen=True, slots=True)
class ModelEntry:
    artifact_id: str
    repository: str
    revision: str
    filename: str
    sha256: str
    license: str
    source_url: str
    expected_bytes: int
    cache_path: Path

    def __post_init__(self) -> None:
        for name in (
            "artifact_id",
            "repository",
            "revision",
            "filename",
            "sha256",
            "license",
            "source_url",
        ):
            value = getattr(self, name)
            if type(value) is not str or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if _REVISION.fullmatch(self.revision) is None:
            raise ValueError("revision must be a 40-character lowercase Git SHA")
        if _SHA256.fullmatch(self.sha256) is None:
            raise ValueError("sha256 must be a 64-character lowercase digest")
        if type(self.expected_bytes) is not int or self.expected_bytes <= 0:
            raise ValueError("expected_bytes must be a positive integer")
        if not isinstance(self.cache_path, Path):
            raise ValueError("cache_path must be a Path")
        if self.cache_path.is_absolute() or ".." in self.cache_path.parts:
            raise ValueError("cache_path must be a safe relative path")
        if not self.cache_path.is_relative_to(_CACHE_ROOT):
            raise ValueError("cache_path must be inside .local/model-cache")

        parsed_url = urllib.parse.urlsplit(self.source_url)
        expected_path = (
            f"/{self.repository}/resolve/{self.revision}/{self.filename}"
        )
        if (
            parsed_url.scheme != "https"
            or parsed_url.netloc != "huggingface.co"
            or parsed_url.path != expected_path
            or parsed_url.query
            or parsed_url.fragment
        ):
            raise ValueError(
                "source_url must exactly bind repository, revision, and filename"
            )

        if self.filename.endswith(".gguf"):
            expected_cache_path = _CACHE_ROOT / self.filename
        else:
            expected_cache_path = (
                _CACHE_ROOT
                / f"models--{self.repository.replace('/', '--')}"
                / "snapshots"
                / self.revision
                / self.filename
            )
        if self.cache_path != expected_cache_path:
            raise ValueError("cache_path does not match the runtime loader layout")


MetadataFetcher = Callable[[str, str], dict[str, object]]
ContentFetcher = Callable[[ModelEntry], bytes]


def parse_manifest(path: Path) -> tuple[ModelEntry, ...]:
    content = path.read_text(encoding="utf-8")
    try:
        marked = content.split(_START_MARKER, 1)[1].split(_END_MARKER, 1)[0]
    except IndexError as error:
        raise ValueError("model manifest markers are missing") from error
    match = re.fullmatch(r"\s*```json\s*(.*?)\s*```\s*", marked, re.DOTALL)
    if match is None:
        raise ValueError("model manifest must contain one fenced JSON block")
    payload = json.loads(match.group(1))
    if not isinstance(payload, list) or not payload:
        raise ValueError("model manifest JSON must be a non-empty array")

    entries: list[ModelEntry] = []
    expected_fields = {
        "artifact_id",
        "repository",
        "revision",
        "filename",
        "sha256",
        "license",
        "source_url",
        "expected_bytes",
        "cache_path",
    }
    for row in payload:
        if not isinstance(row, dict) or set(row) != expected_fields:
            raise ValueError("manifest entry fields are incomplete or unexpected")
        string_fields = expected_fields - {"expected_bytes"}
        for field in string_fields:
            if type(row[field]) is not str:
                raise ValueError(f"{field} must be a string")
        if type(row["expected_bytes"]) is not int:
            raise ValueError("expected_bytes must be an integer")
        entries.append(
            ModelEntry(
                artifact_id=row["artifact_id"],
                repository=row["repository"],
                revision=row["revision"],
                filename=row["filename"],
                sha256=row["sha256"],
                license=row["license"],
                source_url=row["source_url"],
                expected_bytes=row["expected_bytes"],
                cache_path=Path(row["cache_path"]),
            )
        )

    artifact_ids = tuple(entry.artifact_id for entry in entries)
    cache_paths = tuple(entry.cache_path for entry in entries)
    if len(set(artifact_ids)) != len(artifact_ids):
        raise ValueError("artifact_id values must be unique")
    if len(set(cache_paths)) != len(cache_paths):
        raise ValueError("cache_path values must be unique")
    return tuple(entries)


def manifest_total_bytes(entries: Sequence[ModelEntry]) -> int:
    return sum(entry.expected_bytes for entry in entries)


def find_cached_files(cache_root: Path) -> tuple[Path, ...]:
    if not cache_root.exists():
        return ()
    return tuple(
        sorted(
            (path for path in cache_root.rglob("*") if path.is_file()),
            key=lambda path: path.as_posix(),
        )
    )


def validate_remote_metadata(
    entries: Sequence[ModelEntry],
    *,
    fetcher: MetadataFetcher | None = None,
    content_fetcher: ContentFetcher | None = None,
) -> tuple[str, ...]:
    active_fetcher = fetcher or _fetch_metadata
    active_content_fetcher = content_fetcher or _fetch_content
    issues: list[str] = []
    groups: dict[tuple[str, str], list[ModelEntry]] = {}
    for entry in entries:
        groups.setdefault((entry.repository, entry.revision), []).append(entry)

    for (repository, revision), grouped_entries in groups.items():
        try:
            metadata = active_fetcher(repository, revision)
        except Exception as error:
            issues.append(f"{repository}@{revision}: metadata unavailable: {error}")
            continue
        if metadata.get("sha") != revision:
            issues.append(f"{repository}: resolved revision does not match")
        siblings = metadata.get("siblings")
        if not isinstance(siblings, list):
            issues.append(f"{repository}: siblings metadata is missing")
            continue
        by_name = {
            sibling.get("rfilename"): sibling
            for sibling in siblings
            if isinstance(sibling, dict)
            and isinstance(sibling.get("rfilename"), str)
        }
        for entry in grouped_entries:
            sibling = by_name.get(entry.filename)
            if sibling is None:
                issues.append(f"{entry.artifact_id}: remote file is missing")
                continue
            if sibling.get("size") != entry.expected_bytes:
                issues.append(f"{entry.artifact_id}: remote size does not match")
                continue
            lfs = sibling.get("lfs")
            if isinstance(lfs, dict):
                if lfs.get("sha256") != entry.sha256:
                    issues.append(
                        f"{entry.artifact_id}: remote LFS digest does not match"
                    )
                continue
            if entry.expected_bytes > _MAX_INLINE_VERIFICATION_BYTES:
                issues.append(
                    f"{entry.artifact_id}: large artifact lacks LFS digest metadata"
                )
                continue
            try:
                content = active_content_fetcher(entry)
            except Exception as error:
                issues.append(
                    f"{entry.artifact_id}: remote content unavailable: {error}"
                )
                continue
            if len(content) != entry.expected_bytes:
                issues.append(
                    f"{entry.artifact_id}: remote content size does not match"
                )
                continue
            if hashlib.sha256(content).hexdigest() != entry.sha256:
                issues.append(
                    f"{entry.artifact_id}: remote content digest does not match"
                )
    return tuple(issues)


def _fetch_metadata(repository: str, revision: str) -> dict[str, object]:
    url = (
        f"https://huggingface.co/api/models/{repository}/revision/"
        f"{revision}?blobs=true"
    )
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "enterprise-rag-model-manifest/0.1"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError("metadata response must be a JSON object")
    return cast(dict[str, object], payload)


def _fetch_content(entry: ModelEntry) -> bytes:
    if entry.expected_bytes > _MAX_INLINE_VERIFICATION_BYTES:
        raise ValueError("large artifacts must be verified from LFS metadata")
    request = urllib.request.Request(
        entry.source_url,
        headers={"User-Agent": "enterprise-rag-model-manifest/0.1"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        content = response.read(entry.expected_bytes + 1)
    return content


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("docs/model-manifest.md"),
    )
    parser.add_argument("--metadata-only", action="store_true")
    parser.add_argument("--assert-empty-cache", action="store_true")
    parser.add_argument("--cache-root", type=Path, default=_CACHE_ROOT)
    arguments = parser.parse_args(argv)

    try:
        entries = parse_manifest(arguments.manifest)
    except (
        OSError,
        TypeError,
        UnicodeError,
        ValueError,
        json.JSONDecodeError,
    ) as error:
        print(f"manifest invalid: {error}", file=sys.stderr)
        return 1

    print(f"entries={len(entries)}")
    print(f"expected_bytes={manifest_total_bytes(entries)}")
    for entry in entries:
        print(
            f"{entry.artifact_id} {entry.revision} "
            f"{entry.expected_bytes} {entry.filename}"
        )
    if arguments.assert_empty_cache:
        cached_files = find_cached_files(arguments.cache_root)
        if cached_files:
            print(
                f"model cache is not empty: {len(cached_files)} file(s)",
                file=sys.stderr,
            )
            return 1
        print("model_cache=empty")
    if arguments.metadata_only:
        issues = validate_remote_metadata(entries)
        if issues:
            for issue in issues:
                print(issue, file=sys.stderr)
            return 1
        print("metadata=reachable-and-consistent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
