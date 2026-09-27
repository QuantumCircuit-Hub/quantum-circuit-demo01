"""Phase 2D.3.1: alias-aware SemanticGuard (branched_from direction).

Deterministic -- no LLM. The existing Phase 2C.1 rule ("branched from X"
-> successors, "X branched from ..." -> predecessors) must apply to the
ENTITY a question names, not only to one spelling of it. Identity comes
exclusively from `qch.nl.version_resolver` (Phase 2D.3, unchanged).

Numbered tags follow the Phase 2D.3.1 spec, section 29. The first half
uses a small REAL in-memory hub; the second half uses the REAL
data/qch_ecdsa_p17.sqlite3 snapshot (skipped if absent), read only.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from qch import QCH  # noqa: E402
from qch.nl.backend import PlannerCandidate  # noqa: E402
from qch.nl.schema_context import load_schema_context  # noqa: E402
from qch.nl.semantic_guard import SemanticGuard, SemanticOutcome  # noqa: E402
from qch.nl.service import QCHNaturalLanguageService, SystemStatus  # noqa: E402
from qch.nl.version_resolver import (  # noqa: E402
    EntityMatch,
    VersionIdentityContext,
    VersionResolutionOutcome,
    canonicalize_plan_versions,
    extract_candidate_version_identifiers,
    resolve_version_identifier,
)
from qch.query.models import QCHQueryPlan, QCHQueryStep  # noqa: E402

UUID_P = "abc12345-0000-4000-8000-000000000001"  # PROMOTED -> ecdsafail:1234abc (label V9)
UUID_C = "cafe0001-0000-4000-8000-000000000003"  # SUBMITTED, no version
UUID_F = "fee19999-0000-4000-8000-000000000006"  # -> ecdsafail:9990aaa; "fee1" also commit prefix of ecdsafail:fee1234
UUID_G = "beef0001-0000-4000-8000-000000000007"  # -> ecdsafail:beef000 (commit + uuid prefix converge)


@pytest.fixture(scope="module")
def schema():
    return load_schema_context()


@pytest.fixture
def guard(schema):
    return SemanticGuard(schema)


@pytest.fixture
def hub():
    h = QCH.open(":memory:")
    cid = h.circuits.create("qch:logical:test_circuit", name="test_circuit").logical_circuit_id

    def sub(uuid: str, status: str):
        return h.submissions.create("ecdsafail", external_submission_key=f"ecdsafail:{uuid}", status=status)

    h.versions.create(cid, external_version_key="ecdsafail:1234abc", version_label="V9", realized_from_submission_id=sub(UUID_P, "PROMOTED").submission_id)
    h.versions.create(cid, external_version_key="ecdsafail:5678def")
    sub(UUID_C, "SUBMITTED")
    h.versions.create(cid, external_version_key="ecdsafail:fee1234")
    h.versions.create(cid, external_version_key="ecdsafail:9990aaa", realized_from_submission_id=sub(UUID_F, "PROMOTED").submission_id)
    h.versions.create(cid, external_version_key="ecdsafail:beef000", realized_from_submission_id=sub(UUID_G, "PROMOTED").submission_id)
    yield h
    h.close()


def _identity(hub, question: str) -> VersionIdentityContext:
    """Built exactly the way the service builds it: resolve every
    candidate token in the question with the Phase 2D.3 resolver."""
    return VersionIdentityContext(hub, [resolve_version_identifier(hub, t) for t in extract_candidate_version_identifiers(question)])


def _branch_plan(version: str, direction: str) -> QCHQueryPlan:
    return QCHQueryPlan(steps=(QCHQueryStep("traverse", {"version": version, "direction": direction, "relation_type": "branched_from"}),))


def _check(guard, hub, question, plan_version, direction):
    return guard.check(question, _branch_plan(plan_version, direction), identity_context=_identity(hub, question))


def _repaired_direction(result):
    assert result.outcome == SemanticOutcome.SAFE_REPAIR, result.outcome
    return result.effective_plan.steps[0].params["direction"]


# -- entity equivalence helper (three-state) -----------------------------------------


def test_entity_match_is_three_state(hub):
    ctx = VersionIdentityContext(hub)
    assert ctx.same_version_entity("abc1234", "ecdsafail:1234abc") == EntityMatch.SAME_ENTITY
    assert ctx.same_version_entity("V9", UUID_P) == EntityMatch.SAME_ENTITY  # alias vs alias
    assert ctx.same_version_entity("abc1234", "ecdsafail:5678def") == EntityMatch.DIFFERENT_ENTITY
    assert ctx.same_version_entity("fee1", "ecdsafail:fee1234") == EntityMatch.UNRESOLVED_OR_AMBIGUOUS
    assert ctx.same_version_entity("cafe0001", "ecdsafail:1234abc") == EntityMatch.UNRESOLVED_OR_AMBIGUOUS
    assert ctx.same_version_entity("nope-not-real", "ecdsafail:1234abc") == EntityMatch.UNRESOLVED_OR_AMBIGUOUS


def test_identity_context_reuses_question_resolutions_without_re_resolving(hub):
    seeded = resolve_version_identifier(hub, "abc1234")
    ctx = VersionIdentityContext(hub, [seeded])
    assert ctx.resolve("abc1234") is seeded


# -- 1-6. forward "branched from X" across every alias form ---------------------------


@pytest.mark.parametrize(
    "alias",
    [
        "ecdsafail:1234abc",  # 1. literal canonical key (Phase 2C.1 literal path)
        "1234abc",  # 2. commit prefix
        "abc1234",  # 3. submission UUID prefix
        UUID_P,  # 4. full submission UUID
        f"ecdsafail:{UUID_P}",  # 5. submission external key
        "V9",  # 6. version label
    ],
)
def test_forward_branched_from_alias_repairs_predecessors_to_successors(guard, hub, alias):
    result = _check(guard, hub, f"Which versions branched from {alias}?", "ecdsafail:1234abc", "predecessors")
    assert _repaired_direction(result) == "successors"


def test_6_internal_version_id_alias_repairs(guard, hub):
    vid = hub.versions.find_by_external_key("ecdsafail:1234abc").version_id
    result = _check(guard, hub, f"Which versions branched from {vid}?", "ecdsafail:1234abc", "predecessors")
    # a 32-char internal id is only extracted by the existing hex-token rule; if it
    # were not extracted no identity repair could fire -- assert the real outcome.
    assert _repaired_direction(result) == "successors"


def test_plan_using_a_different_alias_than_the_question_still_matches(guard, hub):
    """Alias vs alias: question says the submission prefix, plan says the label."""
    result = _check(guard, hub, "Which versions branched from abc1234?", "V9", "predecessors")
    assert _repaired_direction(result) == "successors"


def test_identity_repair_records_inspectable_diagnostics(guard, hub):
    result = _check(guard, hub, "Which versions branched from abc1234?", "ecdsafail:1234abc", "predecessors")
    action = result.repair_actions[0].to_dict()
    assert action["old_value"] == "predecessors" and action["new_value"] == "successors"
    assert action["details"] == {
        "entity_match_basis": "resolved_identity",
        "raw_question_identifier": "abc1234",
        "matched_namespace": "submission_uuid_prefix",
        "canonical_entity": "ecdsafail:1234abc",
        "plan_entity": "ecdsafail:1234abc",
        "relation": "branched_from",
    }


def test_literal_repairs_serialize_exactly_as_in_phase_2c1(guard, hub):
    result = _check(guard, hub, "Which versions branched from ecdsafail:1234abc?", "ecdsafail:1234abc", "predecessors")
    assert "details" not in result.repair_actions[0].to_dict()


def test_correct_direction_is_left_alone(guard, hub):
    result = _check(guard, hub, "Which versions branched from abc1234?", "ecdsafail:1234abc", "successors")
    assert result.outcome != SemanticOutcome.SAFE_REPAIR


# -- 7. reverse wording ----------------------------------------------------------------


@pytest.mark.parametrize(
    "question",
    [
        "What did abc1234 branch from?",
        "Which version did abc1234 branch from?",
        "Show the version that abc1234 was branched from.",
        f"What did {UUID_P} branch from?",
        "What did V9 branch from?",
    ],
)
def test_7_reverse_wording_repairs_successors_to_predecessors(guard, hub, question):
    result = _check(guard, hub, question, "ecdsafail:1234abc", "successors")
    assert _repaired_direction(result) == "predecessors"


# -- 8. different entity ------------------------------------------------------------------


def test_8_different_entity_gets_no_alias_repair(guard, hub):
    result = _check(guard, hub, "Which versions branched from abc1234?", "ecdsafail:5678def", "predecessors")
    assert result.outcome != SemanticOutcome.SAFE_REPAIR


def test_8_multi_identifier_question_binds_the_role_to_the_right_entity(guard, hub):
    """Both entities appear; only abc1234 is the object of "branched from".
    A plan over the OTHER version must not be repaired from that wording."""
    q = "Which versions branched from abc1234, and how does 5678def compare?"
    assert _check(guard, hub, q, "ecdsafail:5678def", "predecessors").outcome != SemanticOutcome.SAFE_REPAIR
    assert _repaired_direction(_check(guard, hub, q, "ecdsafail:1234abc", "predecessors")) == "successors"


# -- 9-11. ambiguous / unknown / known-submission-no-version --------------------------------


def test_9_ambiguous_identifier_never_repairs(guard, hub):
    assert resolve_version_identifier(hub, "fee1").outcome == VersionResolutionOutcome.AMBIGUOUS
    for candidate in ("ecdsafail:fee1234", "ecdsafail:9990aaa"):
        result = _check(guard, hub, "Which versions branched from fee1?", candidate, "predecessors")
        assert result.outcome != SemanticOutcome.SAFE_REPAIR, candidate


def test_10_unknown_identifier_never_repairs(guard, hub):
    result = _check(guard, hub, "Which versions branched from dddd9999?", "ecdsafail:1234abc", "predecessors")
    assert result.outcome != SemanticOutcome.SAFE_REPAIR


def test_11_known_submission_no_version_never_repairs_or_fabricates(guard, hub):
    before = len(hub.versions.list(hub.circuits.list()[0].logical_circuit_id))
    result = _check(guard, hub, "Which versions branched from cafe0001?", "ecdsafail:1234abc", "predecessors")
    assert result.outcome != SemanticOutcome.SAFE_REPAIR
    assert len(hub.versions.list(hub.circuits.list()[0].logical_circuit_id)) == before


def test_11_service_keeps_known_submission_no_version_for_branch_question(hub):
    plan = {"steps": [{"operator": "traverse", "params": {"version": "cafe0001", "direction": "predecessors", "relation_type": "branched_from"}}]}
    svc = QCHNaturalLanguageService(_Scripted(PlannerCandidate(kind="plan", plan_dict=plan)), hub)
    result = svc.ask("Which versions branched from cafe0001?")
    assert result.status == SystemStatus.KNOWN_SUBMISSION_NO_VERSION
    assert result.raw_result is None  # no traversal executed


# -- 12. converged cross-namespace identity ------------------------------------------------


def test_12_converged_identity_allows_repair(guard, hub):
    r = resolve_version_identifier(hub, "beef0")
    assert r.outcome == VersionResolutionOutcome.RESOLVED and r.resolution_status == "converged_match"
    result = _check(guard, hub, "Which versions branched from beef0?", "ecdsafail:beef000", "predecessors")
    assert _repaired_direction(result) == "successors"


# -- 13 / no identity context: Phase 2C.1 behavior is exactly preserved ------------------------


def test_13_without_identity_context_only_literal_matches_repair(guard):
    assert guard.check("Which versions branched from abc1234?", _branch_plan("ecdsafail:1234abc", "predecessors")).outcome != SemanticOutcome.SAFE_REPAIR
    literal = guard.check("Which versions branched from ecdsafail:1234abc?", _branch_plan("ecdsafail:1234abc", "predecessors"))
    assert _repaired_direction(literal) == "successors"


# -- 15. canonicalization idempotence after a repair --------------------------------------------


def test_15_repaired_plan_canonicalizes_idempotently(guard, hub):
    result = _check(guard, hub, "Which versions branched from abc1234?", "abc1234", "predecessors")  # literal raw in plan
    once = canonicalize_plan_versions(hub, result.effective_plan)
    twice = canonicalize_plan_versions(hub, once.plan)
    assert once.plan.steps[0].params == {"version": "ecdsafail:1234abc", "direction": "successors", "relation_type": "branched_from"}
    assert twice.plan.steps == once.plan.steps


# -- service end to end (scripted planner that makes the Phase 2D.3 mistake) --------------------


class _Scripted:
    name = "scripted"

    def __init__(self, candidate):
        self.candidate = candidate

    def generate_plan(self, question, schema, *, context=None):
        return self.candidate


def _wrong_plan(version: str, direction: str) -> PlannerCandidate:
    return PlannerCandidate(kind="plan", plan_dict={"steps": [{"operator": "traverse", "params": {"version": version, "direction": direction, "relation_type": "branched_from"}}]})


def test_service_repairs_and_shows_the_repair_in_the_explanation(hub):
    svc = QCHNaturalLanguageService(_Scripted(_wrong_plan("ecdsafail:1234abc", "predecessors")), hub)
    result = svc.ask("Which versions branched from abc1234?")
    exp = result.explanation.to_dict()
    assert exp["canonical_plan"]["steps"][0]["params"]["direction"] == "successors"
    assert exp["original_plan"]["steps"][0]["params"]["direction"] == "predecessors"
    assert exp["repair_actions"][0]["details"]["entity_match_basis"] == "resolved_identity"


# == REAL database (read-only) =====================================================================

DB_PATH = PROJECT_ROOT / "data" / "qch_ecdsa_p17.sqlite3"
real_db = pytest.mark.skipif(not DB_PATH.exists(), reason=f"real QCH snapshot not present at {DB_PATH}")


def _ro_sql(query: str, params: tuple = ()) -> list[tuple]:
    con = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro&immutable=1", uri=True)
    try:
        return con.execute(query, params).fetchall()
    finally:
        con.close()


@pytest.fixture(scope="module")
def real_hub():
    if not DB_PATH.exists():
        pytest.skip("real snapshot absent")
    with QCH.open(str(DB_PATH)) as h:
        yield h


@real_db
def test_real_3616dbf_wrong_predecessors_plan_is_repaired_to_successors(schema, real_hub):
    """The exact Phase 2D.3 real-model miss, as a plan-semantics test
    (independent of whether the traversal has rows)."""
    assert resolve_version_identifier(real_hub, "3616dbf").canonical_version_id == "ecdsafail:897dda2"
    result = _check(SemanticGuard(schema), real_hub, "Which versions branched from 3616dbf?", "ecdsafail:897dda2", "predecessors")
    assert _repaired_direction(result) == "successors"
    assert result.repair_actions[0].details["raw_question_identifier"] == "3616dbf"


@real_db
@pytest.mark.parametrize("alias", ["ecdsafail:897dda2", "897dda2", "3616dbf", "3616dbf2-f32d-41c8-9ea2-7a5bbe372fc0", "ecdsafail:3616dbf2-f32d-41c8-9ea2-7a5bbe372fc0"])
def test_real_all_alias_forms_of_897dda2_get_the_same_interpretation(schema, real_hub, alias):
    result = _check(SemanticGuard(schema), real_hub, f"Which versions branched from {alias}?", "ecdsafail:897dda2", "predecessors")
    assert _repaired_direction(result) == "successors"


@real_db
def test_real_b6f2b0a_branch_question_is_not_repaired(schema, real_hub):
    result = _check(SemanticGuard(schema), real_hub, "Which versions branched from b6f2b0a?", "ecdsafail:897dda2", "predecessors")
    assert result.outcome != SemanticOutcome.SAFE_REPAIR


@real_db
def test_real_nonempty_branch_case_v5_repair_changes_the_result_set(real_hub):
    """V5 (ecdsafail:a39e07e, submission 3d03172f-...) is the real parent
    with the most branched_from children (found by read-only SQL, not
    manufactured). A planner that uses the wrong direction on the
    submission-prefix alias gets repaired, and the repaired traversal
    returns exactly the SQL ground-truth children -- observably different
    from the wrong direction's result."""
    children = {k for (k,) in _ro_sql(
        "SELECT b.external_version_key FROM transformation_edge te "
        "JOIN circuit_version a ON a.version_id = te.source_version_id "
        "JOIN circuit_version b ON b.version_id = te.target_version_id "
        "WHERE te.relation_type = 'branched_from' AND a.external_version_key = 'ecdsafail:a39e07e'"
    )}
    parents = _ro_sql(
        "SELECT count(*) FROM transformation_edge te JOIN circuit_version b ON b.version_id = te.target_version_id "
        "WHERE te.relation_type = 'branched_from' AND b.external_version_key = 'ecdsafail:a39e07e'"
    )[0][0]
    assert len(children) >= 1 and len(children) != parents
    assert resolve_version_identifier(real_hub, "3d03172").canonical_version_id == "ecdsafail:a39e07e"

    svc = QCHNaturalLanguageService(_Scripted(_wrong_plan("ecdsafail:a39e07e", "predecessors")), real_hub)
    result = svc.ask("Which versions branched from 3d03172?")
    assert result.status == SystemStatus.ANSWERED
    assert result.explanation.canonical_plan["steps"][0]["params"]["direction"] == "successors"
    rows = result.raw_result["data"]
    returned = {row.get("external_version_key") for row in rows}
    assert returned == children


@real_db
def test_real_nonempty_reverse_case_repairs_to_predecessors(real_hub):
    """ecdsafail:3696a8a (submission cf39122d-...) branched from
    ecdsafail:9700396 -- verified by read-only SQL."""
    parents = {k for (k,) in _ro_sql(
        "SELECT a.external_version_key FROM transformation_edge te "
        "JOIN circuit_version a ON a.version_id = te.source_version_id "
        "JOIN circuit_version b ON b.version_id = te.target_version_id "
        "WHERE te.relation_type = 'branched_from' AND b.external_version_key = 'ecdsafail:3696a8a'"
    )}
    assert parents == {"ecdsafail:9700396"}
    svc = QCHNaturalLanguageService(_Scripted(_wrong_plan("ecdsafail:3696a8a", "successors")), real_hub)
    result = svc.ask("What did cf39122 branch from?")
    assert result.explanation.canonical_plan["steps"][0]["params"]["direction"] == "predecessors"
    assert {row.get("external_version_key") for row in result.raw_result["data"]} == parents
