"""Tests for qch.history.version_graph (DB-5 Phase A11) against small,
disposable, synthetic Git repositories -- not the huge real ECDSA.Fail
repository (see tests/test_qch_history_real_repo_version_graph.py for
that, skipped when the real repo path isn't present).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from qch import QCH  # noqa: E402
from qch.history.version_graph import RELATION_BRANCHED_FROM, RELATION_NEXT_PROMOTED_COMMIT, build_version_graph  # noqa: E402
from qch.importers.ecdsafail_history import ECDSAFailHistoryImporter  # noqa: E402

LOGICAL_CIRCUIT_ID = "qch:logical:secp256k1_point_add"


class _SyntheticRepo:
    """Same small helper tests/test_qch_ecdsafail_history_importer.py
    uses -- a real disposable Git repo built with the actual `git` CLI."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.mkdir(parents=True, exist_ok=True)
        self._run(["init", "-q", "-b", "main"])
        self._run(["config", "user.name", "Test Author"])
        self._run(["config", "user.email", "test@example.com"])
        self._file = path / "content.txt"
        self._counter = 0

    def _run(self, args: list[str]) -> str:
        result = subprocess.run(["git", "-C", str(self.path), *args], capture_output=True, text=True, check=True)
        return result.stdout.strip()

    def commit(self, message: str) -> str:
        self._counter += 1
        self._file.write_text(f"revision {self._counter}\n", encoding="utf-8")
        self._run(["add", "."])
        self._run(["commit", "-q", "-m", message])
        return self._run(["rev-parse", "HEAD"])

    def checkout_new_branch(self, name: str, *, start_point: str = "HEAD") -> None:
        self._run(["checkout", "-q", "-b", name, start_point])

    def checkout(self, ref: str) -> None:
        self._run(["checkout", "-q", ref])


@pytest.fixture()
def hub():
    hub = QCH.open(":memory:")
    yield hub
    hub.close()


def _version_by_uuid_prefix(hub, uuid_prefix: str):
    for v in hub.versions.list(LOGICAL_CIRCUIT_ID):
        if v.metadata.get("submission_uuid", "").startswith(uuid_prefix):
            return v
    raise AssertionError(f"no version found for submission uuid prefix {uuid_prefix!r}")


def test_promoted_chain_gets_historical_successor_edges_in_chronological_order(tmp_path, hub):
    uuid_a = "aaaaaaaa-0000-4000-8000-000000000001"
    uuid_b = "bbbbbbbb-0000-4000-8000-000000000002"
    uuid_c = "cccccccc-0000-4000-8000-000000000003"

    repo = _SyntheticRepo(tmp_path / "repo")
    repo.commit("Initial research commit")
    for uuid in (uuid_a, uuid_b, uuid_c):
        repo.checkout_new_branch(f"submissions/{uuid}")
        repo.commit(f"Submission {uuid}")
        repo.checkout("main")
        repo.commit(f"Accept submission {uuid}")

    ECDSAFailHistoryImporter(hub).import_history(repo.path, repository="test/repo")
    result = build_version_graph(hub, repo.path)

    assert result.promoted_versions_considered == 3
    assert result.validated_unpromoted_versions_considered == 0
    assert result.next_promoted_commit_edges_created == 2
    assert result.warnings == []

    va, vb, vc = (_version_by_uuid_prefix(hub, u) for u in (uuid_a, uuid_b, uuid_c))
    edges_from_a = hub.evolution.list_edges_from(va.version_id)
    assert any(e.target_version_id == vb.version_id and e.relation_type == RELATION_NEXT_PROMOTED_COMMIT for e in edges_from_a)
    edges_from_b = hub.evolution.list_edges_from(vb.version_id)
    assert any(e.target_version_id == vc.version_id and e.relation_type == RELATION_NEXT_PROMOTED_COMMIT for e in edges_from_b)
    # no direct A->C edge: the chain must reflect actual chronological adjacency, not a full clique
    assert not any(e.target_version_id == vc.version_id for e in edges_from_a)


def test_validated_unpromoted_branch_gets_branched_from_nearest_promoted_ancestor(tmp_path, hub):
    uuid_a = "aaaaaaaa-0000-4000-8000-000000000001"  # promoted, first
    uuid_fork = "ffffffff-0000-4000-8000-000000000009"  # forks after A, never merged
    uuid_b = "bbbbbbbb-0000-4000-8000-000000000002"  # promoted, second (after the fork)

    repo = _SyntheticRepo(tmp_path / "repo")
    repo.commit("Initial research commit")

    repo.checkout_new_branch(f"submissions/{uuid_a}")
    repo.commit(f"Submission {uuid_a}")
    repo.checkout("main")
    repo.commit(f"Accept submission {uuid_a}")

    # Fork happens here, after A landed on main, and is never promoted.
    repo.checkout_new_branch(f"submissions/{uuid_fork}", start_point="main")
    repo.commit(f"Validate submission {uuid_fork}")

    repo.checkout("main")
    repo.checkout_new_branch(f"submissions/{uuid_b}")
    repo.commit(f"Submission {uuid_b}")
    repo.checkout("main")
    repo.commit(f"Accept submission {uuid_b}")

    ECDSAFailHistoryImporter(hub).import_history(repo.path, repository="test/repo")
    result = build_version_graph(hub, repo.path)

    assert result.promoted_versions_considered == 2
    assert result.validated_unpromoted_versions_considered == 1
    assert result.branched_from_edges_created == 1
    assert result.warnings == []

    va = _version_by_uuid_prefix(hub, uuid_a)
    v_fork = _version_by_uuid_prefix(hub, uuid_fork)
    edges_from_a = hub.evolution.list_edges_from(va.version_id)
    assert any(e.target_version_id == v_fork.version_id and e.relation_type == RELATION_BRANCHED_FROM for e in edges_from_a)


def test_build_version_graph_is_idempotent(tmp_path, hub):
    uuid_a = "aaaaaaaa-0000-4000-8000-000000000001"
    uuid_b = "bbbbbbbb-0000-4000-8000-000000000002"
    repo = _SyntheticRepo(tmp_path / "repo")
    repo.commit("Initial research commit")
    for uuid in (uuid_a, uuid_b):
        repo.checkout_new_branch(f"submissions/{uuid}")
        repo.commit(f"Submission {uuid}")
        repo.checkout("main")
        repo.commit(f"Accept submission {uuid}")

    ECDSAFailHistoryImporter(hub).import_history(repo.path, repository="test/repo")
    first = build_version_graph(hub, repo.path)
    second = build_version_graph(hub, repo.path)

    assert first.next_promoted_commit_edges_created == 1
    assert second.next_promoted_commit_edges_created == 0
    assert second.next_promoted_commit_edges_already_existed == 1

    va = _version_by_uuid_prefix(hub, uuid_a)
    assert len(hub.evolution.list_edges_from(va.version_id)) == 1  # never duplicated
