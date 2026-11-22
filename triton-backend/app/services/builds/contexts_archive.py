"""Bounded POSIX tar ingestion and deterministic, filtered build contexts.

No archive is extracted. Only validated regular files are written to a private
scratch directory; links, extensions, devices and ambiguous names fail closed.
"""

import gzip
import hashlib
import re
import shlex
import tarfile
import time
from pathlib import Path
from typing import BinaryIO, Callable, cast

from docker.utils.build import PatternMatcher

from app.exceptions import BadRequestError
from app.schemas.build_contexts import BuildContextRequest, ContextFile, ContextManifest
from app.services.candidates.artifacts import CaptureLimits, safe_path
from app.services.lineage import digest

BLOCK = 512
MAX_FILES = 10_000
MAX_CONTROL_BYTES = 65536
SECRET_DIRS = {".git", ".aws", ".ssh", ".kube", ".docker", ".azure", ".config", ".gnupg"}
SECRET_NAMES = {".env", ".npmrc", ".pypirc", ".netrc", "credentials", "kubeconfig", "id_rsa", "id_ed25519"}
SECRET_BYTES = re.compile(rb"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----|(?:AKIA|ASIA)[A-Z0-9]{16}")


def sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def secret_path(path: str) -> bool:
    parts = path.lower().split("/")
    name = parts[-1]
    return (
        any(part in SECRET_DIRS for part in parts)
        or name in SECRET_NAMES or name.startswith(".env.")
        or name.endswith((".pem", ".key", ".p12", ".pfx", ".jks"))
    )


def _allowed(path: str, allowed: tuple[str, ...]) -> bool:
    return any(root == "." or path == root or path.startswith(root + "/") for root in allowed)


def _credential_assignment(instruction: str, words: list[str]) -> bool:
    """Check assignment names, including every key in modern multi-key ENV syntax."""
    if instruction not in {"ENV", "ARG"} or not words:
        return False
    # Legacy ENV uses `key value`; modern ENV and ARG use `key=value`.
    names = [word.split("=", 1)[0] for word in words] if "=" in words[0] else words[:1]
    return any(re.search(r"(?:^|_)(password|secret|token|access_key|private_key)(?:_|$)",
                         name, re.IGNORECASE) for name in names)


def _dockerfile(data: bytes, declared: tuple[str, ...], *, allow_tags: bool = False) -> tuple[str, ...]:
    def external(reference: str) -> None:
        if declared:
            valid = reference in declared
        else:
            repository = r"(?:[a-z0-9.-]+:[0-9]+/)?[a-z0-9]+(?:[._/-]+[a-z0-9]+)*"
            tag = r"(?::[A-Za-z0-9_][A-Za-z0-9_.-]{0,127})?"
            digest = r"@sha256:[0-9a-f]{64}"
            suffix = rf"(?:{digest})?" if allow_tags else digest
            valid = re.fullmatch(repository + tag + suffix, reference) is not None
        if not valid:
            raise BadRequestError("Dockerfile base image must be a supported image reference with an exact digest")
        found.add(reference)

    try:
        text = data.decode("utf-8")
        if re.search(r"^\s*#\s*(syntax|escape)\s*=", text, re.MULTILINE | re.IGNORECASE):
            raise BadRequestError("Custom Dockerfile frontends and escapes are not supported")
        text = text.replace("\r\n", "\n").replace("\\\n", " ")
        stages: set[str] = set()
        found: set[str] = set()
        count = 0
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            words = shlex.split(line)
            if not words:
                continue
            if _credential_assignment(words[0].upper(), words[1:]):
                raise BadRequestError("Credential arguments must not be embedded in the Dockerfile")
            if words[0].upper() in {"COPY", "ADD"}:
                for word in words[1:]:
                    if word.startswith("--from="):
                        origin = word.split("=", 1)[1]
                        if origin.lower() not in stages and not (origin.isdigit() and int(origin) < count):
                            external(origin)
            if words[0].upper() != "FROM":
                continue
            count += 1
            words = words[1:]
            if words and words[0].startswith("--platform="):
                if words[0].split("=", 1)[1] not in {"linux/amd64", "linux/arm64"}:
                    raise BadRequestError("Dynamic build platforms are not supported")
                words = words[1:]
            if not words or len(words) not in {1, 3} or (len(words) == 3 and words[1].upper() != "AS"):
                raise BadRequestError("Unsupported Dockerfile FROM instruction")
            image = words[0]
            if image.lower() not in stages and image != "scratch":
                external(image)
            if len(words) == 3:
                if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", words[2]):
                    raise BadRequestError("Invalid Dockerfile stage name")
                stages.add(words[2].lower())
        if not count or (declared and found != set(declared)):
            raise BadRequestError("Declared base images differ from Dockerfile FROM instructions")
        return tuple(sorted(found))
    except (UnicodeError, ValueError):
        raise BadRequestError("Invalid UTF-8 Dockerfile") from None


def prepare(
    source: BinaryIO, directory: Path, output: BinaryIO, request: BuildContextRequest,
    max_bytes: int, checkpoint: Callable[[], None] = lambda: None, *, allow_tags: bool = False,
) -> ContextManifest:
    """Input is a complete, already size/hash-checked uncompressed POSIX tar."""
    started = time.monotonic()

    def check() -> None:
        if time.monotonic() - started > 120:
            raise BadRequestError("Build context processing deadline exceeded")
        checkpoint()

    entries: dict[str, ContextFile] = {}
    names: set[str] = set()
    total = 0
    nodes = 0
    while True:
        check()
        block = source.read(BLOCK)
        if block == bytes(BLOCK):
            if source.read(BLOCK) != bytes(BLOCK):
                raise BadRequestError("Incomplete tar end marker")
            while chunk := source.read(65536):
                if any(chunk):
                    raise BadRequestError("Unexpected data after tar end marker")
            break
        if len(block) != BLOCK:
            raise BadRequestError("Incomplete tar archive")
        try:
            entry = tarfile.TarInfo.frombuf(block, "utf-8", "strict")
        except (tarfile.TarError, UnicodeError, ValueError):
            raise BadRequestError("Invalid tar header") from None
        nodes += 1
        if nodes > MAX_FILES:
            raise BadRequestError("Too many build context entries")
        if entry.type not in {tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE} or entry.size < 0:
            raise BadRequestError("Only regular files and directories are allowed in build contexts")
        name = entry.name
        while name.startswith("./"):
            name = name[2:]
        if entry.isdir() and name.rstrip("/") in {"", "."}:
            if entry.size:
                raise BadRequestError("Invalid tar root directory")
            continue
        path = safe_path(name.rstrip("/") if entry.isdir() else name, CaptureLimits())
        folded = path.casefold()
        if folded in names:
            raise BadRequestError("Duplicate or case-conflicting build context path")
        names.add(folded)
        if entry.isdir():
            if entry.size:
                raise BadRequestError("Invalid tar directory size")
            continue
        total += entry.size
        if total > max_bytes:
            raise BadRequestError("Expanded build context exceeds byte limit")
        target = directory / path
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            hasher = hashlib.sha256()
            with target.open("xb") as dest:
                remaining = entry.size
                while remaining:
                    check()
                    chunk = source.read(min(65536, remaining))
                    if not chunk:
                        raise BadRequestError("Incomplete tar file")
                    dest.write(chunk)
                    hasher.update(chunk)
                    remaining -= len(chunk)
            padding = (-entry.size) % BLOCK
            if source.read(padding) != bytes(padding):
                raise BadRequestError("Invalid tar padding")
        except OSError:
            raise BadRequestError("Conflicting build context paths") from None
        entries[path] = ContextFile(
            path=path, size=entry.size, digest="sha256:" + hasher.hexdigest(), executable=bool(entry.mode & 0o111),
        )
    ignore = request.dockerfile + ".dockerignore"
    if ignore not in entries:
        ignore = ".dockerignore"
    if ignore in entries and (not _allowed(ignore, request.allowed_paths) or secret_path(ignore)):
        raise BadRequestError("The active Dockerignore file must be included in allowed_paths")
    patterns: list[str] = []
    if ignore in entries:
        if entries[ignore].size > MAX_CONTROL_BYTES:
            raise BadRequestError("Dockerignore exceeds limit")
        try:
            lines = (directory / ignore).read_text(encoding="utf-8-sig").splitlines()
            patterns = [line.strip() for line in lines if line.strip() and not line.startswith("#")]
            if len(patterns) > 1000 or any(len(line) > 256 for line in patterns):
                raise BadRequestError("Dockerignore pattern limit exceeded")
            if any(line == "!" for line in patterns):
                raise ValueError("Empty exception")
        except (UnicodeError, ValueError):
            raise BadRequestError("Invalid dockerignore") from None
    matcher = PatternMatcher(patterns)
    selected = []
    for path, info in sorted(entries.items()):
        check()
        if not _allowed(path, request.allowed_paths) or secret_path(path):
            continue
        if path not in {request.dockerfile, ignore} and matcher.matches(path):
            continue
        # Pattern-based exclusion cannot identify arbitrary secrets; scan common
        # private-key/access-key signatures too, without logging their values.
        with (directory / path).open("rb") as stream:
            tail = b""
            while chunk := stream.read(65536):
                check()
                if SECRET_BYTES.search(tail + chunk):
                    raise BadRequestError("Credential material detected in selected build context")
                tail = chunk[-128:]
        selected.append(info)
    chosen = {item.path: item for item in selected}
    if request.dockerfile not in chosen or any(path not in chosen for path in request.dependency_files):
        raise BadRequestError("Dockerfile or dependency files missing from the selected context")
    if chosen[request.dockerfile].size > MAX_CONTROL_BYTES:
        raise BadRequestError("Dockerfile exceeds limit")
    bases = _dockerfile((directory / request.dockerfile).read_bytes(), request.base_images, allow_tags=allow_tags)
    with gzip.GzipFile(fileobj=output, mode="wb", filename="", mtime=0) as compressed:
        with tarfile.open(fileobj=cast(BinaryIO, compressed), mode="w|", format=tarfile.USTAR_FORMAT) as tar:
            for item in selected:
                check()
                header = tarfile.TarInfo(item.path)
                header.size, header.mode = item.size, 0o755 if item.executable else 0o644
                try:
                    with (directory / item.path).open("rb") as stream:
                        tar.addfile(header, stream)
                except (ValueError, OSError):
                    raise BadRequestError("Build path cannot be represented in POSIX tar") from None
    size = output.tell()
    if size > max_bytes:
        raise BadRequestError("Prepared build context exceeds byte limit")
    output.seek(0)
    hasher = hashlib.sha256()
    while chunk := output.read(65536):
        hasher.update(chunk)
    output.seek(0)
    return ContextManifest(
        files=tuple(selected), inventory_digest=digest([item.model_dump(mode="json") for item in selected]),
        dockerfile_digest=chosen[request.dockerfile].digest,
        dependencies={path: chosen[path].digest for path in request.dependency_files},
        base_images=bases,
        archive_digest="sha256:" + hasher.hexdigest(), archive_bytes=size,
    )
