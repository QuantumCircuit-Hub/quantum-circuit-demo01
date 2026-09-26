"""Identifier resolution helpers shared by every query operator.

Real QCH data has three independent ways to name a CircuitVersion (see
qch.models.CircuitVersion): the opaque `version_id`, the
dataset-native `external_version_key` (e.g. "ecdsafail:6f7c159"), and
`version_label` (e.g. "V1" -- only ever set for the five frozen
ECDSA.Fail milestones; the other ~776 ECDSAFailHistoryImporter versions
have none). A user's natural-language question will name a version by
whichever of these it happens to know, so `resolve_version` tries all
three rather than assuming one -- an illustrative identifier like
"V120" (used in this phase's own example queries) simply resolves to
"not found", which is the honest, correct answer: no such label exists
in real data.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from qch.exceptions import NotFoundError
from qch.models import CircuitVersion, LogicalCircuit

if TYPE_CHECKING:
    from qch.hub import QCH


def resolve_logical_circuit_id(hub: "QCH", explicit: str | None) -> tuple[str | None, list[LogicalCircuit]]:
    """Returns (logical_circuit_id, all_known_circuits). If `explicit`
    is given it is returned as-is (existence is checked by the caller
    when it actually looks up versions). If not given: the sole known
    circuit is picked automatically; with zero or more than one known
    circuit, None is returned so the caller can report MISSING_DATA or
    AMBIGUOUS respectively, rather than guessing."""
    circuits = hub.circuits.list()
    if explicit is not None:
        return explicit, circuits
    if len(circuits) == 1:
        return circuits[0].logical_circuit_id, circuits
    return None, circuits


def resolve_version(hub: "QCH", identifier: str, logical_circuit_id: str | None) -> CircuitVersion | None:
    """Tries, in order: exact version_id, external_version_key, then
    version_label (the latter two scoped to `logical_circuit_id` when
    given, else searched across every known circuit). Returns None
    -- never raises -- if nothing matches, so callers can produce a
    QCHQueryStatus.MISSING_DATA result instead of an exception."""
    try:
        return hub.versions.get(identifier)
    except NotFoundError:
        pass

    by_external_key = hub.versions.find_by_external_key(identifier)
    if by_external_key is not None:
        return by_external_key

    circuit_ids: list[str]
    if logical_circuit_id is not None:
        circuit_ids = [logical_circuit_id]
    else:
        circuit_ids = [c.logical_circuit_id for c in hub.circuits.list()]

    for circuit_id in circuit_ids:
        for version in hub.versions.list(circuit_id):
            if version.version_label == identifier:
                return version
    return None
