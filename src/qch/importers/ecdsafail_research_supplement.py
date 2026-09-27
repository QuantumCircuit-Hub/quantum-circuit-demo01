"""QCH schema v6 importer: two narrow facts from the ECDSA.Fail paper's
pinned supplemental materials (jieyilong/ecdsafail-supplemental-materials @
ef7ecd2...), already downloaded to the git-ignored staging area and listed
with SHA-256 in data/external/ecdsafail/research_supplement/SOURCE_MANIFEST.json.

1. Supplemental Note C (appendices/tex/appendix-C-content.tex): the authors'
   single PRIMARY optimization-family label (and change type) for each of
   404 accepted source commits -> `version_technique_label`, linked to the
   existing promoted CircuitVersion and its incoming next_promoted_commit
   edge. An interpretive author classification, NOT proof that the technique
   caused the whole score change. Scores/deltas are copied verbatim; the four
   unscored bootstrap rows keep NULL score and delta (never manufactured).
2. The frontier-reconstruction curation file
   (frontier_reconstruction/data/curation/reconstruction-50pct.json) and the
   pinned 27 July API response it names, as TWO distinct submission-scoped
   populations (schema v7 correction, importer 1.1):
   - `paper_admitted_candidates_2026_07_26` -- the INPUT of the paper's Pareto
     computation (what the authors call the "paper-admitted set"): every
     promoted row of the pinned response created at or before the data
     cutoff, plus the six rejected IDs the authors selected (414 rows);
   - `paper_table2_pareto_points_2026_07_26` -- the eight Pareto points the
     paper publishes in Table 2, i.e. the nondominated (Q, T) points of that
     candidate set. v6 stored this population as `paper_admitted_2026_07_26`
     with the candidate set's rule text; 1.1 corrects that row in place
     (same population_id, same eight membership rows).
   Both are editorial selections for this paper and cutoff, NOT a general
   admission rule and NOT platform verification evidence.

Design (same as the contributor importer): verify pinned bytes -> parse ->
plan against the hub API (every identifier resolved exactly; anything
unexpected is a BLOCKING issue) -> apply in ONE transaction via
hub.research. Idempotent: a second run writes nothing; a conflicting
re-import raises instead of overwriting. No network, no git, no code run.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from qch import QCH
from qch.models import PopulationMembership, ResearchPopulation, ResearchSource, TechniqueLabel

IMPORTER_NAME = "qch.importers.ecdsafail_research_supplement"
IMPORTER_VERSION = "1.1"  # 1.1: candidate set and published frontier as separate populations

REPOSITORY_URL = "https://github.com/jieyilong/ecdsafail-supplemental-materials"
REPOSITORY_COMMIT = "ef7ecd2b27cf27881874468e003e53e16ba48075"
NOTE_C_PATH = "appendices/tex/appendix-C-content.tex"
CURATION_PATH = "frontier_reconstruction/data/curation/reconstruction-50pct.json"
PARETO_MANIFEST_PATH = "frontier_reconstruction/output/pareto_manifest.csv"  # identifier cross-check only
RAW_RESPONSE_PATH = "frontier_reconstruction/data/raw/official_submissions_2026-07-27T161558Z.json"  # rows of the candidate set
LICENSE_NOTICE = (
    "No license declared by the source repository (GitHub reports none; no LICENSE file). Imported as attributed, "
    "source-linked factual annotations under the PI's approval of 2026-09-27; confirm reuse terms with the authors "
    "before redistributing a database that contains these rows."
)

LEGACY_POPULATION_NAME = "paper_admitted_2026_07_26"  # v6 name of the eight-point population; never reused
FRONTIER_POPULATION_NAME = "paper_table2_pareto_points_2026_07_26"
CANDIDATE_POPULATION_NAME = "paper_admitted_candidates_2026_07_26"
POPULATION_NAME = FRONTIER_POPULATION_NAME
FRONTIER_POPULATION_SEMANTICS = (
    "The eight Pareto points published in Table 2 (tab:admitted-pareto, 'Archive-conditioned Pareto set at the data cutoff') "
    "of the ECDSA.Fail paper (arXiv:2609.09582v2) at the 2026-07-26T09:21:55.494Z data cutoff: the nondominated points, "
    "minimising official peak qubits Q and average executed Toffoli T, of the candidate population "
    "paper_admitted_candidates_2026_07_26. A published result for this paper and cutoff, not an admission rule: six members "
    "are rejected submissions selected by the authors and two are promoted. Not platform verification evidence."
)
FRONTIER_RULE_TEXT = (
    "Members are exactly the submission IDs listed in admission_policy.paper_admitted_pareto_ids_q_ascending: the Pareto set "
    "of paper_admitted_candidates_2026_07_26 (dominance: <= on both Q and T and < on at least one; for duplicate (Q, T) "
    "coordinates the source retains the earliest created row)."
)
CANDIDATE_POPULATION_SEMANTICS = (
    "The input (candidate) population of the ECDSA.Fail paper's Pareto computation at the 2026-07-26T09:21:55.494Z data "
    "cutoff (arXiv:2609.09582v2, Table 2 / Figure 7), which the authors call the 'paper-admitted set': every row of their "
    "pinned 27 July 2026 API response with promotionStatus 'promoted' and createdAt at or before the cutoff, plus the six "
    "rejected submission IDs the authors selected for the paper. An editorial selection for this paper and cutoff -- the "
    "source states it 'does not derive a general exclusion rule for every archived row'. Not a general admission rule, not "
    "platform verification evidence, and not the frontier (see paper_table2_pareto_points_2026_07_26)."
)
CLASSIFICATION_STATUS = "author_final_primary_label; interpretive coding; classification workbench not publicly deposited"

# Pinned-source expectations (these bytes are fixed by SHA-256; see the source audit)
EXPECTED_NOTE_C_FAMILIES = {
    "Dead-Code and Redundancy Elimination": 244, "Register and Scratch Allocation": 50, "Uncompute Strategy": 22,
    "Constant Propagation and Classical Specialization": 19, "Verifier Search": 19, "Strength Reduction": 14, "Fusion": 12,
    "Windowing": 8, "Codec / Encoding": 7, "Unscored bootstrap (DUMMY_TOFFOLIS)": 4, "Neutral / Mechanical": 3,
    "Algorithmic Substitution": 2,
}
UNSCORED_FAMILY = "Unscored bootstrap (DUMMY_TOFFOLIS)"
EXPECTED_ROWS = 404
EXPECTED_LABELLED_EDGES = 403
EXPECTED_DELTA_PAIRS = 399
EXPECTED_POPULATION_SIZE = 8  # published Table 2 frontier points
EXPECTED_CURATED_REJECTED = 6
EXPECTED_CANDIDATE_PROMOTED = 408  # promoted rows of the pinned response created at or before the cutoff
EXPECTED_CANDIDATES = EXPECTED_CANDIDATE_PROMOTED + EXPECTED_CURATED_REJECTED

_HEX7 = re.compile(r"^[0-9a-f]{7}$")
_HEX40 = re.compile(r"^[0-9a-f]{40}$")


class ResearchImportBlocked(Exception):
    """Pinned-source or join validation failed; nothing was written."""

    def __init__(self, issues: list[str]):
        self.issues = issues
        super().__init__(f"{len(issues)} blocking issue(s); nothing was written. First: {issues[:3]}")


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _h(*parts: str) -> str:
    return hashlib.sha256(":".join(parts).encode("utf-8")).hexdigest()[:32]


def research_source_id(repository_url: str, repository_commit: str, file_path: str) -> str:
    return _h("research_source", repository_url, repository_commit, file_path)


# -- pinned sources ------------------------------------------------------------------------


@dataclass
class PinnedFile:
    repo_path: str
    local_path: Path
    sha256: str
    data: bytes
    retrieved: str | None


def load_pinned_sources(manifest_path: Path, project_root: Path) -> dict[str, PinnedFile]:
    """Reads the four pinned files named in SOURCE_MANIFEST.json and verifies
    each file's SHA-256 against the manifest entry (URL must name the pinned
    commit). Raises ResearchImportBlocked on any mismatch."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = manifest.get("sources", [])
    out, issues = {}, []
    for repo_path in (NOTE_C_PATH, CURATION_PATH, PARETO_MANIFEST_PATH, RAW_RESPONSE_PATH):
        matches = [e for e in entries if e.get("local_path", "").endswith(repo_path) and REPOSITORY_COMMIT in e.get("source_url", "")]
        if len(matches) != 1:
            issues.append(f"manifest: expected exactly one entry for {repo_path} at {REPOSITORY_COMMIT[:7]}, found {len(matches)}")
            continue
        entry = matches[0]
        local = project_root / entry["local_path"]
        if not local.is_file():
            issues.append(f"missing pinned file {entry['local_path']}")
            continue
        data = local.read_bytes()
        actual = hashlib.sha256(data).hexdigest()
        if actual != entry.get("sha256"):
            issues.append(f"sha256 mismatch for {entry['local_path']}: manifest {entry.get('sha256')} actual {actual}")
            continue
        out[repo_path] = PinnedFile(repo_path, local, actual, data, entry.get("retrieved"))
    if issues:
        raise ResearchImportBlocked(issues)
    return out


# -- Supplemental Note C -------------------------------------------------------------------


@dataclass(frozen=True)
class CensusRow:
    row_number: int
    commit: str
    contestant: str
    date: str
    score: int | None
    delta: int | None
    family: str
    change_type: str | None


def _int(cell: str) -> int | None:
    digits = re.sub(r"[^0-9-]", "", cell.replace("{,}", ""))
    return int(digits) if re.search(r"\d", digits) else None


def parse_note_c(text: str) -> list[CensusRow]:
    """Parses the longtable of Supplemental Note C: family headings
    (\\textbf{<family>}~\\small(n)), change-type headings (\\itshape <type>
    \\small(n)) and data rows (\\texttt{<sha7>} & contestant & date & score & delta)."""
    family: str | None = None
    change: str | None = None
    rows: list[CensusRow] = []
    for line in text.splitlines():
        if "\\multicolumn" in line and "Commit" not in line and "continued" not in line:
            m = re.search(r"\\textbf\{(.+?)\}~\\small\((\d+)\)", line)
            if m:
                family = re.sub(r"\\texttt\{([^}]*)\}", r"\1", m.group(1)).replace("\\_", "_").strip()
                change = None
                continue
            m = re.search(r"\\itshape (.+?) \\small\((\d+)\)", line)
            if m:
                change = m.group(1).replace("\\_", "_").strip()
                continue
        m = re.match(r"\s*\\texttt\{([0-9a-f]{7})\}\s*&\s*([^&]*)&\s*([^&]*)&\s*([^&]*)&\s*(.*?)\\\\", line)
        if m:
            unscored = "unscored" in m.group(4) or "unscored" in m.group(5)
            rows.append(CensusRow(
                row_number=len(rows) + 1, commit=m.group(1), contestant=m.group(2).strip().replace("\\_", "_"), date=m.group(3).strip(),
                score=None if unscored else _int(m.group(4)), delta=None if unscored else _int(m.group(5)),
                family=family or "", change_type=change,
            ))
    return rows


@dataclass
class TechniquePlan:
    source: ResearchSource
    labels: list[TechniqueLabel]
    blocking: list[str]
    stats: dict[str, Any] = field(default_factory=dict)
    delta_mismatches: list[dict[str, Any]] = field(default_factory=list)


def plan_technique_labels(hub: QCH, pinned: PinnedFile) -> TechniquePlan:
    rows = parse_note_c(pinned.data.decode("utf-8"))
    blocking: list[str] = []
    source = ResearchSource(
        source_id=research_source_id(REPOSITORY_URL, REPOSITORY_COMMIT, NOTE_C_PATH), source_kind="technique_census",
        repository_url=REPOSITORY_URL, repository_commit=REPOSITORY_COMMIT, file_path=NOTE_C_PATH, sha256=pinned.sha256,
        license_notice=LICENSE_NOTICE,
        description="ECDSA.Fail Supplemental Note C: optimization census of accepted competition source commits (404 rows, primary family + change type).",
        retrieved_at=pinned.retrieved or "", importer_name=IMPORTER_NAME, importer_version=IMPORTER_VERSION, imported_at=_now_utc(),
    )

    # -- source-level checks
    commits = [r.commit for r in rows]
    if len(rows) != EXPECTED_ROWS or len(set(commits)) != EXPECTED_ROWS:
        blocking.append(f"Note C: expected {EXPECTED_ROWS} unique commits, parsed {len(rows)} rows / {len(set(commits))} unique")
    families = Counter(r.family for r in rows)
    if dict(families) != EXPECTED_NOTE_C_FAMILIES:
        blocking.append(f"Note C family counts differ from the source audit: {dict(families)}")
    for r in rows:
        if not _HEX7.match(r.commit) or not r.family:
            blocking.append(f"row {r.row_number}: malformed commit/family {r}")
        if (r.family == UNSCORED_FAMILY) != (r.score is None):
            blocking.append(f"row {r.row_number} {r.commit}: scored/unscored inconsistent with its family ({r.family}, score {r.score})")

    # -- joins through the hub API only
    submissions = {s.submission_id: s for s in hub.submissions.list()}
    labels: list[TechniqueLabel] = []
    labelled_edges = delta_pairs = delta_agree = score_equal = score_compared = full_sha = 0
    mismatches: list[dict[str, Any]] = []
    for r in rows:
        version = hub.versions.find_by_external_key(f"ecdsafail:{r.commit}")
        if version is None:
            blocking.append(f"row {r.row_number}: commit {r.commit} has no QCH CircuitVersion ecdsafail:{r.commit}")
            continue
        sub = submissions.get(version.realized_from_submission_id or "")
        if sub is None or sub.status != "PROMOTED":
            blocking.append(f"row {r.row_number}: ecdsafail:{r.commit} is not realized from a PROMOTED submission ({sub.status if sub else None})")
            continue
        promoted = [c.commit_sha for c in hub.submissions.list_source_commits(sub.submission_id, role="promoted")]
        if promoted != [r.commit]:
            blocking.append(f"row {r.row_number}: promoted source commit of {sub.external_submission_key} is {promoted}, expected [{r.commit}]")
        evaluation = hub.evaluations.latest(sub.submission_id)
        ref = (evaluation.promoted_source_ref if evaluation else None) or ""
        resolved = None
        if ref:
            if not (_HEX40.match(ref) and ref.startswith(r.commit)):
                blocking.append(f"row {r.row_number}: promoted_source_ref {ref!r} does not extend {r.commit}")
            else:
                resolved = ref
                full_sha += 1
        incoming = [e for e in hub.evolution.list_edges_to(version.version_id) if e.relation_type == "next_promoted_commit"]
        if len(incoming) > 1:
            blocking.append(f"row {r.row_number}: {len(incoming)} incoming next_promoted_commit edges into ecdsafail:{r.commit}")
        edge = incoming[0] if incoming else None
        if edge is not None:
            labelled_edges += 1
            if r.delta is not None:
                src_sub = submissions.get(hub.versions.get(edge.source_version_id).realized_from_submission_id or "")
                s_src = hub.evaluations.latest(src_sub.submission_id).official_score if src_sub and hub.evaluations.latest(src_sub.submission_id) else None
                s_tgt = evaluation.official_score if evaluation else None
                if s_src is not None and s_tgt is not None:
                    delta_pairs += 1
                    if r.delta == s_tgt - s_src:
                        delta_agree += 1
                    else:
                        mismatches.append({"row": r.row_number, "commit": r.commit, "source_delta": r.delta, "qch_delta": s_tgt - s_src})
        if r.score is not None and evaluation and evaluation.official_score is not None:
            score_compared += 1
            score_equal += int(r.score == evaluation.official_score)
        labels.append(TechniqueLabel(
            label_id=_h("technique_label", source.source_id, version.version_id), source_id=source.source_id, version_id=version.version_id,
            source_row_number=r.row_number, source_commit_ref=r.commit, family=r.family, classification_status=CLASSIFICATION_STATUS,
            source_scored=r.score is not None, incoming_edge_id=edge.edge_id if edge else None, resolved_commit_sha=resolved,
            change_type=r.change_type, source_contestant=r.contestant or None, source_date=r.date or None,
            source_score=r.score, source_frontier_delta=r.delta,
        ))
    if labelled_edges != EXPECTED_LABELLED_EDGES:
        blocking.append(f"labelled next_promoted_commit edges: expected {EXPECTED_LABELLED_EDGES}, found {labelled_edges}")
    if delta_pairs != EXPECTED_DELTA_PAIRS or mismatches:
        blocking.append(f"delta check: {delta_agree}/{delta_pairs} labelled edges have source delta == S(target) - S(source) "
                        f"(expected {EXPECTED_DELTA_PAIRS}/{EXPECTED_DELTA_PAIRS}); mismatches {mismatches[:5]}")
    stats = {"rows": len(rows), "unique_commits": len(set(commits)), "families": dict(families), "labels": len(labels),
             "scored_rows": sum(1 for r in rows if r.score is not None), "unscored_rows": sum(1 for r in rows if r.score is None),
             "rows_with_change_type": sum(1 for r in rows if r.change_type), "labels_with_full_commit_sha": full_sha,
             "labelled_next_promoted_commit_edges": labelled_edges, "delta_pairs": delta_pairs, "delta_agree": delta_agree,
             "source_score_compared_to_official_S": score_compared, "source_score_equals_official_S": score_equal}
    return TechniquePlan(source, labels, blocking, stats, mismatches)


# -- paper populations: candidate set and published Table 2 frontier ------------------------


@dataclass
class PopulationPlan:
    source: ResearchSource
    entries: list[tuple[ResearchPopulation, list[PopulationMembership]]]  # candidate set first
    renames: dict[str, str]  # population_id -> expected former name (the v6 row)
    blocking: list[str]
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def population(self) -> ResearchPopulation:  # the published frontier
        return self.entries[-1][0]

    @property
    def memberships(self) -> list[PopulationMembership]:
        return self.entries[-1][1]


def pareto_frontier(points: list[tuple[str, int, int]]) -> list[str]:
    """Keys of the nondominated (key, q, t) points, minimising both q and t,
    ordered by (q, t, key). b dominates a iff b.q <= a.q and b.t <= a.t with
    at least one strict; ties (identical coordinates) are kept."""
    return [k for k, q, t in sorted(points, key=lambda p: (p[1], p[2], p[0]))
            if not any(bq <= q and bt <= t and (bq < q or bt < t) for _, bq, bt in points)]


def _iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def plan_paper_populations(hub: QCH, curation: PinnedFile, pareto_manifest: PinnedFile, raw_response: PinnedFile) -> PopulationPlan:
    """Derives the candidate set exactly from the pinned response + curation
    rules, cross-checks every member against QCH (UUID, createdAt, status,
    Q/T) and an independent QCH-side derivation, recomputes the Pareto
    frontier and requires it to equal the eight listed Table 2 IDs. Any
    mismatch or ambiguity is a BLOCKING issue."""
    blocking: list[str] = []
    cur = json.loads(curation.data.decode("utf-8"))
    policy = cur.get("admission_policy", {})
    publication = cur.get("publication_policy", {})
    listed = list(policy.get("paper_admitted_pareto_ids_q_ascending", []))
    curated_rejected = list(policy.get("paper_admitted_curated_rejected_ids", []))
    rule_text = policy.get("paper_admitted_rule") or ""
    cutoff_at = cur.get("data_cutoff_at") or ""
    crossing = cur.get("data_cutoff", {})
    raw_meta = cur.get("sources", {}).get("official_submissions", {})
    if len(listed) != EXPECTED_POPULATION_SIZE or len(set(listed)) != EXPECTED_POPULATION_SIZE:
        blocking.append(f"curation: expected {EXPECTED_POPULATION_SIZE} unique paper_admitted_pareto_ids, found {listed}")
    if len(set(curated_rejected)) != EXPECTED_CURATED_REJECTED or not set(curated_rejected) <= set(listed):
        blocking.append(f"curation: expected {EXPECTED_CURATED_REJECTED} curated rejected IDs within the listed frontier, found {curated_rejected}")
    if not rule_text or not cutoff_at:
        blocking.append("curation: admission_policy.paper_admitted_rule or data_cutoff_at is missing")
    if raw_meta.get("sha256") != raw_response.sha256 or not RAW_RESPONSE_PATH.endswith(raw_meta.get("path", "\0")):
        blocking.append(f"curation names raw response {raw_meta.get('path')} sha256 {raw_meta.get('sha256')}, pinned file is {raw_response.sha256}")
    manifest_rows = list(csv.DictReader(io.StringIO(pareto_manifest.data.decode("utf-8"))))
    manifest_uuid = {row["submission"]: row["submission_uuid"] for row in manifest_rows}
    if [row["submission"] for row in manifest_rows] != listed:
        blocking.append("pareto_manifest submissions differ from admission_policy.paper_admitted_pareto_ids_q_ascending")

    # 1. candidate set from the pinned response (the source's own population definition)
    rows = json.loads(raw_response.data.decode("utf-8")).get("submissions", [])
    if len(rows) != raw_meta.get("expected_rows"):
        blocking.append(f"raw response has {len(rows)} rows, curation expects {raw_meta.get('expected_rows')}")
    cut = _iso(cutoff_at)
    by_ref: dict[str, list[dict]] = {}
    for row in rows:
        by_ref.setdefault(row["id"][:7], []).append(row)

    def has_metrics(row: dict) -> bool:
        metrics = row.get("officialMetrics") or {}
        return bool(metrics.get("qubits")) and bool(metrics.get("toffoli"))

    pre_cut = [row for row in rows if _iso(row["createdAt"]) <= cut]
    promoted_rows = [row for row in pre_cut if row.get("promotionStatus") == "promoted"]
    if any(not has_metrics(row) for row in promoted_rows):
        blocking.append("ambiguity: a promoted pre-cutoff row has no official Q/T; the source text does not say whether it is a candidate")
    crossing_created = crossing.get("created_at")
    in_gap = [row["id"][:7] for row in rows if crossing_created and _iso(crossing_created) < _iso(row["createdAt"]) <= cut]
    if in_gap:
        blocking.append(f"ambiguity: rows created between the crossing's createdAt and the cutoff (inclusion depends on reading): {in_gap}")
    candidates: list[tuple[dict, str, str]] = [(row, "promoted", "sources.official_submissions[promotionStatus='promoted', createdAt<=data_cutoff_at]")
                                               for row in promoted_rows]
    for ref in curated_rejected:
        matches = by_ref.get(ref, [])
        if len(matches) != 1:
            blocking.append(f"curated rejected ID {ref}: {len(matches)} rows of the pinned response start with it (need exactly 1)")
            continue
        row = matches[0]
        if row.get("status") != "rejected" or _iso(row["createdAt"]) > cut or not has_metrics(row):
            blocking.append(f"curated rejected ID {ref}: status {row.get('status')}, createdAt {row['createdAt']}, metrics {has_metrics(row)}")
        candidates.append((row, "curated_rejected", "admission_policy.paper_admitted_curated_rejected_ids"))
    if len({row["id"] for row, _, _ in candidates}) != len(candidates):
        blocking.append("candidate set has a duplicate submission")
    if len(promoted_rows) != EXPECTED_CANDIDATE_PROMOTED or len(candidates) != EXPECTED_CANDIDATES:
        blocking.append(f"candidate counts: {len(promoted_rows)} promoted + {len(candidates) - len(promoted_rows)} curated, "
                        f"expected {EXPECTED_CANDIDATE_PROMOTED} + {EXPECTED_CURATED_REJECTED}")

    # 2. every candidate must be the same fact in QCH; QCH-side derivation must agree
    subs = {s.external_submission_key.split(":", 1)[1]: s for s in hub.submissions.list() if s.external_submission_key}
    evals = hub.evaluations.latest_all()
    source_id = research_source_id(REPOSITORY_URL, REPOSITORY_COMMIT, CURATION_PATH)
    candidate_id = _h("research_population", CANDIDATE_POPULATION_NAME)
    frontier_id = _h("research_population", LEGACY_POPULATION_NAME)  # kept from v6 so its membership rows are untouched
    candidate_members: list[PopulationMembership] = []
    points: list[tuple[str, int, int]] = []
    created: dict[str, str] = {}
    for row, role, list_field in candidates:
        uuid, ref = row["id"], row["id"][:7]
        sub = subs.get(uuid)
        ev = evals.get(sub.submission_id) if sub else None
        if sub is None or ev is None:
            blocking.append(f"candidate {uuid}: no QCH submission/evaluation with this full UUID")
            continue
        metrics = row["officialMetrics"]
        want = (row.get("status"), row.get("promotionStatus"), int(metrics["qubits"]), int(metrics["toffoli"]), row["createdAt"])
        have = (ev.platform_status, ev.promotion_status, ev.official_peak_qubits, ev.official_avg_executed_toffoli, ev.platform_created_at)
        if want != have:
            blocking.append(f"candidate {ref}: pinned response {want} != QCH {have}")
        if ev.platform_status != "accepted" and role != "curated_rejected":
            blocking.append(f"candidate {ref}: platform_status {ev.platform_status} but not one of the six curated rejected IDs")
        candidate_members.append(PopulationMembership(candidate_id, sub.submission_id, ref, list_field, role))
        points.append((ref, ev.official_peak_qubits, ev.official_avg_executed_toffoli))
        created[ref] = ev.platform_created_at
    qch_promoted = {e.source_submission_uuid for e in evals.values() if e.promotion_status == "promoted" and e.platform_created_at
                    and _iso(e.platform_created_at) <= cut and e.official_peak_qubits and e.official_avg_executed_toffoli}
    if qch_promoted != {row["id"] for row in promoted_rows}:
        blocking.append(f"QCH-side derivation disagrees: QCH-only {sorted(qch_promoted - {r['id'] for r in promoted_rows})[:5]}, "
                        f"response-only {sorted({r['id'] for r in promoted_rows} - qch_promoted)[:5]}")

    # 3. independent Pareto recomputation must reproduce the eight published IDs
    coords: dict[tuple[int, int], list[str]] = {}
    for ref, q, t in points:
        coords.setdefault((q, t), []).append(ref)
    duplicates = {f"{q},{t}": refs for (q, t), refs in coords.items() if len(refs) > 1}
    if duplicates:
        blocking.append(f"ambiguity: duplicate (Q, T) coordinates in the candidate set {duplicates}; tie-keeping and the source's earliest-row rule may differ")
    computed = pareto_frontier(points)
    if computed != listed:
        blocking.append(f"computed Pareto frontier {computed} != listed Table 2 IDs {listed}")

    # 4. rows
    source = ResearchSource(
        source_id=source_id, source_kind="population_curation",
        repository_url=REPOSITORY_URL, repository_commit=REPOSITORY_COMMIT, file_path=CURATION_PATH, sha256=curation.sha256,
        license_notice=LICENSE_NOTICE,
        description="ECDSA.Fail frontier-reconstruction selection manifest (data cutoff, admission rules, paper-admitted Pareto IDs).",
        retrieved_at=curation.retrieved or "", importer_name=IMPORTER_NAME, importer_version=IMPORTER_VERSION, imported_at=_now_utc(),
        auxiliary={"identifier_crosscheck": {"file_path": PARETO_MANIFEST_PATH, "sha256": pareto_manifest.sha256,
                                             "use": "full submission UUIDs of the eight admitted points; resolution cross-check only"},
                   "primary_rule": policy.get("primary_rule")},
    )
    candidate = ResearchPopulation(
        population_id=candidate_id, name=CANDIDATE_POPULATION_NAME, source_id=source_id, semantics=CANDIDATE_POPULATION_SEMANTICS,
        rule_text=rule_text, cutoff_at=cutoff_at, population_role="candidate_set", rule_source_field="admission_policy.paper_admitted_rule",
        provenance={
            "rows_from": {"repository_url": REPOSITORY_URL, "repository_commit": REPOSITORY_COMMIT, "file_path": RAW_RESPONSE_PATH,
                          "sha256": raw_response.sha256, "endpoint": raw_meta.get("endpoint"), "fetched_at": raw_meta.get("fetched_at")},
            "population_definition_verbatim": {"field": "publication_policy.population_definition", "text": publication.get("population_definition")},
            "primary_rule_verbatim": {"field": "admission_policy.primary_rule", "text": policy.get("primary_rule")},
            "deduplication_rule_verbatim": {"field": "admission_policy.deduplication", "text": policy.get("deduplication")},
            "derivation": {
                "promoted": "rows of the pinned response with promotionStatus == 'promoted' and createdAt <= data_cutoff_at (inclusive)",
                "curated_rejected": "the six IDs in admission_policy.paper_admitted_curated_rejected_ids; each verified rejected, created <= cutoff, with Q/T",
                "cutoff_reading": "no row of the pinned response was created between the crossing submission's createdAt and data_cutoff_at, "
                                  "so reading the cutoff as either instant yields the same set",
                "metric_filter": "every promoted pre-cutoff row carries official Q/T; the metric requirement removes none",
            },
            "counts": {"promoted": len(promoted_rows), "curated_rejected": len(candidates) - len(promoted_rows), "total": len(candidates)},
            "candidate_count_stated_in_source": None,
            "qch_crosscheck": "every member matches QCH by full UUID with equal status, promotion status, createdAt, Q and T; "
                              "QCH's own promoted/created<=cutoff/metric-bearing set equals the promoted part",
            "not": ["a general admission rule for other dates", "platform verification evidence", "the Pareto frontier"],
        },
    )
    frontier = ResearchPopulation(
        population_id=frontier_id, name=FRONTIER_POPULATION_NAME, source_id=source_id, semantics=FRONTIER_POPULATION_SEMANTICS,
        rule_text=FRONTIER_RULE_TEXT, cutoff_at=cutoff_at, population_role="published_pareto_frontier",
        derived_from_population_id=candidate_id, rule_source_field=None,
        provenance={
            "published_in": "arXiv:2609.09582v2 Table 2 (\\label{tab:admitted-pareto}, sections/05-results.tex)",
            "listed_by": {"file_path": CURATION_PATH, "field": "admission_policy.paper_admitted_pareto_ids_q_ascending"},
            "identifier_crosscheck": {"file_path": PARETO_MANIFEST_PATH, "sha256": pareto_manifest.sha256},
            "former_names": [LEGACY_POPULATION_NAME],
            "population_id_note": "population_id is the v6 value (hash of the former name), kept so the eight membership rows are unchanged",
            "correction": "2026-09-27 (importer 1.1): v6 stored the candidate set's rule as this population's rule_text",
            "candidate_set_rule_verbatim": {"field": "admission_policy.paper_admitted_rule", "text": rule_text,
                                            "applies_to": CANDIDATE_POPULATION_NAME, "note": "describes the candidate population, NOT this population's membership"},
            "frontier_check": {"objectives": ["official_peak_qubits", "official_avg_executed_toffoli"], "sense": "minimise both",
                               "dominance": "<= on both and < on at least one; ties kept", "candidate_count": len(points),
                               "computed_ids_q_ascending": computed, "equals_listed_ids": computed == listed},
        },
    )
    frontier_members: list[PopulationMembership] = []
    for ref in listed:
        uuid = manifest_uuid.get(ref)
        sub = subs.get(uuid or "")
        prefixed = [u for u in subs if u.startswith(ref)]
        if sub is None or len(prefixed) != 1:
            blocking.append(f"frontier ref {ref}: manifest UUID {uuid} does not resolve to exactly one QCH submission")
            continue
        role = "curated_rejected" if ref in curated_rejected else "promoted"
        frontier_members.append(PopulationMembership(frontier_id, sub.submission_id, ref, "admission_policy.paper_admitted_pareto_ids_q_ascending", role))
    if not {m.submission_id for m in frontier_members} <= {m.submission_id for m in candidate_members}:
        blocking.append("a published frontier point is not in the candidate set")
    stats = {
        "candidate_population": CANDIDATE_POPULATION_NAME, "candidates": len(candidate_members),
        "candidate_promoted": sum(m.member_role == "promoted" for m in candidate_members),
        "candidate_curated_rejected": sum(m.member_role == "curated_rejected" for m in candidate_members),
        "frontier_population": FRONTIER_POPULATION_NAME, "members": len(frontier_members),
        "curated_rejected": sum(m.member_role == "curated_rejected" for m in frontier_members),
        "promoted": sum(m.member_role == "promoted" for m in frontier_members),
        "computed_frontier_ids": computed, "computed_frontier_count": len(computed), "listed_ids": listed,
        "computed_equals_listed": computed == listed, "duplicate_coordinates": duplicates,
        "frontier_created_at": {ref: created.get(ref) for ref in computed}, "cutoff_at": cutoff_at,
    }
    return PopulationPlan(source, [(candidate, candidate_members), (frontier, frontier_members)], {frontier_id: LEGACY_POPULATION_NAME}, blocking, stats)


# -- apply -----------------------------------------------------------------------------------


@dataclass
class ResearchImportResult:
    technique_labels_written: bool
    population_written: bool
    technique_stats: dict[str, Any]
    population_stats: dict[str, Any]


def apply_research_supplement_import(hub: QCH, technique: TechniquePlan, population: PopulationPlan) -> ResearchImportResult:
    """Refuses to write anything if either plan has a blocking issue. Each
    source is written in its own all-or-nothing transaction (labels first);
    both are validated before either is written."""
    issues = technique.blocking + population.blocking
    if issues:
        raise ResearchImportBlocked(issues)
    wrote_labels = hub.research.record_import(technique.source, labels=technique.labels)
    wrote_population = hub.research.record_populations(population.source, population.entries, population.renames)
    return ResearchImportResult(wrote_labels, wrote_population, technique.stats, population.stats)
