"""Loads docs/qch_query_schema.json into a structured, queryable
grounding contract for the NL planner -- the SAME file that is (per
that file's own "purpose" field) already meant to be the contract
between a future planner and qch.query.QCHQueryExecutor. This module
adds no new source of truth; it only parses that JSON into typed,
easy-to-query Python objects.

Every canonical-name/alias/operator/relation-type check the planner or
validator does goes through this module, so the schema JSON stays the
single place those facts are edited. If qch.query.operators.OPERATORS
or the metric set ever changes without this file being regenerated,
`schema_context.audit_against_live_registry()` (used by a test, not by
the planner itself) will say so rather than silently drifting.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_DEFAULT_SCHEMA_PATH = Path(__file__).resolve().parents[3] / "docs" / "qch_query_schema.json"

# Generic English "noise" words stripped when turning a canonical metric
# name/alias into distinctive keywords for NL matching (see
# `candidate_metrics_for_tokens`). Deliberately small and generic --
# never a dataset-specific word list (no "toffoli", "ecdsa", etc. here).
_GENERIC_METRIC_WORDS = {"count", "number", "of", "total"}


@dataclass(frozen=True)
class OperatorSpec:
    name: str
    kind: str
    params: dict[str, Any]
    returns: str
    description: str


@dataclass(frozen=True)
class MetricSpec:
    canonical_name: str
    definition: str
    unit: str | None
    source: str
    availability: str
    aliases: tuple[str, ...]
    comparable_across: str | None
    # QCH Phase 2D.5: optional, schema-declared NL grounding (see the
    # metric's "nl_grounding" entry). When present it REPLACES the
    # name-derived concept/qualifier heuristics for this metric only;
    # `nl_requires_qualifier` makes the metric join a concept group only
    # when one of its own qualifier words is in the question, so adding
    # it never changes how a bare phrase ("score", "Toffoli") grounds.
    nl_concept: tuple[str, ...] | None = None
    nl_qualifiers: tuple[str, ...] | None = None
    nl_requires_qualifier: bool = False
    entity_scope: str = "version"

    def keywords(self) -> frozenset[str]:
        """Distinctive lowercase tokens derived from the canonical name
        and its aliases, generic noise words removed -- used only for
        NL candidate-matching (see candidate_metrics_for_tokens), never
        for validation (validation only ever checks exact canonical
        names/aliases)."""
        tokens: set[str] = set()
        for text in (self.canonical_name, *self.aliases):
            for part in re.split(r"[._\s]+", text.lower()):
                if part and part not in _GENERIC_METRIC_WORDS:
                    tokens.add(part)
        return frozenset(tokens)

    def concept_keywords(self) -> frozenset[str]:
        """Keywords from ONLY the canonical name's final segment (after
        the last '.'), never aliases -- e.g. both 'toffoli_count' and
        'structural.toffoli_count' share concept_keywords == {'toffoli'}.
        Two metrics sharing a concept are "the same underlying idea,
        named ambiguously" and must be disambiguated by qualifier_tokens
        before either can be resolved from a bare phrase like "Toffoli
        count" -- see candidate_metrics_for_tokens."""
        if self.nl_concept is not None:
            return frozenset(self.nl_concept)
        name_part = self.canonical_name.rsplit(".", 1)[-1]
        return frozenset(part for part in re.split(r"[._\s]+", name_part.lower()) if part and part not in _GENERIC_METRIC_WORDS)

    def is_actually_resolvable(self) -> bool:
        """False for a documented-but-not-really-implemented canonical
        name -- either explicitly marked UNAVAILABLE (e.g. 'gate_count',
        'depth': deliberately not defined/computed by anything, see
        their own schema entries) or a "CORRECTION_from_code_audit_
        phase_2A" entry showing the alias table never actually resolves
        it (e.g. 'classical_bit_count'). Both kinds are kept as VISIBLE
        schema entries (documentation/audit value), but must never
        participate in NL concept-matching or metric validation as if
        they were real, queryable names -- a real bug this check fixes:
        without it, "Toffoli GATES" would spuriously match the never-
        implemented 'gate_count' entry via its bare 'gate' keyword."""
        return self.source != "UNAVAILABLE" and "NOT AN ALIAS AT ALL" not in self.source

    def qualifier_tokens(self) -> frozenset[str]:
        """Tokens that specifically distinguish this metric from others
        sharing the same concept: its canonical-name PREFIX (the part
        before the first '.', e.g. 'structural'), plus any of a small,
        generic set of provenance-marker words that literally appear in
        THIS metric's own `source`/`definition` text. Schema-driven, not
        hardcoded per dataset -- adding a new qualified metric variant to
        the JSON (e.g. a future 'simulated.' prefix) participates
        automatically."""
        if self.nl_qualifiers is not None:
            return frozenset(self.nl_qualifiers)
        tokens: set[str] = set()
        if "." in self.canonical_name:
            tokens.add(self.canonical_name.split(".", 1)[0].lower())
        marker_words = {"benchmark", "executed", "stored", "computed", "static"}
        haystack = f"{self.source} {self.definition}".lower()
        for word in marker_words:
            if word in haystack:
                tokens.add(word)
        return frozenset(tokens)


@dataclass(frozen=True)
class RelationTypeSpec:
    name: str
    meaning: str
    source: str


@dataclass(frozen=True)
class SchemaContext:
    schema_version: str
    operators: dict[str, OperatorSpec]
    metrics: dict[str, MetricSpec]  # keyed by canonical_name
    alias_to_canonical: dict[str, str]  # exact alias string -> canonical_name (only unambiguous ones)
    relation_types: dict[str, RelationTypeSpec]
    comparison_operators: tuple[str, ...]
    raw: dict[str, Any] = field(repr=False)
    version_metadata_fields: tuple[str, ...] = ()
    """The schema's own `version_metadata_fields` list (e.g.
    `external_version_key`, `version_label`, ...) -- already used by
    `qch.nl.validator._looks_like_field_reference` to accept these as
    legal filter/sort/get_metric field references, but NOT rendered
    into the LLM prompt until Phase 2D.1 (`qch.nl.prompts`) added it --
    see docs/QCH_VERSION_IDENTITY_PHASE2D1.md."""
    submission_fields: tuple[str, ...] = ()
    """QCH Phase 2D.5: non-metric fields of `list_submissions` records
    (lifecycle_status, platform.status, evaluation.passed, ...) -- legal
    filter/sort field references, like version_metadata_fields."""
    contributor_fields: tuple[str, ...] = ()
    """QCH Phase 2D.6: fields of `list_contributors` records
    (current_handle, submission_count, ...) -- legal filter/sort field
    references, like submission_fields."""

    # -- operators -----------------------------------------------------
    def is_known_operator(self, name: str) -> bool:
        return name in self.operators

    def operator_spec(self, name: str) -> OperatorSpec | None:
        return self.operators.get(name)

    # -- metrics ---------------------------------------------------------
    def is_known_canonical_metric(self, name: str) -> bool:
        return name in self.metrics

    def resolve_exact_metric_name(self, name: str) -> str | None:
        """Resolves `name` to a canonical metric name ONLY if it is
        already the exact canonical name or an exact, unambiguous
        alias string -- never a fuzzy/NL match (see
        candidate_metrics_for_tokens for that). Returns None if unknown
        OR if `name` IS ITSELF a documented-but-not-actually-resolvable
        canonical name (see MetricSpec.is_actually_resolvable) -- a plan
        must never be accepted as valid for directly referencing e.g.
        'gate_count', which the schema itself says is not defined by
        anything. A known ALIAS still resolves even when its OWN listed
        canonical parent is non-resolvable (e.g. 'classical_bits' is a
        real, directly-usable raw StructuralMetric name in its own
        right, even though it is schema-listed as an alias of the
        non-resolvable 'classical_bit_count' -- a known, documented
        schema-modeling quirk; see docs/QCH_NL_PLANNER_PHASE2A.md)."""
        if name in self.metrics:
            return name if self.metrics[name].is_actually_resolvable() else None
        return self.alias_to_canonical.get(name)

    def resolve_metric_concepts(self, tokens: set[str]) -> tuple[list[str], list[list[str]]]:
        """Groups every canonical metric whose concept_keywords are
        present in `tokens` by shared concept (e.g. 'toffoli'), then
        disambiguates each group by qualifier words (e.g. 'structural',
        'benchmark', 'executed') when present -- see
        MetricSpec.concept_keywords/qualifier_tokens.

        Returns `(resolved, ambiguous_groups)`:
        - `resolved` has ONE canonical name per concept mentioned in the
          question that resolved unambiguously (either only one metric
          matches that concept at all, or exactly one qualifying word
          disambiguates among several) -- a question mentioning several
          DIFFERENT, each-unambiguous concepts (e.g. "Toffoli count" AND
          "qubit count", both qualified by "structural") correctly
          yields multiple entries here, not an ambiguity.
        - `ambiguous_groups` has one inner list per concept where
          multiple non-equivalent metrics remain live candidates with no
          disambiguating qualifier (e.g. bare "Toffoli count" alone) --
          the caller must treat ANY non-empty entry here as AMBIGUOUS
          for that concept, never guess.

        Purely schema-driven: adding a new metric to the JSON
        automatically participates, with no planner code change."""
        groups: dict[frozenset[str], list[MetricSpec]] = {}
        for spec in self.metrics.values():
            if not spec.is_actually_resolvable():
                continue
            concept = spec.concept_keywords()
            if spec.nl_requires_qualifier and not (spec.qualifier_tokens() & tokens):
                continue  # opt-in metric: only grounded when explicitly qualified
            if concept and concept <= tokens:
                groups.setdefault(concept, []).append(spec)

        resolved: list[str] = []
        ambiguous_groups: list[list[str]] = []
        for members in groups.values():
            if len(members) == 1:
                resolved.append(members[0].canonical_name)
                continue
            qualified_matches = [m for m in members if m.qualifier_tokens() & tokens]
            if len(qualified_matches) > 1 and any(m.nl_requires_qualifier for m in qualified_matches):
                # Phase 2D.5, ONLY for groups an opt-in metric joined: the
                # member matching strictly the most qualifier words wins
                # (e.g. "average executed Toffoli" -> the official metric,
                # 2 matches, over the benchmark-run one, 1); a tie stays
                # ambiguous. Groups without opt-in members are unaffected.
                scored = sorted(((len(m.qualifier_tokens() & tokens), m.canonical_name, m) for m in qualified_matches), reverse=True)
                if scored[0][0] > scored[1][0]:
                    qualified_matches = [scored[0][2]]
            if len(qualified_matches) == 1:
                resolved.append(qualified_matches[0].canonical_name)
            else:
                # zero or multiple qualifier words matched -- still genuinely ambiguous
                ambiguous_groups.append(sorted(m.canonical_name for m in members))
        return sorted(resolved), ambiguous_groups

    def candidate_metrics_for_tokens(self, tokens: set[str]) -> list[str]:
        """Back-compat convenience for a SINGLE-metric-expecting caller:
        collapses resolve_metric_concepts() into one flat list, where a
        non-empty `ambiguous_groups` (any concept) makes the whole
        result "ambiguous" (i.e. this returns >1 name) rather than
        silently picking the resolved ones only. Prefer
        resolve_metric_concepts() directly for a multi-metric question."""
        resolved, ambiguous_groups = self.resolve_metric_concepts(tokens)
        if ambiguous_groups:
            # Flatten: report every candidate from every ambiguous concept, so a
            # caller checking len() > 1 still correctly detects ambiguity, and a
            # caller inspecting the names still sees genuine candidates, not resolved ones.
            flattened: set[str] = set()
            for group in ambiguous_groups:
                flattened.update(group)
            return sorted(flattened)
        return resolved

    # -- relation types ---------------------------------------------------
    def is_known_relation_type(self, name: str) -> bool:
        return name in self.relation_types

    # -- comparators -----------------------------------------------------
    def is_known_comparator(self, op: str) -> bool:
        return op in self.comparison_operators


def load_schema_context(path: str | Path | None = None) -> SchemaContext:
    schema_path = Path(path) if path is not None else _DEFAULT_SCHEMA_PATH
    raw = json.loads(schema_path.read_text(encoding="utf-8"))

    operators = {
        o["name"]: OperatorSpec(name=o["name"], kind=o["kind"], params=o.get("params", {}), returns=o.get("returns", ""), description=o.get("description", ""))
        for o in raw.get("operators", [])
    }

    metrics: dict[str, MetricSpec] = {}
    alias_to_canonical: dict[str, str] = {}
    alias_seen_from: dict[str, set[str]] = {}
    for m in raw.get("metrics", []):
        canonical = m["canonical_name"]
        aliases = tuple(m.get("aliases", []))
        grounding = m.get("nl_grounding") or {}
        metrics[canonical] = MetricSpec(
            canonical_name=canonical,
            definition=m.get("definition", ""),
            unit=m.get("unit"),
            source=m.get("source", ""),
            availability=m.get("availability", ""),
            aliases=aliases,
            comparable_across=m.get("comparable_across"),
            nl_concept=tuple(grounding["concept"]) if "concept" in grounding else None,
            nl_qualifiers=tuple(grounding["qualifiers"]) if "qualifiers" in grounding else None,
            nl_requires_qualifier=bool(grounding.get("requires_qualifier", False)),
            entity_scope=m.get("entity_scope", "version"),
        )
        for alias in aliases:
            alias_seen_from.setdefault(alias, set()).add(canonical)

    # An alias string is only usable for exact resolution if it maps to
    # exactly one canonical metric -- a collision (unlikely given the
    # real schema today, but checked defensively) must never be
    # silently resolved to either candidate.
    for alias, canonicals in alias_seen_from.items():
        if len(canonicals) == 1:
            alias_to_canonical[alias] = next(iter(canonicals))

    relation_types = {r["name"]: RelationTypeSpec(name=r["name"], meaning=r.get("meaning", ""), source=r.get("source", "")) for r in raw.get("relation_types", [])}

    comparison_operators = tuple(raw.get("comparison_operators", []))

    return SchemaContext(
        schema_version=raw.get("schema_version", "unknown"),
        operators=operators,
        metrics=metrics,
        alias_to_canonical=alias_to_canonical,
        relation_types=relation_types,
        comparison_operators=comparison_operators,
        raw=raw,
        version_metadata_fields=tuple(raw.get("version_metadata_fields", [])),
        submission_fields=tuple(raw.get("submission_fields", [])),
        contributor_fields=tuple(raw.get("contributor_fields", [])),
    )


def audit_against_live_registry() -> list[str]:
    """Cross-checks the schema JSON's operator list against the REAL,
    live qch.query.operators.OPERATORS registry -- used by a test to
    catch schema drift (an operator added to the registry but never
    documented, or vice versa), never called by the planner itself at
    runtime. Returns a list of human-readable discrepancies (empty if
    none)."""
    from qch.query.operators import OPERATORS

    schema = load_schema_context()
    problems = []
    documented = set(schema.operators)
    live = set(OPERATORS)
    for missing_from_schema in sorted(live - documented):
        problems.append(f"operator {missing_from_schema!r} exists in qch.query.operators.OPERATORS but is not documented in qch_query_schema.json")
    for missing_from_registry in sorted(documented - live):
        problems.append(f"operator {missing_from_registry!r} is documented in qch_query_schema.json but does not exist in qch.query.operators.OPERATORS")
    return problems
