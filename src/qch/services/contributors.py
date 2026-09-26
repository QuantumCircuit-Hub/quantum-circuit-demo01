"""hub.contributors -- contributor provenance (QCH Phase 2D.6).

Principle: MODEL PROVENANCE IDENTITIES, NOT INFERRED PEOPLE.

A ContributorIdentity is an identity a source asserts (for ECDSA.Fail: a
platform account, keyed by the API's `solverAccountId`). It is never a
real-world person: no real name is stored, and nothing here maps a
person's name onto an account. A Contribution links an identity (or a
verbatim declared reference) to a Submission with a role and a
categorical evidence class. See docs/QCH_CONTRIBUTOR_PROVENANCE_PHASE2D6.md.

Resolution is deterministic and exact (`ContributorResolver`): stable
source key, current handle, case-folded handle, proven historical alias,
or an explicitly namespaced stable secondary id. No fuzzy matching, no
string similarity, no LLM, no hidden alias dictionary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from qch.exceptions import ValidationError
from qch.models import (
    CONTRIBUTION_ROLES,
    CONTRIBUTOR_ALIAS_NAMESPACES,
    CONTRIBUTOR_IDENTITY_TYPES,
    EVIDENCE_CLASSES,
    Contribution,
    ContributorAlias,
    ContributorIdentity,
    ContributorImport,
)
from qch.repositories.interfaces import Storage


def normalize_handle(value: str) -> str:
    """The ONLY normalization used for handle lookup: case-folding
    (GitHub logins are case-insensitive). No punctuation stripping, no
    space removal, no transliteration -- those would be fuzzy matching."""
    return value.casefold()


class ContributorResolutionOutcome(str, Enum):
    RESOLVED = "resolved"
    UNKNOWN = "unknown"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class ContributorResolution:
    reference: str
    outcome: ContributorResolutionOutcome
    identity: ContributorIdentity | None = None
    matched_via: str | None = None  # which deterministic rule matched
    candidates: tuple[ContributorIdentity, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "reference": self.reference,
            "outcome": self.outcome.value,
            "contributor_identity_id": self.identity.contributor_identity_id if self.identity else None,
            "current_handle": self.identity.current_handle if self.identity else None,
            "matched_via": self.matched_via,
            "candidates": [c.current_handle or c.contributor_identity_id for c in self.candidates],
        }


class ContributorResolver:
    """Deterministic contributor lookup, tried in this order (the first
    rule with any match decides; >1 distinct identity at that rule ->
    AMBIGUOUS, never a pick):

      1. exact stable key (`source_identity_key`, or QCH's own
         `contributor_identity_id`)
      2. exact current handle
      3. case-folded current handle
      4. case-folded proven alias handle (historical or current)
      5. `github_user_id:<digits>` -- an explicitly namespaced stable
         secondary id (a bare number is never guessed to be one)

    A single leading "@" (mention syntax) is stripped. Anything else --
    a real name, a partial string, a similar-looking handle -- is
    UNKNOWN."""

    SECONDARY_ID_PREFIX = "github_user_id:"

    def __init__(self, storage: Storage) -> None:
        self._storage = storage

    def resolve(self, reference: str) -> ContributorResolution:
        raw = reference if isinstance(reference, str) else ""
        ref = raw.strip()
        if ref.startswith("@"):
            ref = ref[1:]
        if not ref:
            return ContributorResolution(raw, ContributorResolutionOutcome.UNKNOWN)
        identities = self._storage.list_contributor_identities()
        folded = normalize_handle(ref)

        def decide(matches: list[ContributorIdentity], rule: str) -> ContributorResolution | None:
            unique = {m.contributor_identity_id: m for m in matches}
            if not unique:
                return None
            if len(unique) == 1:
                return ContributorResolution(raw, ContributorResolutionOutcome.RESOLVED, next(iter(unique.values())), rule)
            return ContributorResolution(raw, ContributorResolutionOutcome.AMBIGUOUS, None, rule, tuple(unique.values()))

        by_id = {i.contributor_identity_id: i for i in identities}
        rules: list[tuple[str, Any]] = [
            ("source_identity_key", lambda: [i for i in identities if ref in (i.source_identity_key, i.contributor_identity_id)]),
            ("current_handle", lambda: [i for i in identities if i.current_handle == ref]),
            ("current_handle_casefold", lambda: [i for i in identities if i.current_handle is not None and normalize_handle(i.current_handle) == folded]),
            ("alias_handle", lambda: [by_id[a.contributor_identity_id] for a in self._storage.find_contributor_aliases("handle", folded) if a.contributor_identity_id in by_id]),
        ]
        if ref.startswith(self.SECONDARY_ID_PREFIX) and ref[len(self.SECONDARY_ID_PREFIX):].isdigit():
            number = ref[len(self.SECONDARY_ID_PREFIX):]
            rules.append(("github_user_id", lambda: [by_id[a.contributor_identity_id] for a in self._storage.find_contributor_aliases("github_user_id", number) if a.contributor_identity_id in by_id]))
        for rule, matcher in rules:
            decided = decide(matcher(), rule)
            if decided is not None:
                return decided
        return ContributorResolution(raw, ContributorResolutionOutcome.UNKNOWN)


class ContributorsService:
    def __init__(self, storage: Storage) -> None:
        self._storage = storage
        self.resolver = ContributorResolver(storage)

    # -- writes ----------------------------------------------------------------

    def record_import(
        self,
        record: ContributorImport,
        identities: list[ContributorIdentity],
        aliases: list[ContributorAlias],
        contributions: list[Contribution],
    ) -> bool:
        """Atomically stores one snapshot's contributor facts. Returns False
        (changing nothing) if that snapshot's contributors were already
        imported. Validates the domain rules the schema also enforces,
        before any write."""
        known = {i.contributor_identity_id for i in identities}
        for i in identities:
            if i.identity_type not in CONTRIBUTOR_IDENTITY_TYPES:
                raise ValidationError(f"unknown identity_type {i.identity_type!r}")
            if not i.source_identity_key:
                raise ValidationError("a contributor identity needs a source_identity_key")
        for a in aliases:
            self._validate_alias(a)
        submitters: set[str] = set()
        for c in contributions:
            if c.snapshot_id != record.snapshot_id:
                raise ValidationError(f"contribution {c.contribution_id} belongs to a different snapshot")
            if c.role not in CONTRIBUTION_ROLES:
                raise ValidationError(f"unknown contribution role {c.role!r}")
            if c.evidence_class not in EVIDENCE_CLASSES:
                raise ValidationError(f"unknown evidence class {c.evidence_class!r}")
            if c.contributor_identity_id is None and not c.declared_reference:
                raise ValidationError(f"contribution {c.contribution_id} has neither an identity nor a declared reference")
            if c.contributor_identity_id is not None and c.contributor_identity_id not in known and self._storage.get_contributor_identity(c.contributor_identity_id) is None:
                raise ValidationError(f"contribution {c.contribution_id} references an unknown identity")
            if c.role == "SUBMITTER":
                if c.contributor_identity_id is None or c.evidence_class != "AUTHORITATIVE":
                    raise ValidationError("a SUBMITTER contribution must be an AUTHORITATIVE link to an identity")
                if c.submission_id in submitters:
                    raise ValidationError(f"submission {c.submission_id} has two submitters in one snapshot")
                submitters.add(c.submission_id)
        return self._storage.record_contributor_import(record, identities, aliases, contributions)

    def add_aliases(self, aliases: list[ContributorAlias]) -> int:
        """Adds proven aliases (never overwrites an existing one)."""
        for a in aliases:
            self._validate_alias(a)
            if self._storage.get_contributor_identity(a.contributor_identity_id) is None:
                raise ValidationError(f"alias {a.alias_id} references an unknown identity")
        return self._storage.upsert_contributor_aliases(aliases)

    @staticmethod
    def _validate_alias(a: ContributorAlias) -> None:
        if a.namespace not in CONTRIBUTOR_ALIAS_NAMESPACES:
            raise ValidationError(f"unknown alias namespace {a.namespace!r}")
        if a.evidence_class not in EVIDENCE_CLASSES:
            raise ValidationError(f"unknown evidence class {a.evidence_class!r}")
        if a.namespace == "handle" and a.value_normalized != normalize_handle(a.value):
            raise ValidationError(f"alias {a.value!r}: value_normalized must be its case-folded form")
        if a.namespace == "github_user_id" and not a.value.isdigit():
            raise ValidationError(f"github_user_id alias must be digits only, got {a.value!r}")

    # -- reads ------------------------------------------------------------------

    def resolve(self, reference: str) -> ContributorResolution:
        return self.resolver.resolve(reference)

    def get(self, contributor_identity_id: str) -> ContributorIdentity | None:
        return self._storage.get_contributor_identity(contributor_identity_id)

    def list(self) -> list[ContributorIdentity]:
        return self._storage.list_contributor_identities()

    def aliases(self, contributor_identity_id: str | None = None) -> list[ContributorAlias]:
        return self._storage.list_contributor_aliases(contributor_identity_id)

    def get_import(self, snapshot_id: str) -> ContributorImport | None:
        return self._storage.get_contributor_import(snapshot_id)

    def list_imports(self) -> list[ContributorImport]:
        return self._storage.list_contributor_imports()

    def latest_contributions(self) -> dict[str, list[Contribution]]:
        """submission_id -> contributions from the latest contributor-imported
        snapshot containing that submission (one query)."""
        return self._storage.list_latest_contributions()

    def contributions_for_submission(self, submission_id: str) -> list[Contribution]:
        return self.latest_contributions().get(submission_id, [])
