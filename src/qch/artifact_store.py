"""ArtifactStore: an abstraction for artifact BYTES (DB-2 Phase A6).

Deliberately separate from `qch.repositories.interfaces.Storage`, which
holds only metadata/facts (a URI, a SHA-256, a size, a format --
`qch.models.Artifact`'s own shape, unchanged since DB-1). `Storage`
never sees a byte of an actual circuit; `ArtifactStore` never sees a
database row. This mirrors the existing
`QCH -> services -> Storage protocol -> SQLiteStorage` layering one
level further:

    QCH
     |
     +---- metadata Storage  -> SQLite            (facts)
     |
     +---- ArtifactStore     -> LocalArtifactStore (bytes)

so that a future S3/Blob/GCS-backed `ArtifactStore` implementation
requires no change to `hub.artifacts`/`hub.materializations` or to the
metadata schema -- only which `ArtifactStore` a `QCH` instance is
constructed with.

Content addressing: every artifact is identified by its own SHA-256
hex digest (`content_id`). `Artifact.uri` never stores a filesystem
path directly (see DB-2 Phase A6 section 9 -- a temporary build
workspace path must never leak into a persistent record); it stores a
backend-scoped locator (`"local-artifact-store://<content_id>"` for
`LocalArtifactStore`) that only this module's own `open()`/`path_for()`
know how to resolve.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol

_CHUNK_SIZE = 1 << 20  # 1 MiB -- streamed, never load a whole artifact into memory
_URI_SCHEME = "local-artifact-store://"


class ArtifactStoreError(Exception):
    """Raised for ArtifactStore-level failures (missing content, a
    corrupted object, an I/O error) -- never a raw OSError/IOError."""


@dataclass
class PutResult:
    content_id: str  # the SHA-256 hex digest of the stored bytes
    size_bytes: int
    uri: str  # the backend-scoped locator to persist as Artifact.uri
    deduplicated: bool  # True if identical bytes already existed


class ArtifactStore(Protocol):
    """The one interface every artifact-bytes backend must implement.
    `hub.artifacts`/`hub.materializations` depend only on this, never
    on "it's a local file" or "it's S3" directly."""

    def put(self, source_path: str | Path) -> PutResult:
        """Streams `source_path`'s bytes into the store (never loading
        the whole file into memory), computing its SHA-256 incrementally.
        If content with that hash already exists, the existing object is
        reused (`deduplicated=True`) and no second physical copy is
        written."""
        ...

    def exists(self, content_id: str) -> bool: ...

    def open(self, content_id: str) -> BinaryIO:
        """Raises ArtifactStoreError if content_id is not present."""
        ...

    def verify(self, content_id: str) -> bool:
        """Recomputes the stored object's SHA-256 and compares it to
        content_id -- an integrity check, never a correctness check of
        what the bytes represent."""
        ...

    def delete(self, content_id: str) -> None:
        """Removes one physical object. Callers are responsible for
        confirming nothing still references it (DB-2 Phase A6 does not
        implement garbage collection -- see
        docs/DB2_A5_ARTIFACT_MATERIALIZATION_ARCHITECTURE.md section 16)."""
        ...


def uri_for(content_id: str) -> str:
    return f"{_URI_SCHEME}{content_id}"


def content_id_from_uri(uri: str) -> str | None:
    """Returns the content_id if `uri` is a local-artifact-store URI,
    else None (it may belong to a different ArtifactStore backend)."""
    return uri[len(_URI_SCHEME) :] if uri.startswith(_URI_SCHEME) else None


class LocalArtifactStore:
    """Content-addressed, local-filesystem `ArtifactStore` -- the only
    implementation in DB-2 Phase A6 (V0.1 is local-only; see
    docs/DB2_A6_MATERIALIZATION_ENGINE_V01.md for why cloud backends are
    explicitly out of scope here).

    Layout: `<root>/<sha256[:2]>/<sha256[2:4]>/<sha256>` -- the classic
    two-level fan-out used by e.g. Git's own object store, chosen so no
    single directory ever holds more than a few hundred entries even at
    large scale.

    Atomicity: `put()` always writes to a private staging file under
    `<root>/tmp/` first, then does one `os.replace()` onto the final,
    content-addressed path. `os.replace()` is atomic on the same
    filesystem (POSIX rename(2) semantics; Windows' `MoveFileExW` with
    `MOVEFILE_REPLACE_EXISTING`, which Python's `os.replace` uses on
    Windows) -- a process crash mid-copy can only ever leave an orphaned
    *staging* file, never a half-written object at the final path (see
    DB-2 Phase A6 section 24-25: an unreferenced staging leftover is a
    cleanup nuisance, never metadata corruption)."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._staging_dir = self.root / "tmp"
        self._staging_dir.mkdir(exist_ok=True)

    def path_for(self, content_id: str) -> Path:
        return self.root / content_id[:2] / content_id[2:4] / content_id

    def put(self, source_path: str | Path) -> PutResult:
        source_path = Path(source_path)
        hasher = hashlib.sha256()
        size = 0
        fd, staging_name = tempfile.mkstemp(dir=self._staging_dir)
        try:
            with os.fdopen(fd, "wb") as staging_file, open(source_path, "rb") as src:
                while chunk := src.read(_CHUNK_SIZE):
                    staging_file.write(chunk)
                    hasher.update(chunk)
                    size += len(chunk)
            content_id = hasher.hexdigest()
            final_path = self.path_for(content_id)
            if final_path.exists():
                # Identical content already stored -- discard the
                # staging copy rather than writing a second physical
                # object (DB-2 Phase A6 section 10/20).
                os.remove(staging_name)
                return PutResult(content_id=content_id, size_bytes=size, uri=uri_for(content_id), deduplicated=True)
            final_path.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staging_name, final_path)
            # READY artifacts are immutable -- never writable after this point.
            os.chmod(final_path, 0o444)
            return PutResult(content_id=content_id, size_bytes=size, uri=uri_for(content_id), deduplicated=False)
        except BaseException:
            if os.path.exists(staging_name):
                os.remove(staging_name)
            raise

    def exists(self, content_id: str) -> bool:
        return self.path_for(content_id).exists()

    def open(self, content_id: str) -> BinaryIO:
        path = self.path_for(content_id)
        if not path.exists():
            raise ArtifactStoreError(f"content not found: {content_id}")
        return open(path, "rb")

    def verify(self, content_id: str) -> bool:
        if not self.exists(content_id):
            return False
        hasher = hashlib.sha256()
        with self.open(content_id) as f:
            while chunk := f.read(_CHUNK_SIZE):
                hasher.update(chunk)
        return hasher.hexdigest() == content_id

    def delete(self, content_id: str) -> None:
        path = self.path_for(content_id)
        if path.exists():
            os.chmod(path, 0o644)
            os.remove(path)
