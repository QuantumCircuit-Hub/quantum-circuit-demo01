"""ECDSAFailMaterializer: the one real Materializer plugin shipped in
DB-2 Phase A6.

Turns an eligible ECDSA.Fail `CircuitVersion` (see DB2_A3/A4 -- promoted
or validated-but-unpromoted) into a concrete `ops.bin` artifact, using
the exact pipeline reconstructed and empirically confirmed
byte-reproducible in DB2_A5_ARTIFACT_MATERIALIZATION_ARCHITECTURE.md:
`cargo build --release --bin build_circuit` against the resolved
immutable source commit, then run the resulting binary.

**Deliberately does NOT run `eval_circuit`** (the 9,024-shot
correctness harness) -- per DB-2 Phase A6 section 13, materialization
SUCCESS means "a concrete artifact was produced," never "correctness
was verified." Running the trusted evaluator is a separate, later,
optional operation (a future verification phase), not part of
materialization.

**Security model (read before pointing this at anything you don't
already trust -- DB-2 Phase A6 section 15):**

- **Workspace isolation: yes.** Source for the target commit is
  extracted via `git archive <sha> | tarfile.extractall(...)` into a
  fresh, disposable temp directory. This is a pure read of the
  original repository's object database -- it never touches that
  repository's `HEAD`, index, working tree, or even its
  `.git/worktrees/` registration (unlike `git worktree add`, `git
  archive` leaves no trace in the source repository at all).
- **Process sandboxing: NO.** `cargo build`/`build_circuit` run as
  ordinary subprocesses with this process's own OS privileges -- no
  container, VM, seccomp filter, or bubblewrap/sandbox-exec equivalent
  of the kind ECDSA.Fail's own CI pipeline uses (see
  DB2_A5_ARTIFACT_MATERIALIZATION_ARCHITECTURE.md section 5/25). This
  is safe ONLY because V0.1's intended use is materializing already-
  known, already-trusted historical commits (exactly what DB-2 Phase
  A2-A4 discovered) -- it must NOT be pointed at arbitrary,
  unvetted/adversarial source without adding real OS-level or
  container-based sandboxing first.
- **Network isolation: NOT enforced.**
- **CPU/memory/disk limits: NOT enforced** (no portable stdlib
  mechanism used here; a true resource-quota mechanism is OS-specific
  and out of scope for V0.1).
- **Timeout: enforced** (`subprocess.run(..., timeout=...)`).
- **Output-size cap: enforced, but only POST-HOC** -- after generation
  finishes, before the (potentially large) file is hashed/stored; this
  is a sanity check, not a preventive disk quota.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import tarfile
from pathlib import Path
from typing import Any

from qch.materializers import MaterializationContext, MaterializationError, MaterializationOutput, MaterializationTimeoutError
from qch.models import SourceCommit

_DEFAULT_TIMEOUT_SECONDS = 900.0
_DEFAULT_MAX_OUTPUT_BYTES = 4 * 1024**3  # 4 GiB -- generous vs. the largest sample observed (1.6 GB, DB2_A5)


def _tool_version(cmd: list[str]) -> str:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return (result.stdout or result.stderr).strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _extract_source(repo_path: Path, commit_sha: str, dest_dir: Path) -> None:
    """Populates `dest_dir` with the tree at `commit_sha`, via `git
    archive` piped straight into `tarfile` -- no external `tar` binary
    dependency, and no mutation of `repo_path` whatsoever (a pure read
    of the object database; see this module's own docstring)."""
    proc = subprocess.Popen(
        ["git", "-C", str(repo_path), "archive", "--format=tar", commit_sha],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        with tarfile.open(fileobj=proc.stdout, mode="r|") as tar:
            tar.extractall(dest_dir, filter="data")
    finally:
        _, stderr = proc.communicate()
    if proc.returncode != 0:
        raise MaterializationError(
            "SOURCE_PREPARATION_FAILED",
            f"git archive {commit_sha} failed: {stderr.decode('utf-8', errors='replace').strip()}",
        )


class ECDSAFailMaterializer:
    """Configured once, at `QCH.open(..., materializers=[...])` time,
    with the path to a local read-only clone of `ecdsafail-challenge`
    -- never with a per-call repo path (DB-2 Phase A6 section 19's
    "prefer registration at setup" guidance)."""

    name = "ecdsafail_rust_harness"
    # This plugin's OWN version -- bump it whenever the generation
    # logic in this file changes (e.g. adding eval_circuit later), not
    # when the ECDSA.Fail repository's own code changes (that is
    # already captured by `version_id`/the resolved commit, not this
    # field). See qch.materializers.Materializer's own docstring.
    version = "0.1.0"

    def __init__(
        self,
        source_repo_path: str | Path,
        *,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        max_output_bytes: int = _DEFAULT_MAX_OUTPUT_BYTES,
    ) -> None:
        self._source_repo_path = Path(source_repo_path)
        self._default_timeout = timeout_seconds
        self._max_output_bytes = max_output_bytes

    def can_materialize(self, context: MaterializationContext) -> bool:
        return self._resolve_commit(context) is not None

    def environment_fingerprint(self, context: MaterializationContext) -> str:
        """Deliberately scoped to the shared TOOLCHAIN environment
        (rustc/cargo version, OS platform, this plugin's own version) --
        NOT a per-commit dependency-lock hash. `Cargo.lock`'s content
        varies commit-to-commit across ECDSA.Fail's history, but that
        variation is already fully captured by `version_id` itself (via
        the resolved commit), so folding it into the fingerprint too
        would just duplicate the same fact under a different name. This
        resolves DB2_A5_ARTIFACT_MATERIALIZATION_ARCHITECTURE.md's own
        open question #1 in favor of the simpler, non-redundant scope
        for V0.1."""
        payload: dict[str, Any] = {
            "rustc": _tool_version(["rustc", "--version"]),
            "cargo": _tool_version(["cargo", "--version"]),
            "platform": platform.platform(),
            "materializer_version": self.version,
        }
        canonical = json.dumps(payload, sort_keys=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]

    def materialize(self, context: MaterializationContext) -> MaterializationOutput:
        commit = self._resolve_commit(context)
        if commit is None:
            raise MaterializationError(
                "PROVENANCE_UNRESOLVED",
                f"no SourceCommit in provenance matches version {context.version.version_id!r}'s "
                f"external_version_key {context.version.external_version_key!r}",
            )

        timeout = float(context.parameters.get("timeout_seconds", self._default_timeout))
        src_dir = context.workspace / "src"
        src_dir.mkdir(parents=True, exist_ok=True)

        try:
            _extract_source(self._source_repo_path, commit.commit_sha, src_dir)
        except (OSError, subprocess.SubprocessError) as exc:
            raise MaterializationError("SOURCE_PREPARATION_FAILED", f"failed to extract {commit.commit_sha}: {exc}") from exc

        self._run(["cargo", "build", "--release", "--bin", "build_circuit"], src_dir, timeout, "BUILD_FAILED")

        binary = src_dir / "target" / "release" / ("build_circuit.exe" if os.name == "nt" else "build_circuit")
        if not binary.exists():
            raise MaterializationError("BUILD_FAILED", f"expected binary not found at {binary}")

        self._run([str(binary)], src_dir, timeout, "GENERATION_FAILED")

        ops_bin = src_dir / "ops.bin"
        if not ops_bin.exists():
            raise MaterializationError("OUTPUT_MISSING", "build_circuit did not produce ops.bin")

        size = ops_bin.stat().st_size
        if size > self._max_output_bytes:
            raise MaterializationError(
                "OUTPUT_TOO_LARGE", f"ops.bin is {size} bytes, exceeding the configured {self._max_output_bytes}-byte cap"
            )

        return MaterializationOutput(
            file_path=ops_bin,
            artifact_type="circuit",
            format="ops_bin",
            metadata={"source_commit_sha": commit.commit_sha, "repository": commit.repository},
        )

    # -- internal helpers ---------------------------------------------
    def _resolve_commit(self, context: MaterializationContext) -> SourceCommit | None:
        """The realization commit is identified from
        `version.external_version_key` (`"ecdsafail:<7-char sha>"` --
        the same convention `ECDSAFailImporter`/`ECDSAFailHistoryImporter`
        already use, see DB2_A2_METADATA_INGESTION.md's "Identity
        rules"), matched against the commits QCH's own provenance
        already resolved for this version -- never against every commit
        in the repository."""
        key = context.version.external_version_key
        if not key or not key.startswith("ecdsafail:"):
            return None
        short_sha = key.split(":", 1)[1]
        for commit in context.source_commits:
            if commit.commit_sha == short_sha:
                return commit
        return None

    def _run(self, argv: list[str], cwd: Path, timeout: float, error_type: str) -> None:
        try:
            result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise MaterializationTimeoutError(f"{argv[0]} exceeded {timeout}s timeout") from exc
        if result.returncode != 0:
            raise MaterializationError(error_type, f"{' '.join(argv)} exited {result.returncode}: {result.stderr[-4000:]}")
