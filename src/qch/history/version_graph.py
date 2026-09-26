"""Reconstructs the real parent/child structure of the ECDSA.Fail
version history, entirely from evidence already discoverable in Git --
never inventing an ordering where none exists.

`ECDSAFailHistoryImporter` (DB-2 Phase A2) deliberately creates zero
`TransformationEdge`s across the full history (see its own module
docstring: "no unambiguous ordering has been established across the
full eligible population -- every historical_successor edge ... is
deferred, not invented"). This module is that deferred work, done with
real evidence:

- **PROMOTED versions** (a submission's Accept/Validate event commit
  IS reachable on `main`): these have an unambiguous position in
  `main`'s own commit history. Ordering all 520 promoted versions by
  that position and linking each to the very next one gives a real,
  DENSE chain -- relation_type "next_promoted_commit".

  **Deliberately a DIFFERENT relation_type from the frozen V1-V5
  manifest's own "historical_successor" edges, not a superset of the
  same idea** -- an empirical finding from running this reconstruction
  against the real repository, not an assumption: V1's real immediate
  successor in the full promoted chronological sequence is NOT V2 --
  there are roughly a dozen other promoted, non-milestone commits
  between them that the manifest's curators simply skipped when they
  picked five benchmark milestones out of a much denser real history
  (520 promoted versions span main-log positions ~882-1417 out of
  1418, i.e. the overwhelming majority of commits in that range ARE
  promotions). Reusing "historical_successor" for this dense chain
  would have given V1 two DIFFERENT outgoing edges under the same
  relation_type (one to V2, one to its true next commit) -- exactly
  the kind of same-name-different-meaning conflation DB-5 Phase A11's
  own metric-semantics audit warns against, just for a relation_type
  instead of a metric. "historical_successor" therefore keeps its
  original, narrower meaning ("the next CURATED milestone", 4 edges,
  V1-V5 only); "next_promoted_commit" is the complete, dense,
  non-curated chain (519 edges, all 520 promoted versions).
- **VALIDATED-but-never-promoted versions**: these never reached
  `main` at all, so they have no position in it -- treating them as
  successors of anything on main would fabricate an ordering. What IS
  real evidence: `git merge-base <branch_tip> <main>` gives the exact
  commit this submission's branch actually forked from. This module
  links each such version to the nearest PROMOTED version at or before
  that fork point with a third, distinct relation_type,
  "branched_from" -- an honest label for "this is where the fork
  happened," never conflated with either successor relation above (a
  fork that never merged is not a successor in the optimization
  lineage).

Nothing here executes third-party code or checks out a commit into the
working tree -- `GitRepo.log`/`merge_base`/`rev_parse` are the only Git
operations used, all read-only (see git_adapter.py's own docstring).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from qch.importers.git_adapter import GitRepo

if TYPE_CHECKING:
    from qch.hub import QCH
    from qch.models import CircuitVersion

RELATION_NEXT_PROMOTED_COMMIT = "next_promoted_commit"
RELATION_BRANCHED_FROM = "branched_from"


@dataclass
class VersionGraphBuildResult:
    promoted_versions_considered: int = 0
    validated_unpromoted_versions_considered: int = 0
    next_promoted_commit_edges_created: int = 0
    next_promoted_commit_edges_already_existed: int = 0
    branched_from_edges_created: int = 0
    branched_from_edges_already_existed: int = 0
    unresolved_versions: list[str] = field(default_factory=list)  # version_id, with a reason in warnings
    warnings: list[str] = field(default_factory=list)


def _short_or_full_sha_position(sha: str, oldest_first_shas: list[str], position_by_full_sha: dict[str, int]) -> int | None:
    if sha in position_by_full_sha:
        return position_by_full_sha[sha]
    if len(sha) < 40:
        matches = [full for full in oldest_first_shas if full.startswith(sha)]
        if len(matches) == 1:
            return position_by_full_sha[matches[0]]
    return None


def build_version_graph(hub: "QCH", repo_path: str | Path, *, logical_circuit_id: str = "qch:logical:secp256k1_point_add") -> VersionGraphBuildResult:
    """Idempotent: every edge is created through `hub.evolution.add_edge`,
    whose natural key (source, target, relation_type) makes re-running
    this function against an already-processed store a safe no-op for
    edges that already exist (tracked separately in the result so a
    caller can tell "already had this" from "just created this")."""
    result = VersionGraphBuildResult()
    git = GitRepo(Path(repo_path))

    main_ref = "main"
    try:
        git.rev_parse(main_ref)
    except Exception:
        main_ref = "origin/main"

    main_commits = git.log(main_ref)
    oldest_first_shas = [c.sha for c in reversed(main_commits)]
    position_by_full_sha = {sha: i for i, sha in enumerate(oldest_first_shas)}

    versions = hub.versions.list(logical_circuit_id)
    promoted: list[tuple[int, "CircuitVersion"]] = []  # (position on main, version)
    validated_unpromoted: list["CircuitVersion"] = []

    for version in versions:
        if not version.realized_from_submission_id:
            continue  # not realized from a submission at all (e.g. a QASM-imported version elsewhere) -- out of scope here
        submission = hub.submissions.get(version.realized_from_submission_id)

        promoted_commits = hub.submissions.list_source_commits(submission.submission_id, role="promoted")
        if promoted_commits:
            position = _short_or_full_sha_position(promoted_commits[0].commit_sha, oldest_first_shas, position_by_full_sha)
            if position is None:
                result.unresolved_versions.append(version.version_id)
                result.warnings.append(
                    f"version {version.version_id!r} (submission {submission.submission_id!r}): promoted commit "
                    f"{promoted_commits[0].commit_sha!r} not found in `git log {main_ref}` -- skipped"
                )
                continue
            promoted.append((position, version))
            continue

        validated_commits = hub.submissions.list_source_commits(submission.submission_id, role="validated")
        if validated_commits:
            validated_unpromoted.append(version)
            continue

        result.unresolved_versions.append(version.version_id)
        result.warnings.append(
            f"version {version.version_id!r} (submission {submission.submission_id!r}) has neither a 'promoted' "
            f"nor a 'validated' source-commit link -- skipped"
        )

    result.promoted_versions_considered = len(promoted)
    result.validated_unpromoted_versions_considered = len(validated_unpromoted)

    promoted.sort(key=lambda pair: pair[0])
    for (_, earlier), (_, later) in zip(promoted, promoted[1:]):
        already = any(
            e.target_version_id == later.version_id and e.relation_type == RELATION_NEXT_PROMOTED_COMMIT
            for e in hub.evolution.list_edges_from(earlier.version_id)
        )
        hub.evolution.add_edge(earlier.version_id, later.version_id, RELATION_NEXT_PROMOTED_COMMIT)
        if already:
            result.next_promoted_commit_edges_already_existed += 1
        else:
            result.next_promoted_commit_edges_created += 1

    promoted_positions = [p for p, _ in promoted]
    promoted_versions_by_index = [v for _, v in promoted]

    for version in validated_unpromoted:
        submission = hub.submissions.get(version.realized_from_submission_id)
        validated_commit = hub.submissions.list_source_commits(submission.submission_id, role="validated")[0]
        full_sha = validated_commit.commit_sha if len(validated_commit.commit_sha) == 40 else git.rev_parse(validated_commit.commit_sha)

        fork_point = git.merge_base(full_sha, main_ref)
        if fork_point is None:
            result.unresolved_versions.append(version.version_id)
            result.warnings.append(f"version {version.version_id!r}: no merge-base found between {full_sha!r} and {main_ref!r}")
            continue

        fork_position = position_by_full_sha.get(fork_point)
        if fork_position is None:
            result.unresolved_versions.append(version.version_id)
            result.warnings.append(f"version {version.version_id!r}: fork point {fork_point!r} not found in `git log {main_ref}`")
            continue

        # nearest promoted version at or before the fork point (promoted_positions is ascending)
        parent_index = None
        for i, pos in enumerate(promoted_positions):
            if pos <= fork_position:
                parent_index = i
            else:
                break
        if parent_index is None:
            result.unresolved_versions.append(version.version_id)
            result.warnings.append(
                f"version {version.version_id!r}: forked at main position {fork_position} before any promoted version -- no parent"
            )
            continue

        parent_version = promoted_versions_by_index[parent_index]
        already = any(
            e.target_version_id == version.version_id and e.relation_type == RELATION_BRANCHED_FROM
            for e in hub.evolution.list_edges_from(parent_version.version_id)
        )
        hub.evolution.add_edge(parent_version.version_id, version.version_id, RELATION_BRANCHED_FROM)
        if already:
            result.branched_from_edges_already_existed += 1
        else:
            result.branched_from_edges_created += 1

    return result
