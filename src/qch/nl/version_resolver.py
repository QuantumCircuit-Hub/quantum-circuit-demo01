"""QCH Phase 2D.1: deterministic version-identity resolution.

Real QCH data gives every CircuitVersion exactly one PRIMARY, always-
present, stable identity: `external_version_key` (e.g.
"ecdsafail:6f7c159"). `version_label` is a SECONDARY, OPTIONAL alias
set only for a handful of curated milestones ("V1".."V5" in the real
ECDSA.Fail dataset); most of the 781 real versions have none. See
docs/QCH_VERSION_IDENTITY_PHASE2D1.md for the full audit this module
implements.

This module is the ONE place a raw, user-supplied or LLM-proposed
version reference (however it was spelled -- a full canonical key, a
short/partial hex suffix, an internal version_id, or a milestone
label) is turned into a definitive, DB-grounded outcome:

    RESOLVED   -- exactly one real version matches; its canonical
                  `external_version_key` is returned.
    UNKNOWN    -- genuinely no real version matches (never guessed).
    AMBIGUOUS  -- more than one real version's `external_version_key`
                  shares this short form; the real candidates are
                  returned so a caller can ask, never silently pick
                  one.

Deliberately narrow (spec section 12): no LLM call, no Git, no
artifact extraction -- only the already-open `hub`'s existing
`versions`/`circuits` services plus a cheap in-memory scan (781 rows
in the current real dataset). Reuses `qch.query.resolve.resolve_version`
(the SAME exact-match logic every query operator already depends on)
for the exact-match tier rather than re-implementing it, and adds
short-ID matching as a new, additive tier on top -- it never changes
what `resolve_version` itself does, so no frozen query-engine file is
touched.

Phase 2D.3 (multi-namespace resolution, see
docs/QCH_MULTI_NAMESPACE_RESOLUTION_PHASE2D3.md) extends this SAME
resolver -- not a competing one -- to the Submission identity QCH
already stores (`submission.external_submission_key`, linked to a
version via `circuit_version.realized_from_submission_id`). The Phase
2D.2 audit proved that paper-style IDs such as "8e9c9a2" are
submission-UUID prefixes, not Git SHAs, so a bare hex token is now
searched in BOTH the commit namespace and the submission namespace and
never assumed to be either. One new outcome is added:

    KNOWN_SUBMISSION_NO_VERSION -- the identifier names a real
                  Submission that has no CircuitVersion under the
                  existing eligibility rules. Never UNKNOWN (QCH does
                  know it), never RESOLVED (there is no version to
                  execute against, and none is ever fabricated).

Collision rule: every match is reduced to the underlying entity it
names (a CircuitVersion, or a Submission with no version). Matches
that converge on ONE entity resolve; matches naming two or more
distinct entities are AMBIGUOUS -- never "first match wins", never
DB row order.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

from qch.query.models import QCHQueryPlan, QCHQueryStep
from qch.query.resolve import resolve_version

if TYPE_CHECKING:
    from qch.hub import QCH
    from qch.models import CircuitVersion, Submission


class VersionResolutionOutcome(str, Enum):
    RESOLVED = "resolved"
    UNKNOWN = "unknown"
    AMBIGUOUS = "ambiguous"
    KNOWN_SUBMISSION_NO_VERSION = "known_submission_no_version"


class IdentityNamespace(str, Enum):
    """Which stored identity an input matched -- kept on every
    resolution so provenance never collapses into an unlabeled string
    (Phase 2D.3 spec section 16)."""

    VERSION_ID = "version_id"
    EXTERNAL_VERSION_KEY = "external_version_key"
    VERSION_LABEL = "version_label"
    COMMIT_PREFIX = "commit_prefix"  # bare hex prefix of an external_version_key's commit suffix
    SUBMISSION_EXTERNAL_KEY = "submission_external_key"
    SUBMISSION_UUID = "submission_uuid"
    SUBMISSION_UUID_PREFIX = "submission_uuid_prefix"


# A bare hex-shaped token must contain at least one a-f letter to be
# treated as identifier-shaped -- otherwise a plain decimal number used
# as a metric threshold (e.g. "950000" in "below 950000") would be
# mistaken for a version short ID. Real external_version_key hex
# suffixes are 7 characters, but this deliberately accepts shorter
# prefixes too (down to 4) since "unique short ID" (spec section 3)
# means DB-grounded prefix matching, not a fixed length.
_MIN_SHORT_ID_LEN = 4
_HEX_WITH_LETTER_RE = re.compile(r"^(?=[0-9a-f]*[a-f])[0-9a-f]+$")

_EXTERNAL_KEY_PREFIX = "ecdsafail:"


# Phase 2D.3: a submission-UUID prefix may include the UUID's own
# hyphens ("8e9c9a22-a6ec"), but is otherwise held to the SAME policy
# as a commit prefix: at least `_MIN_SHORT_ID_LEN` hex digits and at
# least one a-f letter (so a decimal threshold is never an identifier).
_UUID_PREFIX_SHAPE_RE = re.compile(r"^[0-9a-f][0-9a-f-]*$")


@dataclass(frozen=True)
class VersionResolution:
    """One resolution outcome, plus the minimal observability fields
    the Phase 2D.1 spec (section 13) asks for: `raw_identifier` (what
    was literally supplied), `normalized_identifier` (lowercased, any
    dataset key prefix stripped), `canonical_version_id` (the real
    `external_version_key`, only set when RESOLVED), `version_label`
    (the secondary alias, if the resolved version happens to have
    one), and `resolution_status` (a plain machine-readable code, not
    hidden reasoning).

    Phase 2D.3 adds `matched_namespaces` (every `IdentityNamespace`
    that matched -- more than one only when they all converged on the
    same entity) and, whenever a Submission was matched,
    `submission_external_key`/`submission_status`. For
    KNOWN_SUBMISSION_NO_VERSION, `canonical_version_id` stays None."""

    outcome: VersionResolutionOutcome
    raw_identifier: str
    normalized_identifier: str
    canonical_version_id: str | None = None
    version_label: str | None = None
    internal_version_id: str | None = None
    resolution_status: str = "unknown"
    candidates: tuple[str, ...] = field(default_factory=tuple)
    matched_namespaces: tuple[str, ...] = field(default_factory=tuple)
    submission_external_key: str | None = None
    submission_status: str | None = None

    @property
    def matched_namespace(self) -> str | None:
        return "+".join(self.matched_namespaces) if self.matched_namespaces else None

    @property
    def matched_via_submission(self) -> bool:
        return any(ns.startswith("submission") for ns in self.matched_namespaces)

    @property
    def submission_uuid(self) -> str | None:
        """The bare UUID part of `submission_external_key` (the key's
        stored form is "<source_system>:<uuid>")."""
        key = self.submission_external_key
        if key is None:
            return None
        return key.split(":", 1)[1] if ":" in key else key

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "raw_identifier": self.raw_identifier,
            "normalized_identifier": self.normalized_identifier,
            "matched_namespace": self.matched_namespace,
            "canonical_version_id": self.canonical_version_id,
            "alias_used": self.version_label,
            "submission_external_key": self.submission_external_key,
            "submission_status": self.submission_status,
            "resolution_status": self.resolution_status,
            "candidates": list(self.candidates),
        }


def _normalize(raw_identifier: str) -> str:
    identifier = raw_identifier.strip().lower()
    if identifier.startswith(_EXTERNAL_KEY_PREFIX):
        identifier = identifier[len(_EXTERNAL_KEY_PREFIX):]
    return identifier


def _all_versions(hub: "QCH", logical_circuit_id: str | None) -> list["CircuitVersion"]:
    circuit_ids = [logical_circuit_id] if logical_circuit_id is not None else [c.logical_circuit_id for c in hub.circuits.list()]
    versions: list["CircuitVersion"] = []
    for circuit_id in circuit_ids:
        versions.extend(hub.versions.list(circuit_id))
    return versions


def _submission_uuid_of(submission: "Submission") -> str:
    key = (submission.external_submission_key or "").lower()
    return key.split(":", 1)[1] if ":" in key else key


@dataclass(frozen=True)
class _Match:
    """One namespace hit. `version` is the CircuitVersion the hit
    names (directly, or via the Submission's realized version);
    `submission` is set only for submission-namespace hits."""

    namespace: IdentityNamespace
    version: "CircuitVersion | None"
    submission: "Submission | None" = None

    @property
    def entity_key(self) -> tuple[str, str]:
        """The underlying entity -- what "converge" means for the
        collision rule. A Submission that HAS a version is the same
        entity as that version (so its commit prefix and its UUID prefix
        agree); a Submission without one is its own entity."""
        if self.version is not None:
            return ("version", self.version.version_id)
        return ("submission", self.submission.submission_id)  # type: ignore[union-attr]

    @property
    def candidate_label(self) -> str:
        """A re-resolvable, exact identifier for clarification options:
        the canonical version key, or (no version) the full submission
        external key."""
        if self.version is not None:
            return self.version.external_version_key or self.version.version_id
        return self.submission.external_submission_key or self.submission.submission_id  # type: ignore[union-attr]


class _SubmissionIndex:
    """All submissions plus the ONE CircuitVersion (if any) each
    realized, read from the already-open hub's own services -- no Git,
    no filesystem, no SQL here (see `qch.nl` storage independence)."""

    def __init__(self, hub: "QCH", logical_circuit_id: str | None) -> None:
        self.submissions = hub.submissions.list()
        self._in_scope_ids = {v.version_id for v in _all_versions(hub, logical_circuit_id)}
        self._realized: dict[str, list["CircuitVersion"]] = {}
        for version in _all_versions(hub, None):
            if version.realized_from_submission_id:
                self._realized.setdefault(version.realized_from_submission_id, []).append(version)

    def versions_for(self, submission: "Submission") -> tuple[list["CircuitVersion"], bool]:
        """(in-scope realized versions, whether ANY realized version
        exists at all). A version outside the requested logical circuit
        is not a match here, but it also means the submission is not
        "without a version"."""
        realized = self._realized.get(submission.submission_id, [])
        return [v for v in realized if v.version_id in self._in_scope_ids], bool(realized)


def _submission_matches(index: _SubmissionIndex, submission: "Submission", namespace: IdentityNamespace) -> list[_Match]:
    in_scope, any_realized = index.versions_for(submission)
    if in_scope:
        return [_Match(namespace, version, submission) for version in in_scope]
    if any_realized:
        return []  # realized only in another logical circuit -- out of this lookup's scope
    return [_Match(namespace, None, submission)]


def _exact_matches(hub: "QCH", stripped: str, normalized: str, logical_circuit_id: str | None) -> tuple[list[_Match], str]:
    """Tier 1: complete identifiers only (never a prefix)."""
    matches: list[_Match] = []
    status = "exact_match"

    # Existing Phase 2D.1 behavior, reusing the frozen query resolver:
    # version_id / external_version_key / version_label, then a
    # case-normalized retry.
    version = resolve_version(hub, stripped, logical_circuit_id)
    looked_up = stripped
    if version is None and stripped.lower() != stripped:
        version = resolve_version(hub, stripped.lower(), logical_circuit_id)
        looked_up = stripped.lower()
        status = "exact_match_case_normalized"
    if version is not None:
        if version.version_id == looked_up:
            namespace = IdentityNamespace.VERSION_ID
        elif version.external_version_key == looked_up:
            namespace = IdentityNamespace.EXTERNAL_VERSION_KEY
        else:
            namespace = IdentityNamespace.VERSION_LABEL
        matches.append(_Match(namespace, version))

    # Phase 2D.3: full submission identity. UUIDs compare
    # case-insensitively (standard UUID semantics); stored keys are
    # "<source_system>:<uuid>".
    lowered = stripped.lower()
    if "-" in lowered:  # every stored submission UUID is hyphenated; skip the scan otherwise
        index = _SubmissionIndex(hub, logical_circuit_id)
        for submission in index.submissions:
            key = (submission.external_submission_key or "").lower()
            if key and key == lowered:
                matches.extend(_submission_matches(index, submission, IdentityNamespace.SUBMISSION_EXTERNAL_KEY))
            elif _submission_uuid_of(submission) == normalized and ":" not in normalized:
                matches.extend(_submission_matches(index, submission, IdentityNamespace.SUBMISSION_UUID))
    return matches, status


def _has_min_hex_with_letter(token: str) -> bool:
    hex_only = token.replace("-", "")
    return len(hex_only) >= _MIN_SHORT_ID_LEN and bool(_HEX_WITH_LETTER_RE.match(hex_only))


def _prefix_matches(hub: "QCH", normalized: str, logical_circuit_id: str | None) -> list[_Match]:
    """Tier 2: prefixes, searched in EVERY prefix-capable namespace --
    a hex token is never assumed to be a commit (Phase 2D.2 proved
    paper IDs are submission-UUID prefixes)."""
    matches: list[_Match] = []
    if not _has_min_hex_with_letter(normalized):
        return matches

    if _HEX_WITH_LETTER_RE.match(normalized):
        for candidate in _all_versions(hub, logical_circuit_id):
            key = candidate.external_version_key or ""
            suffix = key.split(":", 1)[1].lower() if ":" in key else key.lower()
            if suffix.startswith(normalized):
                matches.append(_Match(IdentityNamespace.COMMIT_PREFIX, candidate))

    if _UUID_PREFIX_SHAPE_RE.match(normalized):
        index = _SubmissionIndex(hub, logical_circuit_id)
        for submission in index.submissions:
            uuid = _submission_uuid_of(submission)
            if uuid and uuid.startswith(normalized) and uuid != normalized:
                matches.extend(_submission_matches(index, submission, IdentityNamespace.SUBMISSION_UUID_PREFIX))
    return matches


def _decide(raw_identifier: str, normalized: str, matches: list[_Match], single_status: str) -> VersionResolution:
    """Applies the collision rule: converge on one entity -> resolve;
    two or more distinct entities -> AMBIGUOUS. Deterministic
    regardless of match order (namespaces and candidates are sorted)."""
    by_entity: dict[tuple[str, str], list[_Match]] = {}
    for match in matches:
        by_entity.setdefault(match.entity_key, []).append(match)
    namespaces = tuple(sorted({m.namespace.value for m in matches}))

    if len(by_entity) > 1:
        if all(ns == IdentityNamespace.COMMIT_PREFIX.value for ns in namespaces):
            status = "ambiguous_short_id"  # unchanged Phase 2D.1 code
        elif all(ns.startswith("submission") for ns in namespaces):
            status = "ambiguous_submission_prefix"
        else:
            status = "ambiguous_cross_namespace"
        return VersionResolution(
            VersionResolutionOutcome.AMBIGUOUS,
            raw_identifier,
            normalized,
            resolution_status=status,
            candidates=tuple(sorted({group[0].candidate_label for group in by_entity.values()})),
            matched_namespaces=namespaces,
        )

    group = next(iter(by_entity.values()))
    submissions = {m.submission.submission_id: m.submission for m in group if m.submission is not None}
    submission = next(iter(submissions.values())) if len(submissions) == 1 else None
    version = group[0].version

    if version is None:
        return VersionResolution(
            VersionResolutionOutcome.KNOWN_SUBMISSION_NO_VERSION,
            raw_identifier,
            normalized,
            resolution_status="known_submission_no_version",
            matched_namespaces=namespaces,
            submission_external_key=submission.external_submission_key if submission else None,
            submission_status=submission.status if submission else None,
        )

    if len(namespaces) > 1:
        status = "converged_match"
    elif namespaces[0].startswith("submission"):
        status = "submission_match"
    else:
        status = single_status
    return VersionResolution(
        outcome=VersionResolutionOutcome.RESOLVED,
        raw_identifier=raw_identifier,
        normalized_identifier=normalized,
        canonical_version_id=version.external_version_key,
        version_label=version.version_label,
        internal_version_id=version.version_id,
        resolution_status=status,
        matched_namespaces=namespaces,
        submission_external_key=submission.external_submission_key if submission else None,
        submission_status=submission.status if submission else None,
    )


def resolve_version_identifier(hub: "QCH", raw_identifier: str, logical_circuit_id: str | None = None) -> VersionResolution:
    """The deterministic resolver. Never raises for a bad/unknown
    identifier -- always returns a `VersionResolution` so callers can
    branch on `.outcome` rather than catching exceptions.

    Precedence (Phase 2D.3): Tier 1 matches COMPLETE identifiers in
    every namespace (version_id, external_version_key, version_label,
    full submission external key, full submission UUID). Only if Tier 1
    finds nothing does Tier 2 match PREFIXES, again in every
    prefix-capable namespace (commit suffix of external_version_key,
    submission UUID). Within a tier, matches that name distinct
    entities are AMBIGUOUS; matches converging on one entity resolve."""
    if not isinstance(raw_identifier, str) or not raw_identifier.strip():
        return VersionResolution(
            VersionResolutionOutcome.UNKNOWN,
            raw_identifier if isinstance(raw_identifier, str) else "",
            "",
            resolution_status="empty",
        )

    stripped = raw_identifier.strip()
    normalized = _normalize(stripped)

    exact, exact_status = _exact_matches(hub, stripped, normalized, logical_circuit_id)
    if exact:
        return _decide(raw_identifier, normalized, exact, exact_status)

    prefix = _prefix_matches(hub, normalized, logical_circuit_id)
    if prefix:
        return _decide(raw_identifier, normalized, prefix, "short_id_match")

    return VersionResolution(VersionResolutionOutcome.UNKNOWN, raw_identifier, normalized, resolution_status="unknown")


# -- Phase 2D.3.1: entity equivalence (for SemanticGuard) --------------------


class EntityMatch(str, Enum):
    """Three-state answer to "do these two strings name the same
    CircuitVersion?" -- never collapsed into a guessed boolean."""

    SAME_ENTITY = "same_entity"
    DIFFERENT_ENTITY = "different_entity"
    UNRESOLVED_OR_AMBIGUOUS = "unresolved_or_ambiguous"


class VersionIdentityContext:
    """The deterministic identity facts for ONE request, handed to
    `SemanticGuard` so a semantic rule can apply to an ENTITY rather
    than to one spelling of it (Phase 2D.3.1). Seeded with the
    resolutions the service already computed for the question's own
    tokens (reused, not recomputed); any other string -- typically the
    plan's version parameter -- is resolved on demand with the SAME
    `resolve_version_identifier` and memoized. Adds no resolution
    semantics of its own: two strings are the same entity only when
    BOTH resolve (RESOLVED) to the same CircuitVersion. AMBIGUOUS,
    UNKNOWN and KNOWN_SUBMISSION_NO_VERSION never establish identity."""

    def __init__(self, hub: "QCH", question_resolutions: "list[VersionResolution] | tuple[VersionResolution, ...]" = (), logical_circuit_id: str | None = None) -> None:
        self._hub = hub
        self._logical_circuit_id = logical_circuit_id
        self.question_resolutions: tuple[VersionResolution, ...] = tuple(question_resolutions)
        self._cache: dict[str, VersionResolution] = {r.raw_identifier: r for r in self.question_resolutions}

    def resolve(self, identifier: str) -> VersionResolution:
        if identifier not in self._cache:
            self._cache[identifier] = resolve_version_identifier(self._hub, identifier, self._logical_circuit_id)
        return self._cache[identifier]

    def same_version_entity(self, a: str, b: str) -> EntityMatch:
        ra, rb = self.resolve(a), self.resolve(b)
        if ra.outcome != VersionResolutionOutcome.RESOLVED or rb.outcome != VersionResolutionOutcome.RESOLVED:
            return EntityMatch.UNRESOLVED_OR_AMBIGUOUS
        if ra.internal_version_id is not None and ra.internal_version_id == rb.internal_version_id:
            return EntityMatch.SAME_ENTITY
        return EntityMatch.DIFFERENT_ENTITY

    def question_aliases_for(self, plan_identifier: str) -> list[VersionResolution]:
        """The question's own identifier tokens that are provably the
        SAME CircuitVersion as `plan_identifier`, other than the
        identical spelling (the literal case needs no identity)."""
        return [
            r
            for r in self.question_resolutions
            if r.raw_identifier != plan_identifier and self.same_version_entity(r.raw_identifier, plan_identifier) == EntityMatch.SAME_ENTITY
        ]


# -- candidate-token extraction (for LLM grounding context) ------------------

_CANDIDATE_TOKEN_RE = re.compile(
    # Phase 2D.3: a full submission UUID, optionally as its stored
    # "ecdsafail:<uuid>" external key -- listed FIRST so it is taken
    # whole rather than split at its hyphens by the bare-hex branch.
    r"\b(?:ecdsafail:)?[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
    r"|\becdsafail:[0-9a-fA-F]{4,40}\b"           # a full canonical key
    r"|\b(?=[0-9a-fA-F]*[a-fA-F])[0-9a-fA-F]{4,40}\b"  # a bare hex-shaped token (has >=1 letter)
    r"|\bV\d+\b"                                 # a milestone-label-shaped token
)


def extract_candidate_version_identifiers(question: str) -> list[str]:
    """Cheap, deterministic candidate-token extraction from raw question
    text. This is NOT a claim that any token found is a real version --
    only a shortlist a caller (see `QCHNaturalLanguageService`) then
    feeds through `resolve_version_identifier` to find which ones (if
    any) are REAL, so a real identifier can be surfaced to the planner
    as grounding context instead of the model having to somehow already
    know it (this is the fix for the previously-observed false
    UNKNOWN_ENTITY problem)."""
    seen: list[str] = []
    for match in _CANDIDATE_TOKEN_RE.finditer(question):
        token = match.group(0)
        if token not in seen:
            seen.append(token)
    return seen


# -- plan canonicalization (the pre-execution boundary) -----------------------


class PlanCanonicalizationOutcome(str, Enum):
    OK = "ok"
    UNKNOWN_ENTITY = "unknown"
    AMBIGUOUS = "ambiguous"
    # Phase 2D.3: the plan names a real Submission with no
    # CircuitVersion -- execution stops here; nothing is sent to the
    # executor for it.
    KNOWN_SUBMISSION_NO_VERSION = "known_submission_no_version"


@dataclass(frozen=True)
class PlanCanonicalizationResult:
    outcome: PlanCanonicalizationOutcome
    plan: QCHQueryPlan | None
    resolutions: tuple[VersionResolution, ...] = field(default_factory=tuple)
    offending: VersionResolution | None = None


_VERSION_VALUE_PARAMS = ("version", "version_a", "version_b")
_IDENTITY_METRIC_NAMES = ("version_id", "external_version_key", "version_label")
_IDENTITY_EQUALITY_OPERATORS = ("==", "!=")


def canonicalize_plan_versions(hub: "QCH", plan: QCHQueryPlan) -> PlanCanonicalizationResult:
    """The ONE boundary (spec section 7) where every version reference
    in an already-validated, already-SemanticGuard-checked plan is
    rewritten to its real canonical `external_version_key` before
    execution. Runs AFTER `SemanticGuard.check()` -- which still needs
    the plan's ORIGINAL, question-literal version text for its own
    text-matching rule (`qch.nl.semantic_guard.
    expected_branch_traversal_direction` regex-searches the question
    for the plan's literal `version` param) -- and BEFORE
    `QCHQueryExecutor`. Touches neither `SemanticGuard` nor any query
    operator; it only rewrites step params using facts already
    computed by `resolve_version_identifier`, then relies on the
    existing validator (via the caller's `_revalidate`) to confirm the
    rewritten plan is still schema-legal.

    `conditions`-based identity lookups (`filter`/`filter_versions`
    conditions naming `version_id`/`external_version_key`/
    `version_label` with an equality operator) are normalized onto
    `external_version_key` uniformly, since that is always a real
    version's one stable identity regardless of which identity field
    the original condition happened to name."""
    resolutions: list[VersionResolution] = []
    new_steps: list[QCHQueryStep] = []
    changed = False

    for step in plan.steps:
        params = dict(step.params)

        # Phase 2D.4: describe_entity is the one operator that can describe
        # a Submission WITHOUT a CircuitVersion, so for it (and only it)
        # KNOWN_SUBMISSION_NO_VERSION is not a stop: the reference becomes
        # `submission=<exact external key>`, never a fabricated version.
        # A `submission` reference whose submission HAS a version becomes
        # `version=<canonical key>`. Resolution itself is unchanged.
        if step.operator == "describe_entity":
            for param_name in ("version", "submission"):
                raw_value = params.get(param_name)
                if not isinstance(raw_value, str):
                    continue
                resolution = resolve_version_identifier(hub, raw_value, plan.logical_circuit_id)
                resolutions.append(resolution)
                if resolution.outcome == VersionResolutionOutcome.RESOLVED:
                    target = {"version": resolution.canonical_version_id}
                elif resolution.outcome == VersionResolutionOutcome.KNOWN_SUBMISSION_NO_VERSION:
                    target = {"submission": resolution.submission_external_key}
                elif (
                    # Phase 2D.6: a reference that is no version/submission at all but
                    # that QCH's deterministic ContributorResolver resolves to exactly
                    # one contributor identity describes that contributor.
                    resolution.outcome == VersionResolutionOutcome.UNKNOWN
                    and hub.contributors.resolve(raw_value).outcome.value == "resolved"
                ):
                    target = {"contributor": raw_value}
                else:
                    return PlanCanonicalizationResult(
                        outcome=PlanCanonicalizationOutcome(resolution.outcome.value),
                        plan=None,
                        resolutions=tuple(resolutions),
                        offending=resolution,
                    )
                rewritten = {k: v for k, v in params.items() if k not in ("version", "submission")}
                rewritten.update(target)
                if rewritten != params:
                    params = rewritten
                    changed = True
                break
            new_steps.append(QCHQueryStep(step.operator, params) if changed else step)
            continue

        # Phase 2D.5: a step asking for SUBMISSION-scoped metrics
        # (evaluation.*, platform.*) can be answered for a Submission that
        # has no CircuitVersion, so for it KNOWN_SUBMISSION_NO_VERSION
        # becomes the exact submission external key (never a fake version
        # key). Scope comes from the metric namespace, not from guessing.
        requested_metrics = [params.get("metric"), *(params.get("metrics") or [])]
        submission_scope_ok = (step.operator in ("get_metric", "compare_versions") and any(
            isinstance(m, str) and m.startswith(("evaluation.", "platform.")) for m in requested_metrics
        )) or step.operator == "list_contributors"  # Phase 2D.6: contributors belong to the Submission

        for param_name in _VERSION_VALUE_PARAMS:
            raw_value = params.get(param_name)
            if not isinstance(raw_value, str):
                continue
            resolution = resolve_version_identifier(hub, raw_value, plan.logical_circuit_id)
            resolutions.append(resolution)
            if submission_scope_ok and resolution.outcome == VersionResolutionOutcome.KNOWN_SUBMISSION_NO_VERSION and resolution.submission_external_key:
                if params[param_name] != resolution.submission_external_key:
                    params[param_name] = resolution.submission_external_key
                    changed = True
                continue
            if resolution.outcome != VersionResolutionOutcome.RESOLVED:
                return PlanCanonicalizationResult(
                    outcome=PlanCanonicalizationOutcome(resolution.outcome.value),
                    plan=None,
                    resolutions=tuple(resolutions),
                    offending=resolution,
                )
            if resolution.canonical_version_id != raw_value:
                params[param_name] = resolution.canonical_version_id
                changed = True

        conditions = params.get("conditions")
        if isinstance(conditions, list):
            new_conditions = []
            conditions_changed = False
            for condition in conditions:
                if not isinstance(condition, dict):
                    new_conditions.append(condition)
                    continue
                metric = condition.get("metric")
                operator = condition.get("operator")
                value = condition.get("value")
                if metric in _IDENTITY_METRIC_NAMES and operator in _IDENTITY_EQUALITY_OPERATORS and isinstance(value, str):
                    resolution = resolve_version_identifier(hub, value, plan.logical_circuit_id)
                    resolutions.append(resolution)
                    if resolution.outcome != VersionResolutionOutcome.RESOLVED:
                        return PlanCanonicalizationResult(
                            outcome=PlanCanonicalizationOutcome(resolution.outcome.value),
                            plan=None,
                            resolutions=tuple(resolutions),
                            offending=resolution,
                        )
                    if metric != "external_version_key" or value != resolution.canonical_version_id:
                        conditions_changed = True
                    new_conditions.append({**condition, "metric": "external_version_key", "value": resolution.canonical_version_id})
                else:
                    new_conditions.append(condition)
            if conditions_changed:
                params["conditions"] = new_conditions
                changed = True

        new_steps.append(QCHQueryStep(step.operator, params) if changed else step)

    if not changed:
        return PlanCanonicalizationResult(outcome=PlanCanonicalizationOutcome.OK, plan=plan, resolutions=tuple(resolutions))

    new_plan = QCHQueryPlan(steps=tuple(new_steps), logical_circuit_id=plan.logical_circuit_id)
    return PlanCanonicalizationResult(outcome=PlanCanonicalizationOutcome.OK, plan=new_plan, resolutions=tuple(resolutions))


# -- Phase 2D.7.1: cue-gated ALL-DIGIT identifier prefixes -------------------
#
# The frozen rule above (a bare token needs >= 1 hex letter) keeps plain
# numbers -- thresholds, limits -- from ever being identifiers, and it stays
# unchanged. An all-digit token becomes an identifier candidate ONLY after
# an explicit entity cue, and is then searched in exactly the namespace the
# cue names:
#     "submission [ID|UUID|prefix] <digits>"          -> submission UUID prefixes
#     "version|circuit [version] [ID] <digits>"       -> version commit-suffix prefixes
# The token is kept as a STRING (leading zeros are identity-significant),
# must have >= _MIN_SHORT_ID_LEN digits (the existing minimum; the collision
# analysis in docs/QCH_METRIC_GROUNDING_NUMERIC_PREFIX_PHASE2D7_1.md shows the
# unique-match rule below handles the rare short collisions), and resolves
# only on a UNIQUE entity match -- 0 -> UNKNOWN, >1 -> AMBIGUOUS, never a pick.

NUMERIC_NAMESPACE_SUBMISSION = "submission"
NUMERIC_NAMESPACE_VERSION = "version"
NUMERIC_NAMESPACE_ANY = "any"  # both namespaces; distinct entities -> AMBIGUOUS (used for comparison slots)
_NUMERIC_CUE_RE = re.compile(
    r"\b(?P<cue>submission|circuit\s+version|version|circuit)(?:\s+(?:id|uuid|prefix|key))?\s*[:#]?\s*"
    r"(?P<token>\d{%d,40})\b(?![.,]\d)" % _MIN_SHORT_ID_LEN,
    re.IGNORECASE,
)


@dataclass(frozen=True)
class NumericIdentifierCandidate:
    token: str
    cue: str
    namespace: str


def extract_cued_numeric_identifiers(question: str) -> list[NumericIdentifierCandidate]:
    found: list[NumericIdentifierCandidate] = []
    for m in _NUMERIC_CUE_RE.finditer(question or ""):
        cue = re.sub(r"\s+", " ", m.group("cue").lower())
        namespace = NUMERIC_NAMESPACE_SUBMISSION if cue == "submission" else NUMERIC_NAMESPACE_VERSION
        candidate = NumericIdentifierCandidate(m.group("token"), cue, namespace)
        if candidate not in found:
            found.append(candidate)
    return found


def resolve_numeric_identifier(hub: "QCH", token: str, namespace: str, logical_circuit_id: str | None = None) -> VersionResolution:
    """Unique-match resolution of an all-digit prefix in ONE namespace (or
    both, for NUMERIC_NAMESPACE_ANY). Never parses the token as a number."""
    if not isinstance(token, str) or not token.isdigit() or len(token) < _MIN_SHORT_ID_LEN:
        return VersionResolution(VersionResolutionOutcome.UNKNOWN, str(token), str(token), resolution_status="not_a_numeric_prefix")
    matches: list[_Match] = []
    if namespace in (NUMERIC_NAMESPACE_SUBMISSION, NUMERIC_NAMESPACE_ANY):
        index = _SubmissionIndex(hub, logical_circuit_id)
        for submission in index.submissions:
            uuid = _submission_uuid_of(submission)
            if uuid.startswith(token):
                matches.extend(_submission_matches(index, submission, IdentityNamespace.SUBMISSION_UUID_PREFIX))
    if namespace in (NUMERIC_NAMESPACE_VERSION, NUMERIC_NAMESPACE_ANY):
        for candidate in _all_versions(hub, logical_circuit_id):
            key = (candidate.external_version_key or "").lower()
            suffix = key.split(":", 1)[1] if ":" in key else key
            if suffix.startswith(token):
                matches.append(_Match(IdentityNamespace.COMMIT_PREFIX, candidate))
    if not matches:
        return VersionResolution(VersionResolutionOutcome.UNKNOWN, token, token, resolution_status=f"numeric_prefix_unknown_{namespace}")
    return _decide(token, token, matches, f"numeric_prefix_{namespace}")
